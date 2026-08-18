"""Session manager: key material, lifecycle, checkin handling."""

from __future__ import annotations

import logging
import threading
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from .crypto import SessionCrypto, b64d, b64e, new_salt
from .db import Database
from .models import Session, new_id

log = logging.getLogger("meridian.session")


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
        session_id = new_id()
        server_nonce = b64e(new_salt())
        crypto = SessionCrypto.from_exchange(
            session_id, self._priv, client_pub_b64, client_nonce_b64, server_nonce
        )
        interval = int(profile.get("interval", default_interval)) if profile else default_interval
        jitter = float(profile.get("jitter", default_jitter)) if profile else default_jitter
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
        self.touch(session_id)
        if meta:
            self.enrich(session_id, meta)
        for r in results:
            self._db.insert_result(_result_from_wire(r, session_id))

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
        return self._db.list_sessions()

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
