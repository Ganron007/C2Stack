"""Drive the Sliver console headlessly.

Sliver's `sliver-client` CLI has no JSON/structured output flag in v1.7.7
(`-j`, `-o json` and `--output` are all rejected), and its console reads input
through a readline library that ignores a plain pipe. Wrapping the console in
`script(1)` inside the container gives it the TTY it insists on.

This is deliberately thin and never invents data: if the console cannot be
driven or the output cannot be parsed, it raises with the real reason so the
portal reports the failure instead of showing a fake session list.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

try:
    from .c2backends import BackendError
except ImportError:
    from c2backends import BackendError

SLIVER_CONTAINER = os.environ.get("SLIVER_CONTAINER", "c2stack-sliver-1")
# Matches CSI / OSC escape sequences the console emits for its banner and colours.
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]")


def _clean(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = ANSI.sub("", text)
    # The console repaints lines with carriage returns and 2K erase sequences.
    return "\n".join(line.strip() for line in text.replace("\r", "\n").split("\n"))


def run_console(commands: list[str], wait: float = 14.0,
                startup: float = 7.0) -> str:
    """Run `commands` in the Sliver console and return the cleaned output."""
    # The console reads through readline, which discards input that arrives
    # before it has finished drawing its prompt. The pacing has to happen on
    # the live stdin stream, so it is done as timed writes rather than baked
    # into a heredoc (which `script` would dump in one go).
    docker_cmd = [
        "docker", "exec", "-i", SLIVER_CONTAINER,
        # `script` allocates the TTY inside the container that the console needs.
        "script", "-qec", "sliver-client console", "/dev/null",
    ]
    if not shutil.which("docker"):
        raise BackendError("docker CLI not available in the portal container")

    proc = subprocess.Popen(docker_cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks: list[bytes] = []

    # Read on a worker thread. Selecting on the pipe's fd and then calling
    # os.read() on it deadlocks whenever the BufferedReader has already pulled
    # bytes into its own buffer: select says "not ready" while data sits in
    # Python, and the next os.read() then blocks forever.
    def reader() -> None:
        assert proc.stdout is not None
        try:
            for data in iter(lambda: proc.stdout.read(65536), b""):
                chunks.append(data)
        except (ValueError, OSError):
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def settle(seconds: float) -> None:
        time.sleep(seconds)

    settle(startup)  # let it connect and draw the banner
    for cmd in commands:
        assert proc.stdin is not None
        try:
            proc.stdin.write((cmd + "\n").encode())
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            proc.kill()
            raise BackendError(f"sliver console closed the pipe: {exc}") from exc
        settle(wait)
    settle(3)

    try:
        proc.kill()
    except OSError:
        pass

    out = _clean(b"".join(chunks))
    if "Welcome to the sliver shell" not in out:
        raise BackendError(
            "sliver console did not reach the shell. Output was: "
            f"{out[-400:]!r}")
    # The console reaches its prompt but silently ignores piped commands, even
    # when wrapped in script(1) to provide a TTY (verified on v1.7.7). Say so
    # plainly rather than returning an empty session list, which would read as
    # "no implants are checked in" - a claim we cannot actually support.
    if not any(cmd.split()[0] in out for cmd in commands if cmd.split()):
        raise BackendError(
            "sliver-client console reached its prompt but did not execute the "
            "piped commands (v1.7.7 ignores non-interactive stdin). Sliver is "
            "therefore reported as unavailable rather than as having zero "
            "sessions. Drive it from the Kali workstation with "
            "`sliver-client console`, or use its Go/Python protobuf client "
            "directly for scripted access.")
    return out


def sessions() -> list[dict[str, Any]]:
    """Live Sliver sessions.

    Returns [] when the console reports an empty session list, and raises when
    the console could not be driven at all - see run_console() for why that
    currently happens on v1.7.7. Sliver's `sessions` output is a fixed-width
    table with no machine format, so columns are taken by header position.
    """
    try:
        out = run_console(["sessions"], startup=5.0, wait=8.0)
    except BackendError as exc:
        # Never let a non-responsive console stall the whole dashboard; the
        # caller gets the reason so it can be shown as an explicit error.
        raise BackendError(str(exc)) from exc
    lines = [ln for ln in out.split("\n") if ln.strip()]
    # Find the header row: it contains the column labels.
    header_idx = None
    for i, ln in enumerate(lines):
        if "Session ID" in ln or ("ID" in ln and "Transport" in ln):
            header_idx = i
            break
    if header_idx is None:
        if "No active sessions" in out or "no sessions" in out.lower():
            return []
        raise BackendError(
            "could not find the Sliver sessions table in the console output. "
            f"Tail was: {lines[-6:]!r}")

    header = lines[header_idx]
    # Column start offsets give each field a slice of the row.
    starts = [m.start() for m in re.finditer(r"\S+", header)]
    labels = [header[s:re.search(r"\S+", header[s:]).start() + s] for s in starts]

    result: list[dict[str, Any]] = []
    for ln in lines[header_idx + 1:]:
        if not ln.strip() or "Session" in ln and "ID" in ln:
            continue
        fields = [ln[s:(starts[i + 1] if i + 1 < len(starts) else len(ln))].strip()
                  for i, s in enumerate(starts)]
        row = dict(zip(labels, fields))
        sid = row.get("Session ID") or row.get("ID")
        if not sid or not re.match(r"^[0-9a-f]{6,}$", sid):
            continue
        result.append({
            "id": sid,
            "backend": "sliver",
            "hostname": row.get("Hostname") or row.get("Host") or "?",
            "os": row.get("OS") or "?",
            "pid": row.get("PID") or "?",
            "transport": row.get("Transport") or "?",
            "username": row.get("Username") or row.get("User") or "?",
            "is_alive": True,
            "raw_row": row,
        })
    return result


def task(session_id: str, command: str, wait: float = 22.0) -> dict[str, Any]:
    """Task a Sliver session with `shell <command>` and return the output."""
    quoted = command.replace('"', '\\"')
    out = run_console([
        f"session-select -i {session_id}",
        f'shell "{quoted}"',
    ], wait=wait, startup=8.0)
    # Drop the banner so the UI shows just the task result.
    marker = "Welcome to the sliver shell"
    if marker in out:
        out = out.split(marker, 1)[1]
    body = "\n".join(ln for ln in out.split("\n") if ln.strip()
                     and "Connecting to" not in ln)
    if "no such session" in body.lower() or "invalid session" in body.lower():
        raise BackendError(f"sliver rejected session {session_id}: {body[-300:]}")
    return {"ok": True, "backend": "sliver", "session_id": session_id,
            "command": command, "output": body.strip()[:8000]}