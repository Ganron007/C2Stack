"""Crypto primitives for the Meridian server.

Implements the wire-protocol key exchange and AEAD envelope described in
docs/protocol.md:

    shared = X25519(server_priv, client_pub)
    key    = HKDF-SHA256(ikm=shared, salt=client_nonce||server_nonce, info="meridian-v1")
    ct     = AES-256-GCM(key, nonce, aad="meridian/v1/<session_id>")(plaintext)
"""

from __future__ import annotations

import base64
import os
import threading
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import AAD_INFO

NONCE_LEN = 12
SALT_LEN = 16
KEY_LEN = 32

#: Compact (binary) framing tags for low-bandwidth transports (DNS).
FRAME_KEX = 0x01
FRAME_CHECKIN = 0x02


def b64url_e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64url_d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except Exception as exc:
        raise CryptoError("invalid base64url") from exc


class CryptoError(Exception):
    """Raised on any cryptographic failure (bad key, tamper, replay)."""


class ReplayError(CryptoError):
    """A byte-identical envelope seen before.

    Subclassed so the wire handlers can answer idempotently ("ok" — the first
    delivery already ran) while every other crypto failure stays a rejection.
    """


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode()


def b64d(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:  # binascii.Error, ValueError
        raise CryptoError("invalid base64") from exc


def new_salt() -> bytes:
    return os.urandom(SALT_LEN)


#: How many recent inbound nonces are remembered per session for replay
#: detection, and how long a nonce stays in the window.
REPLAY_WINDOW = 1024
REPLAY_TTL = 24 * 3600


class SessionCrypto:
    """Holds one session's key material and envelope (de)serialization."""

    def __init__(self, session_id: str, key: bytes) -> None:
        self.session_id = session_id
        self.key = key
        self._aad = f"{AAD_INFO}/{session_id}".encode()
        # Inbound nonces already accepted. AES-GCM envelopes carry random
        # nonces, so any repeat is either a replayed checkin (which would
        # re-dispatch tasks and duplicate results) or nonce misuse. Reject it.
        # Guarded by a lock: listeners open envelopes from several threads
        # (aiohttp loop, DNS resolver pool).
        self._seen_lock = threading.Lock()
        self._seen: dict[bytes, float] = {}

    def _check_replay(self, nonce: bytes) -> None:
        now = time.time()
        with self._seen_lock:
            first = self._seen.get(nonce)
            if first is not None:
                raise ReplayError("replayed envelope")
            if len(self._seen) >= REPLAY_WINDOW:
                # Bound memory: evict the oldest quarter of the window.
                for old in sorted(self._seen, key=self._seen.get)[: len(self._seen) // 4]:
                    del self._seen[old]
            else:
                # Opportunistic TTL expiry while we hold the lock anyway.
                cutoff = now - REPLAY_TTL
                for old, ts in list(self._seen.items()):
                    if ts < cutoff:
                        del self._seen[old]
            self._seen[nonce] = now

    @classmethod
    def from_exchange(
        cls,
        session_id: str,
        server_priv: X25519PrivateKey,
        client_pub_b64: str,
        client_nonce_b64: str,
        server_nonce_b64: str,
    ) -> SessionCrypto:
        try:
            client_pub = X25519PublicKey.from_public_bytes(b64d(client_pub_b64))
            salt = b64d(client_nonce_b64) + b64d(server_nonce_b64)
        except CryptoError:
            raise
        except Exception as exc:
            raise CryptoError("bad key material") from exc
        if len(salt) != SALT_LEN * 2:
            raise CryptoError("bad nonce length")
        shared = server_priv.exchange(client_pub)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_LEN,
            salt=salt,
            info=AAD_INFO.encode(),
        ).derive(shared)
        return cls(session_id, key)

    def seal(self, plaintext: bytes) -> dict:
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(self.key).encrypt(nonce, plaintext, self._aad)
        return {"nonce": b64e(nonce), "ct": b64e(ct)}

    def open(self, envelope: dict) -> bytes:
        try:
            nonce = b64d(envelope["nonce"])
            ct = b64d(envelope["ct"])
        except CryptoError:
            raise
        except Exception as exc:
            raise CryptoError("malformed envelope") from exc
        if len(nonce) != NONCE_LEN:
            raise CryptoError("bad nonce length")
        try:
            plaintext = AESGCM(self.key).decrypt(nonce, ct, self._aad)
        except Exception as exc:
            raise CryptoError("decryption failed") from exc
        # Only after successful authentication: rejecting before decrypt
        # would let anyone with a guessed nonce mount a DoS on the session.
        self._check_replay(nonce)
        return plaintext

    # ------------------------------------------------ compact binary framing
    def seal_compact(self, plaintext: bytes) -> bytes:
        """Return FRAME_CHECKIN | nonce | AES-GCM ct with AAD = meridian/v1/sid."""
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(self.key).encrypt(nonce, plaintext, self._aad)
        return bytes([FRAME_CHECKIN]) + nonce + ct

    def open_compact(self, framed: bytes) -> bytes:
        if len(framed) < 1 + NONCE_LEN + 16 or framed[0] != FRAME_CHECKIN:
            raise CryptoError("bad compact frame")
        nonce = framed[1 : 1 + NONCE_LEN]
        ct = framed[1 + NONCE_LEN :]
        try:
            plaintext = AESGCM(self.key).decrypt(nonce, ct, self._aad)
        except Exception as exc:
            raise CryptoError("decryption failed") from exc
        self._check_replay(nonce)
        return plaintext


def generate_server_keypair() -> tuple[str, str]:
    """Return (private_b64, public_b64) for a fresh server identity."""
    priv = X25519PrivateKey.generate()
    pub = priv.public_key().public_bytes_raw()
    return b64e(priv.private_bytes_raw()), b64e(pub)
