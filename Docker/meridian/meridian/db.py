"""SQLite persistence for sessions, tasks and results.

Results can be stored encrypted at rest (AES-256-GCM with the server master
key) or plain for lab work. Encryption is transparent to callers.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import NONCE_LEN, b64d, b64e
from .models import Session, Task, TaskResult

log = logging.getLogger("meridian.task")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    hostname    TEXT,
    os          TEXT,
    arch        TEXT,
    pid         INTEGER,
    uid         TEXT,
    user        TEXT,
    kernel      TEXT,
    ips         TEXT,
    mac         TEXT,
    interval    INTEGER,
    jitter      REAL,
    first_seen  REAL,
    last_seen   REAL,
    listener    TEXT,
    alive       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    module      TEXT NOT NULL,
    args        TEXT,
    ttl         INTEGER DEFAULT 120,
    created     REAL,
    dispatched  INTEGER DEFAULT 0,
    completed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS results (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    status      TEXT,
    exit_code   INTEGER,
    stdout      TEXT,
    stderr      TEXT,
    data        TEXT,
    ts          REAL
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_results_session ON results(session_id);
CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id);
"""


class Database:
    def __init__(self, path: Path, master_key: bytes | None, encrypt_results: bool):
        self._lock = threading.RLock()
        self._encrypt = encrypt_results
        self._key = master_key
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ blob
    def _pack(self, data: bytes) -> str:
        if not self._encrypt:
            return "raw:" + b64e(data)
        if self._key is None:
            raise RuntimeError("master key missing")
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(self._key).encrypt(nonce, data, b"meridian-result")
        return "enc:" + b64e(nonce) + ":" + b64e(ct)

    def _unpack(self, blob: str | None) -> bytes:
        if not blob:
            return b""
        if blob.startswith("raw:"):
            return b64d(blob[4:])
        if blob.startswith("enc:"):
            if self._key is None:
                raise RuntimeError("master key missing")
            _, nonce, ct = blob.split(":", 2)
            return AESGCM(self._key).decrypt(b64d(nonce), b64d(ct), b"meridian-result")
        return blob.encode()

    # --------------------------------------------------------------- sessions
    def upsert_session(self, s: Session) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO sessions (id, hostname, os, arch, pid, uid, user,
                   kernel, ips, mac, interval, jitter, first_seen, last_seen,
                   listener, alive)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       hostname=excluded.hostname, os=excluded.os,
                       arch=excluded.arch, pid=excluded.pid,
                       uid=excluded.uid, user=excluded.user,
                       kernel=excluded.kernel, ips=excluded.ips,
                       mac=excluded.mac, interval=excluded.interval,
                       jitter=excluded.jitter, last_seen=excluded.last_seen,
                       listener=excluded.listener, alive=excluded.alive""",
                (
                    s.id, s.hostname, s.os, s.arch, s.pid, s.uid, s.user,
                    s.kernel, json.dumps(s.ips), s.mac, s.interval, s.jitter,
                    s.first_seen, s.last_seen, s.listener, int(s.alive),
                ),
            )
            self._conn.commit()

    def touch_session(self, session_id: str, last_seen: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET last_seen=? WHERE id=?", (last_seen, session_id)
            )
            self._conn.commit()

    def get_session(self, session_id: str) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def list_sessions(self) -> list[Session]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY first_seen").fetchall()
        return [self._row_to_session(r) for r in rows]

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            hostname=row["hostname"],
            os=row["os"],
            arch=row["arch"],
            pid=row["pid"],
            uid=row["uid"],
            user=row["user"],
            kernel=row["kernel"],
            ips=json.loads(row["ips"] or "[]"),
            mac=row["mac"],
            interval=row["interval"],
            jitter=row["jitter"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            listener=row["listener"],
            alive=bool(row["alive"]),
        )

    # ----------------------------------------------------------------- tasks
    def count_pending_tasks(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE session_id=? AND completed=0",
                (session_id,),
            ).fetchone()
            return row[0] if row else 0

    def insert_task(self, t: Task) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO tasks (id, session_id, module, args, ttl, created,
                   dispatched, completed) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    t.id, t.session_id, t.module, json.dumps(t.args), t.ttl,
                    t.created, int(t.dispatched), int(t.completed),
                ),
            )
            self._conn.commit()

    def pending_tasks(self, session_id: str) -> list[Task]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM tasks WHERE session_id=? AND completed=0
                   AND dispatched=0 ORDER BY created""",
                (session_id,),
            ).fetchall()
            ids = [r["id"] for r in rows]
            self._conn.executemany(
                "UPDATE tasks SET dispatched=1 WHERE id=?", [(i,) for i in ids]
            )
            self._conn.commit()
        return [
            Task(
                id=r["id"], session_id=r["session_id"], module=r["module"],
                args=json.loads(r["args"] or "{}"), ttl=r["ttl"], created=r["created"],
                dispatched=True,
            )
            for r in rows
        ]

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if not row:
            return None
        return Task(
            id=row["id"], session_id=row["session_id"], module=row["module"],
            args=json.loads(row["args"] or "{}"), ttl=row["ttl"], created=row["created"],
            dispatched=bool(row["dispatched"]), completed=bool(row["completed"]),
        )

    # --------------------------------------------------------------- results
    def insert_result(self, r: TaskResult) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO results (id, task_id, session_id, status, exit_code,
                   stdout, stderr, data, ts) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    r.id, r.task_id, r.session_id, r.status, r.exit_code,
                    self._pack(r.stdout), self._pack(r.stderr),
                    self._pack(r.data) if r.data is not None else None, r.ts,
                ),
            )
            self._conn.execute("UPDATE tasks SET completed=1 WHERE id=?", (r.task_id,))
            self._conn.commit()
        log.info(
            "result %s -> task %s (%s, exit %s)",
            r.status, r.task_id[:8], r.session_id[:8], r.exit_code,
            extra={"event": "task_result", "result_id": r.id, "task_id": r.task_id,
                   "session_id": r.session_id, "status": r.status,
                   "exit_code": r.exit_code},
        )

    def list_results(self, session_id: str | None = None) -> list[TaskResult]:
        q = "SELECT * FROM results"
        args: tuple = ()
        if session_id:
            q += " WHERE session_id=?"
            args = (session_id,)
        q += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = []
        for r in rows:
            data = self._unpack(r["data"]) if r["data"] is not None else None
            out.append(
                TaskResult(
                    id=r["id"], task_id=r["task_id"], session_id=r["session_id"],
                    status=r["status"], exit_code=r["exit_code"],
                    stdout=self._unpack(r["stdout"]), stderr=self._unpack(r["stderr"]),
                    data=data, ts=r["ts"],
                )
            )
        return out
