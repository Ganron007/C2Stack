"""Reporting: turn raw sessions/results into operator-ready artifacts."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render_markdown(db, sessions: list, results: list) -> str:
    lines = ["# Meridian Engagement Report", ""]
    lines.append(f"Generated: {_ts(time.time())}")
    lines.append(f"Sessions in report: {len(sessions)}")
    lines.append(f"Results in report: {len(results)}")
    lines.append("")

    for s in sessions:
        lines.append(f"## Session {s.id[:8]} — {s.hostname}")
        lines.append("")
        lines.append(f"- OS: {s.os}/{s.arch}  user: {s.user} (uid {s.uid})")
        lines.append(f"- Kernel: {s.kernel}  PID: {s.pid}")
        lines.append(f"- First seen: {_ts(s.first_seen)}  Last seen: {_ts(s.last_seen)}")
        lines.append(f"- Listener: {s.listener}  Interval: {s.interval}s ±{s.jitter * 100:.0f}%")
        if s.ips:
            lines.append(f"- IPs: {', '.join(s.ips)}")
        lines.append("")

        session_results = [r for r in results if r.session_id == s.id]
        if session_results:
            lines.append("### Tasks")
            lines.append("")
            lines.append("| time (UTC) | task | status | exit | stdout (hex) |")
            lines.append("|---|---|---|---|---|")
            for r in session_results:
                task = db.get_task(r.task_id)
                module = task.module if task else "?"
                out = (r.stdout[:64] + (b"..." if len(r.stdout) > 64 else b"")).hex()
                lines.append(
                    f"| {_ts(r.ts)} | {module} | {r.status} | {r.exit_code} | {out} |"
                )
            lines.append("")
    return "\n".join(lines)


def write_report(db, sessions, results, path: Path, fmt: str = "markdown") -> Path:
    path = Path(path)
    if fmt == "markdown":
        path.write_text(render_markdown(db, sessions, results))
    elif fmt == "json":
        payload = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "sessions": [s.to_dict(full=True) for s in sessions],
            "results": [
                {
                    "task_id": r.task_id,
                    "session_id": r.session_id,
                    "status": r.status,
                    "exit_code": r.exit_code,
                    "stdout_b64": base64.b64encode(r.stdout).decode(),
                    "ts": r.ts,
                }
                for r in results
            ],
        }
        path.write_text(json.dumps(payload, indent=2))
    else:
        raise ValueError(f"unknown format: {fmt}")
    return path
