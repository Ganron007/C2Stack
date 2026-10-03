"""Session manager: key material, lifecycle, checkin handling."""

from __future__ import annotations

import logging
import threading
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .crypto import CryptoError, SessionCrypto, b64d, b64e, new_salt
from .db import Database
from .models import Session, new_id

log = logging.getLogger("meridian.session")

#: Hard ceiling on sessions (live key material held). KEX is unauthenticated,
#: so without a cap anyone can mint sessions until memory/SQLite fall over.
MAX_SESSIONS = 1024

#: A session with no checkin for longer than this is reaped to dead (and its
#: key material dropped). Conservative on purpose: max(5 min, 10x interval).
REAP_GRACE = 300.0
REAP_FACTOR = 10.0


class SessionManager:
    """Owns the in-memory session key material and the sessions table."""

    def __init__(self, db: Database, server_priv_b64: str, server_pub_b64: str):
        self._db = db
        self._server_pub_b64 = server_pub_b64
        self._priv = X25519PrivateKey.from_private_bytes(b64d(server_priv_b64))
        self._crypto: dict[str, SessionCrypto] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ kex
    def kex(
        self,
        client_pub_b64: str,
        client_nonce_b64: str,
        meta: dict | None = None,
        profile: dict | None = None,
        listener: str = "",
        default_interval: int = 30,
        default_jitter: float = 0.2,
    ) -> dict:
        with self._lock:
            if len(self._crypto) >= MAX_SESSIONS:
                log.warning(
                    "kex refused: session table full (%d)", len(self._crypto),
                    extra={"event": "kex_refused_full"},
                )
                raise CryptoError("session table full")
        session_id = new_id()
        server_nonce = b64e(new_salt())
        crypto = SessionCrypto.from_exchange(
            session_id, self._priv, client_pub_b64, client_nonce_b64, server_nonce
        )
        # Clamp both: interval goes on the wire as u16 (base.py does
        # interval.to_bytes(2, "big"), so >65535 or <0 raises OverflowError and
        # kills DNS KEX), and a negative interval makes the Go implant spin with
        # no backoff. Both values can arrive from an unauthenticated KEX body.
        raw_interval = profile.get("interval", default_interval) if profile else default_interval
        try:
            interval = int(raw_interval)
        except (TypeError, ValueError):
            interval = default_interval
        interval = max(1, min(65535, interval))
        raw_jitter = profile.get("jitter", default_jitter) if profile else default_jitter
        try:
            jitter = float(raw_jitter)
        except (TypeError, ValueError):
            jitter = default_jitter
        jitter = max(0.0, min(1.0, jitter))
        meta = meta or {}

        s = Session(
            id=session_id,
            hostname=str(meta.get("hostname", "?")),
            os=str(meta.get("os", "?")),
            arch=str(meta.get("arch", "?")),
            pid=int(meta.get("pid", 0)),
            uid=str(meta.get("uid", "")),
            user=str(meta.get("user", "")),
            kernel=str(meta.get("kernel", "")),
            ips=list(meta.get("ips", [])),
            mac=str(meta.get("mac", "")),
            interval=interval,
            jitter=jitter,
            last_seen=time.time(),
            listener=listener,
            meta=dict(meta),
        )
        with self._lock:
            self._db.upsert_session(s)
            self._crypto[session_id] = crypto
        log.info(
            "session %s up: %s@%s (%s/%s) via %s",
            session_id[:8], s.user, s.hostname, s.os, s.arch, listener,
            extra={"event": "session_up", "session_id": session_id, "hostname": s.hostname,
                   "user": s.user, "os": s.os, "arch": s.arch, "pid": s.pid,
                   "listener": listener, "interval": s.interval, "jitter": s.jitter},
        )
        return {
            "session_id": session_id,
            "server_pub": self._server_pub_b64,
            "server_nonce": server_nonce,
            "interval": interval,
            "jitter": jitter,
            "server_time": time.time(),
        }

    # ---------------------------------------------------------------- checkin
    def checkin(self, session_id: str, results: list[dict], meta: dict | None = None) -> None:
        wire = [_result_from_wire(r, session_id) for r in results]
        self._db.apply_checkin(session_id, wire, meta, time.time())

    def enrich(self, session_id: str, meta: dict) -> None:
        """Merge late-arriving host metadata (used when KEX carries no meta)."""
        s = self._db.get_session(session_id)
        if s is None:
            return
        merged = dict(s.meta)
        merged.update(meta)
        for field in ("hostname", "os", "arch", "uid", "user", "kernel", "mac"):
            if field in meta:
                setattr(s, field, str(meta[field]))
        if "pid" in meta:
            s.pid = int(meta["pid"])
        if "ips" in meta:
            s.ips = list(meta["ips"])
        s.meta = merged
        self._db.upsert_session(s)

    def touch(self, session_id: str) -> None:
        self._db.touch_session(session_id, time.time())

    def get_crypto(self, session_id: str) -> SessionCrypto | None:
        return self._crypto.get(session_id)

    def get(self, session_id: str) -> Session | None:
        return self._db.get_session(session_id)

    def list(self) -> list[Session]:
        self.reap()
        return self._db.list_sessions()

    def reap(self, now: float | None = None) -> int:
        """Mark sessions with long-missed checkins dead and drop their keys.

        Without this the table fills with corpses: `alive` stays 1 forever
        and key material is held for sessions that will never check in again.
        Called on every listing; only writes when something actually expired.
        """
        now = time.time() if now is None else now
        expired = [
            s.id for s in self._db.list_sessions()
            if s.alive and now - s.last_seen > max(REAP_GRACE, s.interval * REAP_FACTOR)
        ]
        if not expired:
            return 0
        with self._lock:
            for sid in expired:
                self._db.mark_dead(sid)
                self._crypto.pop(sid, None)
        log.info(
            "reaped %d dead session(s)", len(expired),
            extra={"event": "sessions_reaped", "count": len(expired)},
        )
        return len(expired)

    def close(self, session_id: str) -> None:
        with self._lock:
            self._crypto.pop(session_id, None)


def _result_from_wire(r: dict, session_id: str):
    from .models import TaskResult

    return TaskResult(
        id=new_id(),
        task_id=str(r.get("id", "")),
        session_id=session_id,
        status=str(r.get("status", "ok")),
        exit_code=int(r.get("exit_code", 0)),
        stdout=b64d(r["stdout_b64"]) if r.get("stdout_b64") else b"",
        stderr=b64d(r["stderr_b64"]) if r.get("stderr_b64") else b"",
        data=b64d(r["data_b64"]) if r.get("data_b64") else None,
        ts=float(r.get("ts", time.time())),
    )
