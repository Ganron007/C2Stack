"""Structured JSONL logging.

All "meridian.*" loggers write human-readable lines to stderr and JSON objects
to <state_dir>/events.jsonl, one per line. Call setup(state_dir) once during
startup, then log as usual. Emit machine-readable events by passing
extra={"event": "...", ...fields}.

Example events.jsonl line:
    {"ts":"2026-08-07T12:00:00.000Z","level":"INFO","logger":"meridian.session",
     "event":"session_up","session_id":"...","hostname":"lab01",...}
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """Renders a log record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        if record.msg is not None:
            data["msg"] = record.getMessage()
        for key in ("event", "session_id", "task_id", "result_id", "hostname",
                    "os", "arch", "listener", "transport", "mod", "status",
                    "interval", "jitter", "ip", "ua", "pid", "user", "task_args"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=True)


class HumanFormatter(logging.Formatter):
    """Compact one-line stderr formatting."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        event = getattr(record, "event", None)
        suffix = f" [{event}]" if event else ""
        logger = record.name.split(".")[-1]
        return f"{ts} {record.levelname:>5} {logger}{suffix} {record.getMessage()}"


def setup(state_dir: Path, level: int = logging.INFO) -> None:
    """Configure the meridian logger family with stderr + JSONL file handlers."""
    root = logging.getLogger("meridian")
    root.setLevel(level)
    if root.handlers:
        return  # already configured

    human = logging.StreamHandler(sys.stderr)
    human.setFormatter(HumanFormatter())
    root.addHandler(human)

    json_path = Path(state_dir) / "events.jsonl"
    fileh = logging.FileHandler(json_path, encoding="utf-8")
    fileh.setFormatter(JsonFormatter())
    root.addHandler(fileh)


def event(event: str, logger: str = "meridian", level: int = logging.INFO, **fields) -> None:
    """Emit a structured event without an interpolated message."""
    log = logging.getLogger(logger)
    log.log(level, event.replace("_", " ").capitalize(), extra={"event": event, **fields})
