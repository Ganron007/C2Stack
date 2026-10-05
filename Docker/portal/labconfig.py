"""Lab configuration: operator-editable network/C2 settings for C2Stack.

Why this exists
---------------
Every address in this lab used to be a module-level constant read from the
environment at import time. That is fine for a single developer whose
`.env` already matches their network, but it is not configurable by the
person actually using the portal: to point the stagers at *their* C2 host
they had to edit a file and restart the container. Worse, a handful of
values were missed entirely and stayed hardcoded (Meridian's DNS domain in
its stager, the Adaptix/Mythic UI ports, the Sliver callback port in the
JS), so a "configured" lab still emitted wrong implant URLs.

Design
------
- `SETTINGS` is the schema: every knob, with a default, help text, and an
  explicit `scope` saying what it actually affects.
- Resolution order: persisted overrides (JSON on a volume) > environment >
  documented default. That keeps Docker/.env authoritative for a fresh
  clone while letting the UI change things without a container rebuild.
- `scope` is honest about propagation. Portal-scope values change
  immediately (stagers, build forms, defaults handed to teamserver APIs).
  Stack-scope values are consumed by OTHER containers at their own boot
  (redirector routes, Havoc's profile, Meridian's listener config), so the
  UI cannot apply them; instead `render_env_file()` produces the exact
  `Docker/.env` lines the operator needs, which keeps the required manual
  step visible instead of pretending a restart is enough.

This is a lab control panel for authorised red-team use against systems you
own or have written permission to test.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from typing import Any

CONFIG_PATH = os.environ.get(
    "LAB_CONFIG_PATH", "/data/lab-config.json")

# scope "portal" -> consumed by the portal process only; applies instantly.
# scope "stack"  -> consumed by sibling containers at boot; needs Docker/.env
#                   + a container recreate (surfaced via render_env_file).
SETTINGS: dict[str, dict[str, Any]] = {
    "victim_redirector_ip": {
        "label": "Victim-facing C2 / redirector IP",
        "env": "VICTIM_REDIRECTOR_IP",
        "default": "192.168.100.1",
        "scope": "portal",
        "help": "The address implants call back to. Everything a victim or "
                "beacon dials (Havoc Hosts, Sliver c2_url, Adaptix "
                "callback_address, Mythic callback_host, all stagers) is "
                "derived from this. Set it to the host running this stack as "
                "seen FROM the victim network.",
    },
    "redirector_http_port": {
        "label": "Redirector HTTP port",
        "env": "REDIRECTOR_HTTP_PORT",
        "default": "80",
        "scope": "stack",
        "help": "Published port victims connect to. Also the port baked into "
                "implant callback URLs.",
    },
    "c2_header_name": {
        "label": "C2 header name",
        "env": "C2_HEADER_NAME",
        "default": "X-Request-ID",
        "scope": "stack",
        "help": "Redirector requires this header on C2 routes; anything "
                "missing gets the CloudEdge decoy. Must match every "
                "framework's listener config and the portal stagers.",
    },
    "c2_header_value": {
        "label": "C2 header value",
        "env": "C2_HEADER_VALUE",
        "default": "cadre-c2",
        "scope": "stack",
        "help": "Value paired with the header above. Change it and every "
                "framework's listener must change too, or beacons silently "
                "get the decoy.",
    },
    "meridian_uri_prefix": {
        "label": "Meridian URI prefix",
        "env": "MERIDIAN_URI_PREFIX",
        "default": "/gateway/v1/telemetry",
        "scope": "stack",
        "help": "Redirector route + MERIDIAN_URI_PREFIX. The implant env var "
                "is base+prefix combined, so keep this prefix-only.",
    },
    "meridian_dns_domain": {
        "label": "Meridian DNS domain",
        "env": "MERIDIAN_DNS_DOMAIN",
        "default": "c2.lab.local",
        "scope": "stack",
        "help": "DNS TXT C2 zone. Only used when the state volume is created "
                "fresh; an existing meridian_data volume keeps its domain.",
    },
    "sliver_uri_prefix": {
        "label": "Sliver URI prefix",
        "env": "SLIVER_URI_PREFIX",
        "default": "/cloud/storage/objects",
        "scope": "stack",
        "help": "Redirector route for Sliver. Sliver sends no custom headers, "
                "so this route matches on path alone.",
    },
    "havoc_uri_prefix": {
        "label": "Havoc URI prefix",
        "env": "HAVOC_URI_PREFIX",
        "default": "/edge/cache/assets",
        "scope": "stack",
        "help": "Redirector route for Havoc, and the Uris baked into the "
                "teamserver profile (which the entrypoint renders).",
    },
    "adaptix_uri_prefix": {
        "label": "Adaptix URI prefix",
        "env": "ADAPTIX_URI_PREFIX",
        "default": "/api/v1/sync",
        "scope": "stack",
        "help": "Redirector route for Adaptix HTTP Beacon.",
    },
    "mythic_uri_prefix": {
        "label": "Mythic URI prefix",
        "env": "MYTHIC_URI_PREFIX",
        "default": "/cdn/media/stream",
        "scope": "stack",
        "help": "Redirector route for Mythic. The agent posts to "
                "<prefix>/data and callback_host must stay bare (no path, no "
                "port) - Mythic's URL parser replaces the path.",
    },
    "sliver_ctrl_port": {
        "label": "Sliver control port",
        "env": "SLIVER_CTRL_PORT",
        "default": "31337",
        "scope": "stack",
        "help": "Published Sliver server control port.",
    },
    "havoc_ts_port": {
        "label": "Havoc teamserver port",
        "env": "HAVOC_TS_PORT",
        "default": "40056",
        "scope": "stack",
        "help": "Published Havoc WebSocket control port.",
    },
    "adaptix_ts_port": {
        "label": "Adaptix teamserver port",
        "env": "ADAPTIX_TS_PORT",
        "default": "4321",
        "scope": "stack",
        "help": "Published Adaptix REST/teamserver port (also shown in the "
                "operator connection instructions).",
    },
    "mythic_ui_port": {
        "label": "Mythic UI / REST port",
        "env": "MYTHIC_UI_PORT",
        "default": "7443",
        "scope": "stack",
        "help": "Published Mythic REST port used by the portal and shown in "
                "operator instructions.",
    },
    "victim_ssh_target": {
        "label": "Victim SSH target",
        "env": "VICTIM_SSH_TARGET",
        "default": "",
        "scope": "portal",
        "help": "user@host for the lab target, used by the /api/ops/victim "
                "helper. Empty means not configured; requests then fail "
                "explicitly instead of guessing.",
    },
}

_lock = threading.Lock()
# Plain module attribute, deliberately NOT `global`-declared: the mutation
# helpers use an explicit `global` statement, and a `global` inside a
# function makes every bare reference in that function a local name - which
# is exactly how a half-initialised store ends up raising UnboundLocalError.
_overrides: dict[str, str] = {}


def _load() -> dict[str, str]:
    """Read persisted overrides. A corrupt file must not take the lab down -
    it is reported so the UI can say so instead of silently resetting."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()
                    if str(k) in SETTINGS and v is not None}
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError, TypeError):
        pass
    return {}


def _save(data: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    # Atomic replace so a crash mid-write cannot leave a truncated file.
    shutil.move(tmp, CONFIG_PATH)


def reload() -> None:
    global _overrides
    with _lock:
        _overrides = _load()


def get(key: str, fallback: str | None = None) -> str:
    """Resolved value: override > environment > schema default.

    Falling back to the environment (rather than straight to the default)
    means Docker/.env keeps working exactly as before for anyone who never
    opens the config UI.
    """
    spec = SETTINGS.get(key)
    if spec is None:
        return os.environ.get(key, fallback if fallback is not None else "")
    with _lock:
        if key in _overrides and _overrides[key] != "":
            return _overrides[key]
    env_value = os.environ.get(spec["env"])
    if env_value not in (None, ""):
        return env_value
    if fallback is not None:
        return fallback
    return str(spec["default"])


def get_int(key: str, fallback: int) -> int:
    try:
        return int(str(get(key)).strip())
    except (TypeError, ValueError):
        return fallback


def set_overrides(values: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Validate and persist. Returns (applied, errors).

    Only known keys are accepted and only strings are storable; an unknown
    key is a caller bug, not a setting, so it is rejected loudly instead of
    being written to disk where nothing would read it.
    """
    global _overrides
    errors: list[str] = []
    clean: dict[str, str] = {}
    for key, value in values.items():
        spec = SETTINGS.get(key)
        if spec is None:
            errors.append(f"unknown setting '{key}'")
            continue
        if value is None:
            value = ""
        text = str(value).strip()
        if "\n" in text or "\r" in text:
            errors.append(f"{spec['label']}: no newlines allowed")
            continue
        # Ports must be numeric; catching it here beats a silently broken
        # implant URL three steps later.
        if key.endswith("_port") and text:
            try:
                port = int(text)
                if not 1 <= port <= 65535:
                    raise ValueError
            except ValueError:
                errors.append(f"{spec['label']}: must be a port 1-65535")
                continue
        if key.endswith("_uri_prefix") and text and not text.startswith("/"):
            errors.append(f"{spec['label']}: must start with '/'")
            continue
        if key == "victim_redirector_ip" and text:
            try:
                # Accept a hostname too - some labs front the stack with a
                # DNS name - but a bare host:port would produce a broken
                # implant URL, so reject that shape explicitly.
                if "/" in text or ":" in text:
                    raise ValueError
            except ValueError:
                errors.append(
                    f"{spec['label']}: host or IP only, no port or path")
                continue
        clean[key] = text
    if errors:
        return {}, errors
    with _lock:
        merged = dict(_overrides)
        merged.update(clean)
        try:
            _save(merged)
        except OSError as exc:
            return {}, [f"could not write {CONFIG_PATH}: {exc}"]
        _overrides = merged
    return clean, []


def clear(key: str) -> None:
    global _overrides
    with _lock:
        merged = {k: v for k, v in _overrides.items() if k != key}
        try:
            _save(merged)
        except OSError:
            return
        _overrides = merged


def snapshot() -> dict[str, Any]:
    """Schema + resolved values + override provenance, for the UI."""
    with _lock:
        current = dict(_overrides)
    out: dict[str, Any] = {}
    for key, spec in SETTINGS.items():
        out[key] = {
            "label": spec["label"],
            "help": spec["help"],
            "scope": spec["scope"],
            "env": spec["env"],
            "default": spec["default"],
            "value": get(key),
            "overridden": key in current,
        }
    return out


def env_lines() -> list[str]:
    """The Docker/.env lines for stack-scope settings.

    Portal-scope values are deliberately omitted: the portal persists them
    itself, so putting them in .env would create two sources of truth.
    """
    lines: list[str] = []
    for key, spec in SETTINGS.items():
        if spec["scope"] != "stack":
            continue
        lines.append(f"{spec['env']}={get(key)}")
    return lines


def render_env_file() -> str:
    lines = ["# ---- C2Stack lab configuration (from the portal config UI) ----",
             "# Stack-scope settings only: portal-scope values (victim IP, SSH",
             "# target) are stored by the portal itself in",
             f"# {CONFIG_PATH} and must NOT be duplicated here.", ""]
    lines.extend(env_lines())
    lines.append("")
    lines.append("# Apply with:  docker compose --env-file .env up -d --force-recreate")
    return "\n".join(lines) + "\n"


reload()
