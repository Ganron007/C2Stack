"""Render sibling containers' config files from the portal's lab settings.

Why this exists
---------------
Stack-scope settings were previously consumed by the other containers at
boot via `docker compose` environment variables. That works, but it means an
operator who edits a route prefix in the UI is told to hand-edit `.env` and
recreate containers - a manual, error-prone step that silently does nothing
if skipped. Worse, the same value then has two sources of truth.

This module renders those config files directly and hands them to the
containers through a shared volume, so the portal becomes the single writer.
Each container's entrypoint prefers the rendered file and falls back to its
own environment rendering when the file is absent - so a plain `docker
compose up` with no portal involvement still works exactly as before.

Deliberate design points:

- Only `portal` mounts the render volume read-write. The consumers mount it
  read-only, so a compromised sibling cannot rewrite the routing policy.
- Rendering is pure: same settings in, same bytes out. That makes it
  testable and means "did my change take effect" is answerable by diffing.
- The redirector renderer mirrors the shell entrypoint's variable list
  EXACTLY, and both refuse to emit a config with an unsubstituted reference.
  A silently-unsubstituted route matches nothing and every request falls
  through to the decoy - the failure mode that cost the httpx route once.

This is a lab control panel for authorised red-team use against systems you
own or have written permission to test.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import labconfig

RENDER_DIR = os.environ.get("LAB_RENDER_DIR", "/render")

REDIRECTOR_CONF = os.path.join(RENDER_DIR, "redirector", "c2stack.conf")
MERIDIAN_CONFIG = os.path.join(RENDER_DIR, "meridian", "config.json")
HAVOC_PROFILE = os.path.join(RENDER_DIR, "havoc", "havoc.yaotl")

# Backend wiring. These are container-network coordinates (Docker service
# names), not operator-facing addresses, so they stay as compose defaults
# rather than UI settings - a lab cannot sensibly move a backend onto a
# different Docker network from a browser form.
BACKENDS = {
    "mythic": ("MYTHIC_BACKEND_HOST", "mythic_http", 80),
    "mythic_httpx": ("MYTHIC_HTTPX_BACKEND_HOST", "mythic_httpx", 82),
    "sliver": ("SLIVER_BACKEND_HOST", "sliver", 80),
    "havoc": ("HAVOC_BACKEND_HOST", "havoc", 80),
    "adaptix": ("ADAPTIX_BACKEND_HOST", "adaptix", 80),
    "meridian": ("MERIDIAN_BACKEND_HOST", "meridian", 8080),
}

# The httpx profile has its own prefix (mythic_httpx is a second Mythic
# profile container), and it is NOT one of the five UI prefix settings - it
# is a separate C2 profile with its own listener.
MYTHIC_HTTPX_PREFIX_DEFAULT = "/media/uploads"


class RenderError(Exception):
    """A rendered config would be unsafe to ship (unsubstituted var, etc)."""


def _backend(name: str) -> tuple[str, int]:
    env_key, default_host, default_port = BACKENDS[name]
    host = os.environ.get(env_key, default_host)
    port_env = env_key.replace("_HOST", "_PORT")
    try:
        port = int(os.environ.get(port_env, str(default_port)))
    except ValueError:
        port = default_port
    return host, port


def _httpx_prefix() -> str:
    return os.environ.get("MYTHIC_HTTPX_URI_PREFIX", MYTHIC_HTTPX_PREFIX_DEFAULT)


def _sliver_gate() -> str:
    """The Sliver route's header condition.

    Sliver's HTTP implant sends no custom headers (its beacon is just
    `<prefix>/<session-id>`), so requiring the C2 header there silently
    blocked every Sliver request while looking correctly configured. The
    always-true branch is used instead. The ON branch must ALSO be
    always-true - a previous version tested for a header no request carries,
    which made the route match nothing.
    """
    if os.environ.get("SLIVER_HEADER_GATE", "on") == "off":
        return r"%{REQUEST_URI} ^/"
    return r"%{HTTP:" + labconfig.get("c2_header_name") + r"} ^" + \
        labconfig.get("c2_header_value") + r"$ [NC]"


def render_redirector(template: str) -> str:
    """Fill the Apache vhost template.

    APACHE_LOG_DIR is Apache's own variable and is expanded at runtime, so
    it is the one reference allowed to survive.
    """
    values: dict[str, str] = {
        "C2_HEADER_NAME": labconfig.get("c2_header_name"),
        "C2_HEADER_VALUE": labconfig.get("c2_header_value"),
        "SLIVER_HEADER_GATE": _sliver_gate(),
        "MYTHIC_URI_PREFIX": labconfig.get("mythic_uri_prefix"),
        "MYTHIC_HTTPX_URI_PREFIX": _httpx_prefix(),
        "SLIVER_URI_PREFIX": labconfig.get("sliver_uri_prefix"),
        "HAVOC_URI_PREFIX": labconfig.get("havoc_uri_prefix"),
        "ADAPTIX_URI_PREFIX": labconfig.get("adaptix_uri_prefix"),
        "MERIDIAN_URI_PREFIX": labconfig.get("meridian_uri_prefix"),
    }
    for name in BACKENDS:
        host, port = _backend(name)
        values[f"{name.upper()}_BACKEND_HOST"] = host
        values[f"{name.upper()}_BACKEND_PORT"] = str(port)

    out = template
    for key, value in values.items():
        # Plain str.replace, not regex: values can contain characters that
        # are meaningful in a replacement pattern ($ and \ especially, which
        # are entirely plausible inside a header value).
        out = out.replace("${" + key + "}", value)

    leftover = sorted({m for m in re.findall(r"\$\{([A-Z_]+)\}", out)
                       if m != "APACHE_LOG_DIR"})
    if leftover:
        # A surviving reference means a route that can never match: the
        # request falls through to the decoy with no error anywhere.
        raise RenderError(
            "unsubstituted variables in the rendered vhost: "
            + ", ".join(leftover))
    return out


def render_meridian() -> dict[str, Any]:
    """The seeded listener config.

    Only the DNS domain is operator-facing here; host/port are the in-container
    binds the redirector proxies to. `interval`/`jitter` are not UI settings
    because they are implant-visible tuning, not lab topology.
    """
    return {
        "interval": 30,
        "jitter": 0.2,
        "store_results": "encrypted",
        "listeners": [
            {"name": "http-c2", "transport": "http", "host": "0.0.0.0",
             "port": 8080, "domain": labconfig.get("meridian_dns_domain")},
            {"name": "dns-c2", "transport": "dns", "host": "0.0.0.0",
             "port": 5353, "domain": labconfig.get("meridian_dns_domain")},
        ],
    }


def render_havoc_profile(template: str) -> str:
    """The Havoc teamserver profile template.

    The teamserver bakes Hosts/ports/URIs/headers into every Demon at BUILD
    time, so a wrong value here fails silently: beacons dial a dead address
    and nothing reports an error.
    """
    values = {
        "VICTIM_REDIRECTOR_IP": labconfig.get("victim_redirector_ip"),
        "REDIRECTOR_HTTP_PORT": labconfig.get("redirector_http_port"),
        "HAVOC_TS_PORT": labconfig.get("havoc_ts_port"),
        "HAVOC_URI_PREFIX": labconfig.get("havoc_uri_prefix"),
        "C2_HEADER_NAME": labconfig.get("c2_header_name"),
        "C2_HEADER_VALUE": labconfig.get("c2_header_value"),
    }
    out = template
    for key, value in values.items():
        out = out.replace(f"__{key}__", value)
    leftover = sorted(set(re.findall(r"__[A-Z_]+__", out)))
    if leftover:
        raise RenderError(
            "unsubstituted placeholders in the rendered Havoc profile: "
            + ", ".join(leftover))
    return out


def _write(path: str, content: str) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    # Atomic so a consumer can never read a half-written config - that would
    # look exactly like a broken route.
    os.replace(tmp, path)
    return len(content.encode("utf-8"))


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def render_all(templates: dict[str, str] | None = None) -> dict[str, Any]:
    """Render every config the portal owns.

    `templates` supplies the source templates (redirector vhost + havoc
    profile). They are read from disk when omitted. A missing template is
    reported per-target rather than aborting the whole render, so one
    unavailable template doesn't hide a successful redirector write.
    """
    results: dict[str, Any] = {"ok": True, "written": [], "errors": []}

    tdir = os.path.join(RENDER_DIR, "templates")

    def _template(name: str) -> str | None:
        if templates and name in templates:
            return templates[name]
        return _read(os.path.join(tdir, name))

    redirector_tpl = _template("c2stack.conf.template")
    if redirector_tpl is None:
        results["errors"].append(
            "redirector: template c2stack.conf.template not found "
            f"(looked in {tdir})")
    else:
        try:
            size = _write(REDIRECTOR_CONF,
                          render_redirector(redirector_tpl))
            results["written"].append({"target": "redirector",
                                       "path": REDIRECTOR_CONF,
                                       "bytes": size})
        except RenderError as exc:
            results["errors"].append(f"redirector: {exc}")

    try:
        size = _write(MERIDIAN_CONFIG,
                      json.dumps(render_meridian(), indent=2) + "\n")
        results["written"].append({"target": "meridian",
                                   "path": MERIDIAN_CONFIG, "bytes": size})
    except (RenderError, OSError) as exc:
        results["errors"].append(f"meridian: {exc}")

    havoc_tpl = _template("havoc.yaotl")
    if havoc_tpl is None:
        results["errors"].append(
            f"havoc: profile template not found (looked in {tdir})")
    else:
        try:
            size = _write(HAVOC_PROFILE, render_havoc_profile(havoc_tpl))
            results["written"].append({"target": "havoc",
                                       "path": HAVOC_PROFILE, "bytes": size})
        except (RenderError, OSError) as exc:
            results["errors"].append(f"havoc: {exc}")

    results["ok"] = not results["errors"]
    return results
