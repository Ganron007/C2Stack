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
    alive       INTEGER DEFAULT 1,
    meta        TEXT
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
            # Migrate pre-existing databases that predate a schema column.
            # CREATE TABLE IF NOT EXISTS will not add it; ALTER is idempotent
            # here because the duplicate-column error is swallowed.
            for ddl in ("ALTER TABLE sessions ADD COLUMN meta TEXT",):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
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
            try:
                _, nonce, ct = blob.split(":", 2)
            except ValueError as exc:
                raise ValueError("malformed encrypted blob") from exc
            return AESGCM(self._key).decrypt(b64d(nonce), b64d(ct), b"meridian-result")
        # No silent fallthrough: previously an unknown scheme was returned as
        # opaque bytes, so a corrupt or foreign row surfaced as garbage output
        # in the operator's results view with no indication anything was wrong.
        raise ValueError(f"unknown result blob encoding: {blob[:12]!r}")

    # --------------------------------------------------------------- sessions
    def _upsert_session(self, s: Session) -> None:
        """Upsert without committing (caller owns the transaction)."""
        self._conn.execute(
            """INSERT INTO sessions (id, hostname, os, arch, pid, uid, user,
               kernel, ips, mac, interval, jitter, first_seen, last_seen,
               listener, alive, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   hostname=excluded.hostname, os=excluded.os,
                   arch=excluded.arch, pid=excluded.pid,
                   uid=excluded.uid, user=excluded.user,
                   kernel=excluded.kernel, ips=excluded.ips,
                   mac=excluded.mac, interval=excluded.interval,
                   jitter=excluded.jitter, last_seen=excluded.last_seen,
                   listener=excluded.listener, alive=excluded.alive,
                   meta=excluded.meta""",
            (
                s.id, s.hostname, s.os, s.arch, s.pid, s.uid, s.user,
                s.kernel, json.dumps(s.ips), s.mac, s.interval, s.jitter,
                s.first_seen, s.last_seen, s.listener, int(s.alive),
                json.dumps(s.meta),
            ),
        )

    def upsert_session(self, s: Session) -> None:
        with self._lock:
            self._upsert_session(s)
            self._conn.commit()

    def _touch_session(self, session_id: str, last_seen: float) -> None:
        self._conn.execute(
            "UPDATE sessions SET last_seen=? WHERE id=?", (last_seen, session_id)
        )

    def touch_session(self, session_id: str, last_seen: float) -> None:
        with self._lock:
            self._touch_session(session_id, last_seen)
            self._conn.commit()

    def mark_dead(self, session_id: str) -> None:
        """Flip a session to dead (reaped for missed checkins)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET alive=0 WHERE id=?", (session_id,)
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
        # sqlite3.Row raises IndexError for a missing column on databases the
        # __init__ migration did not touch; read defensively so one odd row
        # cannot break session listing.
        try:
            raw_meta = row["meta"]
        except IndexError:
            raw_meta = None
        try:
            meta = json.loads(raw_meta or "{}")
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
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
            meta=meta,
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
    def _insert_result(self, r: TaskResult) -> bool:
        """Insert without committing. Returns False when rejected (forgery)."""
        # Reject results for unknown/foreign tasks BEFORE persisting them:
        # task_id comes from the wire, so an implant could otherwise mark
        # another session's queued task complete (and its task would vanish
        # from the queue) and write forged rows into the results history.
        owner = self._conn.execute(
            "SELECT session_id FROM tasks WHERE id=?", (r.task_id,)
        ).fetchone()
        if owner is None or owner["session_id"] != r.session_id:
            log.warning(
                "result %s references unknown or foreign task %s from session %s",
                r.status, r.task_id[:8], r.session_id[:8],
                extra={"event": "task_result_rejected", "task_id": r.task_id,
                       "session_id": r.session_id},
            )
            return False
        self._conn.execute(
            """INSERT INTO results (id, task_id, session_id, status, exit_code,
               stdout, stderr, data, ts) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                r.id, r.task_id, r.session_id, r.status, r.exit_code,
                self._pack(r.stdout), self._pack(r.stderr),
                self._pack(r.data) if r.data is not None else None, r.ts,
            ),
        )
        # Scope the completion to the owning session: task_id arrives from
        # the wire, so without this any implant can mark another session's
        # queued task complete and make it vanish from the task queue.
        self._conn.execute(
            "UPDATE tasks SET completed=1 WHERE id=? AND session_id=?",
            (r.task_id, r.session_id),
        )
        return True

    def insert_result(self, r: TaskResult) -> None:
        with self._lock:
            accepted = self._insert_result(r)
            self._conn.commit()
        if accepted:
            log.info(
                "result %s -> task %s (%s, exit %s)",
                r.status, r.task_id[:8], r.session_id[:8], r.exit_code,
                extra={"event": "task_result", "result_id": r.id, "task_id": r.task_id,
                       "session_id": r.session_id, "status": r.status,
                       "exit_code": r.exit_code},
            )

    def apply_checkin(
        self,
        session_id: str,
        results: list[TaskResult],
        meta: dict | None,
        now: float,
    ) -> None:
        """Apply a whole checkin — touch, metadata merge, results — as ONE
        transaction.

        Previously touch/enrich/each-result committed separately, so a crash
        mid-checkin left half-applied state (last_seen bumped, results lost).
        """

        with self._lock:
            self._touch_session(session_id, now)
            if meta:
                row = self._conn.execute(
                    "SELECT * FROM sessions WHERE id=?", (session_id,)
                ).fetchone()
                if row is not None:
                    s = self._row_to_session(row)
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
                    self._upsert_session(s)
            accepted = 0
            for r in results:
                if self._insert_result(r):
                    accepted += 1
            self._conn.commit()
        if accepted:
            log.info(
                "checkin %s: %d result(s) stored", session_id[:8], accepted,
                extra={"event": "checkin_applied", "session_id": session_id,
                       "results": accepted},
            )

    def list_results(
        self, session_id: str | None = None, since: float | None = None
    ) -> list[TaskResult]:
        # Finding #17: the console polled the ENTIRE results table every
        # second. `since` lets it fetch only what is new (indexed on ts via
        # the session index + ts ordering).
        q = "SELECT * FROM results"
        clauses: list[str] = []
        args: list = []
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if since is not None:
            clauses.append("ts>?")
            args.append(since)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(q, tuple(args)).fetchall()
        out = []
        for r in rows:
            try:
                data = self._unpack(r["data"]) if r["data"] is not None else None
                stdout = self._unpack(r["stdout"])
                stderr = self._unpack(r["stderr"])
            except Exception as exc:
                # One corrupt row must not nuke the whole results listing, but
                # it must not render as plausible output either.
                log.warning(
                    "unreadable result row %s: %s", r["id"][:8], exc,
                    extra={"event": "result_unreadable", "result_id": r["id"]},
                )
                stdout = stderr = b"[unreadable stored blob]"
                data = None
            out.append(
                TaskResult(
                    id=r["id"], task_id=r["task_id"], session_id=r["session_id"],
                    status=r["status"], exit_code=r["exit_code"],
                    stdout=stdout, stderr=stderr,
                    data=data, ts=r["ts"],
                )
            )
        return out
