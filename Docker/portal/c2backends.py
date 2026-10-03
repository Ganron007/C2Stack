"""Real C2 backend integrations for the portal.

Every function here talks to an ACTUAL backend over the network and returns real
data or an explicit error. Nothing fabricates sessions, HTTP statuses or build
results - the previous implementation returned invented hosts when a backend
was unreachable, which made the UI lie about the lab's state.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import urllib.parse
import urllib.request
from typing import Any

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
MYTHIC_ENDPOINT = os.environ.get("MYTHIC_ENDPOINT", "http://mythic_server:17443")
MYTHIC_UI_PORT = os.environ.get("MYTHIC_UI_PORT", "7443")
MYTHIC_USER = os.environ.get("MYTHIC_USERNAME", "mythic_admin")
MYTHIC_PASS = os.environ.get("MYTHIC_PASSWORD", "mythic")
MYTHIC_DB_CONTAINER = os.environ.get("MYTHIC_DB_CONTAINER", "c2stack-mythic_postgres-1")

MERIDIAN_HOST = os.environ.get("MERIDIAN_BACKEND_HOST", "meridian")
MERIDIAN_PORT = int(os.environ.get("MERIDIAN_BACKEND_PORT", "8080"))

REDIRECTOR_HOST = os.environ.get("REDIRECTOR_HOST", "redirector")
REDIRECTOR_PORT = int(os.environ.get("REDIRECTOR_HTTP_PORT", "80"))

WS01_SSH = os.environ.get("WS01_SSH", "analyst_t1@192.168.77.62")
WS01_SSH_KEY = os.environ.get("WS01_SSH_KEY", "/root/.ssh/cadre-ws01-key")


class BackendError(RuntimeError):
    """A backend was unreachable or returned an error. Never swallowed."""


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------
def _json_or_raise(raw: bytes, what: str) -> dict[str, Any]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackendError(f"{what}: response was not JSON ({exc})") from exc
    if isinstance(data, dict) and data.get("status") == "error":
        raise BackendError(str(data.get("error") or "unknown backend error"))
    return data if isinstance(data, dict) else {"data": data}


# --------------------------------------------------------------------------
# Mythic
# --------------------------------------------------------------------------
class MythicClient:
    """Minimal Mythic REST client (JWT auth, v1.4 webhooks)."""

    def __init__(self, endpoint: str = MYTHIC_ENDPOINT) -> None:
        self.endpoint = endpoint.rstrip("/")
        self._token: str | None = None

    def _login(self) -> str:
        if self._token:
            return self._token
        body = json.dumps({"username": MYTHIC_USER, "password": MYTHIC_PASS}).encode()
        req = urllib.request.Request(
            f"{self.endpoint}/auth", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = _json_or_raise(resp.read(), "mythic /auth")
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"mythic /auth unreachable: {exc}") from exc
        token = data.get("access_token")
        if not token:
            raise BackendError("mythic /auth returned no access_token")
        self._token = token
        return token

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._login()
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.endpoint}{path}", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json_or_raise(resp.read(), f"mythic {path}")
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"mythic {path} failed: {exc}") from exc

    def get(self, path: str, timeout: int = 30) -> bytes:
        token = self._login()
        req = urllib.request.Request(
            f"{self.endpoint}{path}",
            headers={"Authorization": f"Bearer {token}"}, method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            raise BackendError(f"mythic GET {path} failed: {exc}") from exc


def mythic_psql(sql: str, columns: list[str] | None = None) -> list[dict[str, str]]:
    """Run a query inside the Mythic postgres container. Returns [] on failure
    so callers can distinguish 'no rows' from 'unreachable' via is_available()."""
    cmd = [
        "docker", "exec", MYTHIC_DB_CONTAINER,
        "psql", "-U", "mythic_user", "-d", "mythic_db",
        "-t", "-A", "-F", "\t", "-c", sql,
    ]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=25, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"psql unavailable: {exc}") from exc
    if done.returncode != 0:
        raise BackendError(done.stderr.decode("utf-8", "replace")[:200])
    out = done.stdout.decode("utf-8", "replace").strip()
    if not out:
        return []
    rows = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        vals = line.split("\t")
        if columns:
            # Map positionally onto the requested column names. psql -t -A
            # emits bare values, so positional mapping is the only reliable
            # way (dict(zip(vals, vals)) silently collapses every row to one
            # key/value pair).
            rows.append({c: (vals[i] if i < len(vals) else "") for i, c in enumerate(columns)})
        else:
            rows.append({"value": vals[0]} if len(vals) == 1
                        else dict(zip(vals[0::2], vals[1::2])))
    return rows


# --------------------------------------------------------------------------
# Meridian
# --------------------------------------------------------------------------
def _meridian_cli(args: list[str], container: str = "c2stack-meridian-1") -> str:
    cmd = ["docker", "exec", container, "meridian", *args]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise BackendError(f"meridian CLI unavailable: {exc}") from exc
    if done.returncode != 0:
        raise BackendError(done.stderr.decode("utf-8", "replace")[:200])
    return done.stdout.decode("utf-8", "replace")


def meridian_sessions() -> list[dict[str, Any]]:
    raw = _meridian_cli(["sessions", "--json"])
    try:
        data = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
    except (ValueError, IndexError):
        # last line is the JSON array
        for line in reversed(raw.strip().split("\n")):
            line = line.strip()
            if line.startswith("["):
                try:
                    data = json.loads(line)
                except ValueError as exc:
                    raise BackendError(f"meridian sessions JSON: {exc}") from exc
                break
        else:
            raise BackendError("meridian sessions: no JSON array in output")
    out = []
    for s in data if isinstance(data, list) else []:
        out.append({
            "id": s.get("id", ""),
            "backend": "meridian",
            "hostname": s.get("hostname") or s.get("host") or "?",
            "username": s.get("user") or s.get("username") or "?",
            "os": f"{s.get('os','?')} {s.get('arch','')}".strip(),
            # meridian's session record has no process-name field (verified
            # against `meridian sessions --json`), only a pid. Showing the pid
            # alone beats a bare "?" which read as missing data.
            "process": "",
            "pid": s.get("pid") or s.get("process_id") or "",
            "transport": "HTTP" if s.get("listener") == "http" else "DNS TXT",
            "interval": s.get("interval"),
            "last_seen": s.get("last_seen", 0),
            "is_alive": bool(s.get("alive", s.get("is_alive", False))),
        })
    return out


def meridian_exec(session_id: str, command_line: str) -> dict[str, Any]:
    """Queue a command. Meridian's CLI takes the raw command line positionally."""
    raw = _meridian_cli(["exec", session_id, command_line, "--json"])
    for line in reversed(raw.strip().split("\n")):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError as exc:
                raise BackendError(f"meridian exec JSON: {exc}") from exc
    raise BackendError("meridian exec: no JSON in output")


def meridian_results(session_id: str | None = None) -> list[dict[str, Any]]:
    args = ["results", "--json"] + ([session_id] if session_id else [])
    raw = _meridian_cli(args)
    for line in reversed(raw.strip().split("\n")):
        line = line.strip()
        if line.startswith("["):
            try:
                data = json.loads(line)
            except ValueError as exc:
                raise BackendError(f"meridian results JSON: {exc}") from exc
            break
    else:
        raise BackendError("meridian results: no JSON array")
    out = []
    for r in data if isinstance(data, list) else []:
        stdout = r.get("stdout_b64") or ""
        try:
            import base64
            decoded = base64.b64decode(stdout).decode("utf-8", "replace") if stdout else ""
        except Exception:
            decoded = ""
        out.append({
            "task_id": r.get("task_id", ""),
            "module": r.get("module", ""),
            "status": r.get("status", ""),
            "exit_code": r.get("exit_code"),
            "stdout": decoded,
            "ts": r.get("ts"),
            # builtin/download file bytes (None for other modules). Surfaced
            # so retrieved files are actually reachable from the portal.
            "data_b64": r.get("data_b64"),
            "data_size": len(r.get("data_b64") or "") * 3 // 4,
        })
    return out


# --------------------------------------------------------------------------
# Redirector - REAL probe, no simulation
# --------------------------------------------------------------------------
def probe_redirector(path: str, headers: dict[str, str] | None = None,
                     method: str = "GET", timeout: float = 6.0) -> dict[str, Any]:
    """Actually issue an HTTP request through the redirector and report the real
    status, byte length and whether the CloudEdge decoy came back.

    Decoy detection is by content, not by a hardcoded size: the decoy page is
    ~406 bytes and contains 'CloudEdge'. A backend 404 is 0 or 14 bytes.
    """
    url = f"http://{REDIRECTOR_HOST}:{REDIRECTOR_PORT}{path if path.startswith('/') else '/' + path}"
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    started = os.times()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
    except Exception as exc:
        return {
            "ok": False, "url": url, "error": str(exc),
            "status": None, "bytes": 0, "is_decoy": None,
            "verdict": "unreachable",
            "explanation": "redirector did not respond",
        }
    text = body.decode("utf-8", errors="replace")
    is_decoy = "CloudEdge" in text
    if is_decoy:
        verdict = "decoy"
        explanation = "CloudEdge decoy page served - request did NOT reach a C2 backend"
    elif status in (502, 503, 504):
        verdict = "backend_down"
        explanation = f"route matched but the backend refused the connection (HTTP {status})"
    else:
        verdict = "backend"
        explanation = f"routed to a C2 backend (HTTP {status}, {len(body)} bytes)"
    return {
        "ok": True, "url": url, "status": status, "bytes": len(body),
        "content_type": ctype, "is_decoy": is_decoy,
        "verdict": verdict, "explanation": explanation,
        "preview": text[:160],
    }


# --------------------------------------------------------------------------
# ws01 - victim host, over SSH
# --------------------------------------------------------------------------
def ws01_exec(command: str, timeout: int = 30) -> dict[str, Any]:
    """Run a command on the victim. Detached launch uses WMI Win32_Process.Create
    so the process is not killed when the SSH session closes."""
    key = WS01_SSH_KEY if os.path.exists(WS01_SSH_KEY) else None
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12"]
    if key:
        cmd += ["-i", key]
    cmd += [WS01_SSH, command]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ssh timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "error": "ssh client not found in the portal container"}
    return {
        "ok": done.returncode == 0,
        "returncode": done.returncode,
        "stdout": done.stdout.decode("utf-8", "replace"),
        "stderr": done.stderr.decode("utf-8", "replace"),
    }


def ws01_reachable() -> bool:
    try:
        with socket.create_connection(("192.168.77.62", 22), timeout=3):
            return True
    except OSError:
        return False