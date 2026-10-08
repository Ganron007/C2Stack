"""Tests for the config renderer that feeds the sibling containers.

These cover the failure modes that cost real time: a route that never matches
because a variable was left unsubstituted (every request falls through to the
decoy with no error), and a stale value in a render that silently disagrees
with the settings the operator just saved.
"""
import io
import json
import os
import tempfile

import pytest

import lagrender
import labconfig


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(labconfig, "CONFIG_PATH",
                            os.path.join(tmp, "lab-config.json"))
        monkeypatch.setattr(labconfig, "_overrides", {})
        monkeypatch.setattr(lagrender, "RENDER_DIR", os.path.join(tmp, "render"))
        # Backend coordinates come from the environment; pin them so the tests
        # assert on the template substitution, not on ambient env state.
        for key, value in (("MYTHIC_BACKEND_HOST", "mythic_http"),
                           ("MYTHIC_BACKEND_PORT", "80"),
                           ("MYTHIC_HTTPX_BACKEND_HOST", "mythic_httpx"),
                           ("MYTHIC_HTTPX_BACKEND_PORT", "82"),
                           ("SLIVER_BACKEND_HOST", "sliver"),
                           ("SLIVER_BACKEND_PORT", "80"),
                           ("HAVOC_BACKEND_HOST", "havoc"),
                           ("HAVOC_BACKEND_PORT", "80"),
                           ("ADAPTIX_BACKEND_HOST", "adaptix"),
                           ("ADAPTIX_BACKEND_PORT", "80"),
                           ("MERIDIAN_BACKEND_HOST", "meridian"),
                           ("MERIDIAN_BACKEND_PORT", "8080"),
                           ("MYTHIC_HTTPX_URI_PREFIX", "/media/uploads"),
                           ("SLIVER_HEADER_GATE", "on")):
            monkeypatch.setenv(key, value)
        yield


VHOST = """<VirtualHost *:80>
    CustomLog ${APACHE_LOG_DIR}/access.log combined
    RewriteCond %{REQUEST_URI} ^${MYTHIC_URI_PREFIX}(/|$)
    RewriteCond %{HTTP:${C2_HEADER_NAME}} ^${C2_HEADER_VALUE}$ [NC]
    RewriteRule ^(.*)$ http://${MYTHIC_BACKEND_HOST}:${MYTHIC_BACKEND_PORT}$1 [P,L]
    RewriteCond %{REQUEST_URI} ^${MYTHIC_HTTPX_URI_PREFIX}(/|$)
    RewriteRule ^(.*)$ http://${MYTHIC_HTTPX_BACKEND_HOST}:${MYTHIC_HTTPX_BACKEND_PORT}$1 [P,L]
    RewriteCond %{REQUEST_URI} ^${SLIVER_URI_PREFIX}(/|$)
    RewriteCond ${SLIVER_HEADER_GATE}
    RewriteRule ^(.*)$ http://${SLIVER_BACKEND_HOST}:${SLIVER_BACKEND_PORT}$1 [P,L]
    RewriteCond %{REQUEST_URI} ^${HAVOC_URI_PREFIX}(/|$)
    RewriteRule ^(.*)$ http://${HAVOC_BACKEND_HOST}:${HAVOC_BACKEND_PORT}$1 [P,L]
    RewriteCond %{REQUEST_URI} ^${ADAPTIX_URI_PREFIX}(/|$)
    RewriteRule ^(.*)$ http://${ADAPTIX_BACKEND_HOST}:${ADAPTIX_BACKEND_PORT}$1 [P,L]
    RewriteCond %{REQUEST_URI} ^${MERIDIAN_URI_PREFIX}(/|$)
    RewriteRule ^(.*)$ http://${MERIDIAN_BACKEND_HOST}:${MERIDIAN_BACKEND_PORT}$1 [P,L]
</VirtualHost>
"""

HAVOC = """Teamserver {
    Port = __HAVOC_TS_PORT__
}
Listeners {
    Http {
        Hosts = [ "__VICTIM_REDIRECTOR_IP__" ]
        PortBind = __REDIRECTOR_HTTP_PORT__
        PortConn = __REDIRECTOR_HTTP_PORT__
        Uris = [ "__HAVOC_URI_PREFIX__/" ]
        Headers = [ "__C2_HEADER_NAME__: __C2_HEADER_VALUE__" ]
    }
}
"""


def test_redirector_substitutes_every_reference():
    out = lagrender.render_redirector(VHOST)
    assert "${APACHE_LOG_DIR}" in out          # Apache's own var, expected
    for leftover in ("${C2_HEADER_NAME}", "${MYTHIC_URI_PREFIX}",
                     "${MYTHIC_BACKEND_HOST}", "${SLIVER_HEADER_GATE}"):
        assert leftover not in out, leftover
    assert "${MYTHIC_HTTPX_URI_PREFIX}" not in out


def test_redirector_raises_on_unsubstituted_reference():
    """The httpx route went missing once because a var was left in the
    template; the render must refuse rather than emit a dead route."""
    with pytest.raises(lagrender.RenderError) as exc:
        lagrender.render_redirector(VHOST + "\nRewriteCond %{REQUEST_URI} "
                                    "^${NEVER_LISTED_VAR}(/|$)\n")
    assert "NEVER_LISTED_VAR" in str(exc.value)


def test_redirector_reflects_configured_prefixes():
    labconfig.set_overrides({"havoc_uri_prefix": "/cfgtest/path"})
    out = lagrender.render_redirector(VHOST)
    assert "^/cfgtest/path(/|$)" in out
    assert "/edge/cache/assets" not in out


def test_redirector_reflects_configured_header():
    labconfig.set_overrides({"c2_header_name": "X-Lab",
                             "c2_header_value": "tok-42"})
    out = lagrender.render_redirector(VHOST)
    assert "%{HTTP:X-Lab} ^tok-42$ [NC]" in out


def test_header_value_with_regex_and_dollar_chars():
    """Literal $ and \\ in a header value are escaped for Apache RewriteCond
    so they retain literal meaning rather than regex behavior."""
    labconfig.set_overrides({"c2_header_value": "a$b\\c"})
    out = lagrender.render_redirector(VHOST)
    assert r"^a\$b\\c$ [NC]" in out


def test_header_value_escapes_literal_dots():
    """Dots in header values must be escaped so they don't match arbitrary chars in Apache."""
    labconfig.set_overrides({"c2_header_value": "literal.review"})
    out = lagrender.render_redirector(VHOST)
    assert r"^literal\.review$ [NC]" in out


def _sliver_gate_line(rendered: str) -> str:
    """The Sliver route's gate condition, in isolation.

    Located via the Sliver RewriteRule's backend target and walking back to the
    last RewriteCond above it, so an assertion about the gate cannot be
    satisfied by another route's header check.
    """
    lines = [ln.strip() for ln in rendered.split("\n")]
    rule_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("RewriteRule") and "http://sliver:" in ln:
            rule_idx = i
            break
    if rule_idx is None:
        raise AssertionError("sliver RewriteRule not found")
    for i in range(rule_idx - 1, -1, -1):
        if lines[i].startswith("RewriteCond"):
            return lines[i]
    raise AssertionError("no RewriteCond above the sliver RewriteRule")


def test_sliver_gate_off_is_always_true():
    """Sliver sends no headers; the gate must never be a condition that can
    fail, or every Sliver request silently falls through to the decoy."""
    original = os.environ["SLIVER_HEADER_GATE"]
    os.environ["SLIVER_HEADER_GATE"] = "off"
    try:
        gate = _sliver_gate_line(lagrender.render_redirector(VHOST))
        assert gate == "RewriteCond %{REQUEST_URI} ^/"
        assert "%{HTTP:" not in gate
    finally:
        os.environ["SLIVER_HEADER_GATE"] = original


def test_sliver_gate_on_is_also_always_true():
    """The ON branch must also be satisfiable. A previous version tested for a
    header no Sliver request carries, which made the route match NOTHING while
    looking correctly configured."""
    out = lagrender.render_redirector(VHOST)
    gate = _sliver_gate_line(out)
    assert gate.startswith("RewriteCond %{HTTP:")
    assert "[NC]" in gate
    # Any REQUEST_URI starts with /, so an anchored ^/ condition is trivially
    # true; assert the gate does not require a header that cannot be sent.
    assert "%{REQUEST_URI}" in out


def test_meridian_config_carries_configured_domain():
    labconfig.set_overrides({"meridian_dns_domain": "c3.example.org"})
    cfg = lagrender.render_meridian()
    domains = {l["domain"] for l in cfg["listeners"]}
    assert domains == {"c3.example.org"}
    assert {l["transport"] for l in cfg["listeners"]} == {"http", "dns"}


def test_havoc_profile_substitutes_and_reflects_config():
    labconfig.set_overrides({"victim_redirector_ip": "10.9.8.7",
                             "havoc_ts_port": "45500",
                             "havoc_uri_prefix": "/probe/path"})
    out = lagrender.render_havoc_profile(HAVOC)
    assert "10.9.8.7" in out
    assert "Port = 45500" in out
    assert '"/probe/path/"' in out
    assert "__" not in out.replace("__C2", "")  # no placeholders survive


def test_havoc_raises_on_surviving_placeholder():
    with pytest.raises(lagrender.RenderError):
        lagrender.render_havoc_profile(HAVOC + "\nExtra = __SOME_NEW_TOKEN__\n")


def test_render_all_writes_all_three_and_reports_bytes():
    templates = {"c2stack.conf.template": VHOST, "havoc.yaotl": HAVOC}
    result = lagrender.render_all(templates)
    assert result["ok"], result["errors"]
    targets = {w["target"] for w in result["written"]}
    assert targets == {"redirector", "meridian", "havoc"}
    for w in result["written"]:
        assert w["bytes"] > 0
        assert os.path.exists(w["path"])


def test_render_all_reports_missing_template_without_aborting():
    result = lagrender.render_all({})   # no templates available at all
    assert result["ok"] is False
    assert any("redirector" in e for e in result["errors"])
    # Meridian needs no template, so it must still be written.
    assert any(w["target"] == "meridian" for w in result["written"])


def test_render_all_is_idempotent():
    templates = {"c2stack.conf.template": VHOST, "havoc.yaotl": HAVOC}
    first = lagrender.render_all(templates)
    a = io.open(lagrender.REDIRECTOR_CONF, encoding="utf-8").read()
    second = lagrender.render_all(templates)
    b = io.open(lagrender.REDIRECTOR_CONF, encoding="utf-8").read()
    assert first["written"] == second["written"]
    assert a == b


def test_rendered_meridian_file_is_valid_json():
    lagrender.render_all({"havoc.yaotl": HAVOC})
    with io.open(lagrender.MERIDIAN_CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert len(cfg["listeners"]) == 2


def test_render_leaves_no_temp_files():
    lagrender.render_all({"c2stack.conf.template": VHOST, "havoc.yaotl": HAVOC})
    for path in (lagrender.REDIRECTOR_CONF, lagrender.MERIDIAN_CONFIG,
                 lagrender.HAVOC_PROFILE):
        assert not os.path.exists(path + ".tmp")
