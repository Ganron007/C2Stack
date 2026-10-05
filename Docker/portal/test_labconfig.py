"""Tests for the portal-side lab configuration store.

These are the rules that stop a silently-wrong lab: unknown keys rejected,
ports validated, overrides surviving a reload, and env still working as the
fallback for anyone who never opens the UI.
"""
import io
import json
import os
import tempfile

import pytest

import labconfig


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch):
    """Each test gets its own config file so nothing leaks between them."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "lab-config.json")
        monkeypatch.setattr(labconfig, "CONFIG_PATH", path)
        monkeypatch.setattr(labconfig, "_overrides", {})
        monkeypatch.setattr(labconfig, "_observed", {})
        yield path


def test_defaults_when_nothing_set():
    assert labconfig.get("victim_redirector_ip") == "192.168.100.1"
    assert labconfig.get("c2_header_name") == "X-Request-ID"


def test_env_beats_default(monkeypatch):
    monkeypatch.setenv("VICTIM_REDIRECTOR_IP", "10.1.2.3")
    assert labconfig.get("victim_redirector_ip") == "10.1.2.3"


def test_compose_default_env_loses_to_discovery(monkeypatch):
    """Compose injects `:-default` into the portal's environment whether or not
    an operator chose the value. If those counted as deliberate, rendering a
    config would stamp the documented default over live state - e.g.
    MERIDIAN_DNS_DOMAIN defaults to c2.lab.local while the seeded volume uses
    c2.cadre.local, silently re-pointing the DNS C2 channel."""
    monkeypatch.setenv("MERIDIAN_DNS_DOMAIN",
                       labconfig.SETTINGS["meridian_dns_domain"]["default"])
    labconfig.observe("meridian_dns_domain", "c2.cadre.local")
    assert labconfig.get("meridian_dns_domain") == "c2.cadre.local"
    assert labconfig.snapshot()["meridian_dns_domain"]["source"] == "stack"


def test_explicit_env_still_beats_discovery(monkeypatch):
    monkeypatch.setenv("MERIDIAN_DNS_DOMAIN", "chosen.example.org")
    labconfig.observe("meridian_dns_domain", "c2.cadre.local")
    assert labconfig.get("meridian_dns_domain") == "chosen.example.org"
    assert labconfig.snapshot()["meridian_dns_domain"]["source"] == "env"


def test_override_beats_env(monkeypatch):
    monkeypatch.setenv("VICTIM_REDIRECTOR_IP", "10.1.2.3")
    applied, errors = labconfig.set_overrides({"victim_redirector_ip": "172.16.9.9"})
    assert errors == []
    assert applied["victim_redirector_ip"] == "172.16.9.9"
    assert labconfig.get("victim_redirector_ip") == "172.16.9.9"


def test_unknown_key_rejected():
    applied, errors = labconfig.set_overrides({"not_a_setting": "x"})
    assert applied == {}
    assert any("unknown setting" in e for e in errors)


def test_bad_port_rejected():
    applied, errors = labconfig.set_overrides({"havoc_ts_port": "notaport"})
    assert applied == {}
    assert any("port" in e for e in errors)


def test_out_of_range_port_rejected():
    applied, errors = labconfig.set_overrides({"havoc_ts_port": "70000"})
    assert applied == {}
    assert any("65535" in e for e in errors)


def test_prefix_must_start_with_slash():
    applied, errors = labconfig.set_overrides({"havoc_uri_prefix": "edge/cache"})
    assert applied == {}
    assert any("must start with" in e for e in errors)


def test_victim_ip_rejects_port():
    applied, errors = labconfig.set_overrides({"victim_redirector_ip": "10.0.0.1:80"})
    assert applied == {}
    assert any("no port" in e for e in errors)


def test_victim_ip_accepts_hostname():
    applied, errors = labconfig.set_overrides({"victim_redirector_ip": "c2.example.org"})
    assert errors == []
    assert labconfig.get("victim_redirector_ip") == "c2.example.org"


def test_newlines_rejected():
    applied, errors = labconfig.set_overrides({"c2_header_value": "a\nb"})
    assert applied == {}
    assert any("newline" in e for e in errors)


def test_persists_and_reloads():
    labconfig.set_overrides({"c2_header_value": "rotated-token"})
    labconfig.reload()  # simulates a fresh process reading the same file
    assert labconfig.get("c2_header_value") == "rotated-token"


def test_clear_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("C2_HEADER_VALUE", "from-env")
    labconfig.set_overrides({"c2_header_value": "from-ui"})
    assert labconfig.get("c2_header_value") == "from-ui"
    labconfig.clear("c2_header_value")
    assert labconfig.get("c2_header_value") == "from-env"


def test_observed_is_lowest_precedence(monkeypatch):
    """Discovery fills gaps; it must never beat intent."""
    monkeypatch.setenv("C2_HEADER_VALUE", "from-env")
    labconfig.observe("c2_header_value", "from-stack")
    assert labconfig.get("c2_header_value") == "from-env"


def test_observed_loses_to_override():
    labconfig.observe("c2_header_value", "from-stack")
    labconfig.set_overrides({"c2_header_value": "from-ui"})
    assert labconfig.get("c2_header_value") == "from-ui"
    labconfig.set_overrides({"c2_header_value": "from-ui"})
    assert labconfig.get("c2_header_value") == "from-ui"


def test_observed_used_when_nothing_else_set():
    """A lab whose DNS domain differs from the documented default must not get
    the default stamped over it by a config render."""
    labconfig.observe("meridian_dns_domain", "c2.cadre.local")
    assert labconfig.get("meridian_dns_domain") == "c2.cadre.local"
    snap = labconfig.snapshot()
    assert snap["meridian_dns_domain"]["source"] == "stack"
    assert snap["meridian_dns_domain"]["overridden"] is False


def test_forget_drops_discovered_value():
    """Clearing a setting must also drop what discovery learned, or the next
    render would re-learn the stale live value and 'reset' would do nothing."""
    labconfig.observe("havoc_uri_prefix", "/stale/path")
    assert labconfig.get("havoc_uri_prefix") == "/stale/path"
    labconfig.forget("havoc_uri_prefix")
    assert "havoc_uri_prefix" not in labconfig.observed()
    assert labconfig.get("havoc_uri_prefix") == (
        labconfig.SETTINGS["havoc_uri_prefix"]["default"])


def test_observed_ignores_unknown_and_blank():
    labconfig.observe("not_a_setting", "x")
    labconfig.observe("victim_redirector_ip", "   ")
    assert "not_a_setting" not in labconfig.observed()
    assert "victim_redirector_ip" not in labconfig.observed()


def test_snapshot_source_labels(monkeypatch):
    labconfig.observe("sliver_ctrl_port", "31337")
    labconfig.set_overrides({"havoc_ts_port": "40056"})
    snap = labconfig.snapshot()
    assert snap["sliver_ctrl_port"]["source"] == "stack"
    assert snap["havoc_ts_port"]["source"] == "override"
    # A compose `:-default` in the environment is not a deliberate choice.
    monkeypatch.setenv("MYTHIC_UI_PORT",
                       labconfig.SETTINGS["mythic_ui_port"]["default"])
    assert labconfig.snapshot()["mythic_ui_port"]["source"] == "default"


def test_corrupt_file_does_not_crash(monkeypatch, tmp_path):
    bad = tmp_path / "lab-config.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(labconfig, "CONFIG_PATH", str(bad))
    labconfig.reload()
    # Falls back to defaults rather than taking the lab down.
    assert labconfig.get("c2_header_value") == "cadre-c2"


def test_snapshot_reports_provenance():
    labconfig.set_overrides({"victim_redirector_ip": "10.9.9.9"})
    snap = labconfig.snapshot()
    assert snap["victim_redirector_ip"]["overridden"] is True
    assert snap["victim_redirector_ip"]["value"] == "10.9.9.9"
    assert snap["havoc_ts_port"]["overridden"] is False


def test_env_file_excludes_portal_scope():
    text = labconfig.render_env_file()
    # Stack-scope only: the portal persists its own values, so duplicating
    # them in .env would create two sources of truth.
    assert "HAVOC_TS_PORT=" in text
    assert "VICTIM_REDIRECTOR_IP=" not in text
    assert "VICTIM_SSH_TARGET=" not in text


def test_env_file_reflects_current_values():
    labconfig.set_overrides({"havoc_ts_port": "41000"})
    assert "HAVOC_TS_PORT=41000" in labconfig.render_env_file()


def test_atomic_write_leaves_no_temp_file():
    path = labconfig.CONFIG_PATH
    labconfig.set_overrides({"c2_header_name": "X-Lab"})
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")
    with io.open(path, encoding="utf-8") as fh:
        assert json.load(fh)["c2_header_name"] == "X-Lab"
