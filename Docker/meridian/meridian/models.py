"""Data models shared across the server."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Session:
    id: str
    hostname: str
    os: str
    arch: str
    pid: int
    uid: str
    user: str
    kernel: str
    ips: list[str] = field(default_factory=list)
    mac: str = ""
    interval: int = 30
    jitter: float = 0.2
    last_seen: float = 0.0
    first_seen: float = field(default_factory=time.time)
    listener: str = ""
    alive: bool = True
    meta: dict = field(default_factory=dict)

    def to_dict(self, full: bool = False) -> dict:
        d = {
            "id": self.id,
            "hostname": self.hostname,
            "os": self.os,
            "arch": self.arch,
            "pid": self.pid,
            "uid": self.uid,
            "user": self.user,
            "kernel": self.kernel,
            "ips": self.ips,
            "mac": self.mac,
            "interval": self.interval,
            "jitter": self.jitter,
            "last_seen": self.last_seen,
            "first_seen": self.first_seen,
            "listener": self.listener,
            "alive": self.alive,
        }
        if full:
            d["meta"] = self.meta
        return d


@dataclass
class Task:
    id: str
    session_id: str
    module: str
    args: dict
    ttl: int = 120
    created: float = field(default_factory=time.time)
    dispatched: bool = False
    completed: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "module": self.module,
            "args": self.args,
            "ttl": self.ttl,
            "created": self.created,
        }


@dataclass
class TaskResult:
    id: str
    task_id: str
    session_id: str
    status: str  # ok | error
    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""
    data: bytes | None = None
    ts: float = field(default_factory=time.time)
