"""Offline tests for Adaptix operator credentials and TLS verification."""

import ssl

import pytest

import adaptix_client as ax


def test_adaptix_requires_deployment_password(monkeypatch):
    monkeypatch.delenv(ax.ADAPTIX_PASSWORD_ENV, raising=False)
    client = ax.AdaptixClient()
    assert client._ssl.verify_mode == ssl.CERT_REQUIRED
    assert client._ssl.check_hostname is True
    with pytest.raises(ax.BackendError, match=ax.ADAPTIX_PASSWORD_ENV):
        client.login()


def test_adaptix_login_uses_configured_teamserver_password(monkeypatch):
    monkeypatch.setenv(ax.ADAPTIX_PASSWORD_ENV, "deployment-secret")
    client = ax.AdaptixClient()
    captured = {}

    def fake_request(method, path, payload):
        captured.update({"method": method, "path": path, "payload": payload})
        return {"access_token": "test-token"}

    client._request = fake_request
    assert client.login() == "test-token"
    assert captured["payload"]["password"] == "deployment-secret"


def test_adaptix_configured_ca_path_is_passed_to_verifier(monkeypatch, tmp_path):
    ca_file = tmp_path / "adaptix.crt"
    ca_file.write_text("configured cert path", encoding="utf-8")
    captured = {}
    monkeypatch.setenv("ADAPTIX_CA_FILE", str(ca_file))
    monkeypatch.setattr(
        ax.ssl,
        "create_default_context",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    ax.AdaptixClient()
    assert captured["cafile"] == str(ca_file)


def test_adaptix_uses_isolated_trust_mount_by_default(monkeypatch):
    monkeypatch.delenv("ADAPTIX_CA_FILE", raising=False)
    captured = {}
    monkeypatch.setattr(ax.os.path, "isfile", lambda _path: True)
    monkeypatch.setattr(
        ax.ssl,
        "create_default_context",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    ax.AdaptixClient()

    assert captured["cafile"] == "/trust/adaptix/adaptix.crt"
