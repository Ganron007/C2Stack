"""Test suite for C2Stack Flight Control Portal API and UI."""

import pytest
from fastapi.testclient import TestClient

import labconfig
import c2backends as cb
from app import _havoc_fields, _vhost_fields, app

client = TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _isolated_labconfig(monkeypatch, tmp_path):
    """Config API tests must not touch the real store."""
    monkeypatch.setattr(labconfig, "CONFIG_PATH",
                        str(tmp_path / "lab-config.json"))
    monkeypatch.setattr(labconfig, "_overrides", {})
    monkeypatch.setattr(labconfig, "_observed", {})
    yield


def test_index_html_serving():
    """Verify root path serves index.html."""
    response = client.get("/")
    assert response.status_code == 200
    assert "C2STACK" in response.text
    assert "FLIGHT CONTROL" in response.text
    assert "OPSEC Redirector Visualizer" in response.text


def test_static_assets():
    """Verify static CSS and JS are reachable."""
    css_resp = client.get("/static/portal.css")
    assert css_resp.status_code == 200
    assert "--crimson-base" in css_resp.text

    js_resp = client.get("/static/portal.js")
    assert js_resp.status_code == 200
    assert "initRedirectorVisualizer" in js_resp.text


def test_api_status():
    """Verify /api/status returns all 6 services with metadata."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "services" in data
    services = data["services"]
    for svc in ["redirector", "meridian", "sliver", "havoc", "adaptix", "mythic"]:
        assert svc in services
        assert "ports" in services[svc]
        assert "state" in services[svc]


def test_redirector_simulation_valid():
    """Verify valid header forwards to C2 backend."""
    payload = {
        "url_path": "/gateway/v1/telemetry",
        "headers": {"X-Request-ID": "cadre-c2"},
        "method": "POST",
    }
    response = client.post("/api/redirector/test", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["opsec_shielded"] is False
    assert "MERIDIAN" in data["routed_to"]
    assert len(data["trace"]) == 3
    assert data["trace"][1]["status"] == "header_verified"
    assert data["trace"][2]["status"] == "c2_forwarded"


def test_redirector_simulation_shielded():
    """Verify missing header diverts to CloudEdge CDN Decoy."""
    payload = {
        "url_path": "/gateway/v1/telemetry",
        "headers": {"User-Agent": "Shodan Scanner"},
        "method": "GET",
    }
    response = client.post("/api/redirector/test", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["opsec_shielded"] is True
    assert "Decoy" in data["routed_to"]
    assert data["trace"][1]["status"] == "shield_divert"
    assert data["trace"][2]["status"] == "decoy_served"


def test_redirector_simulation_prefix_mismatch():
    """Verify valid header with unknown URI returns 404 mismatch."""
    payload = {
        "url_path": "/unknown/secret/path",
        "headers": {"X-Request-ID": "cadre-c2"},
        "method": "POST",
    }
    response = client.post("/api/redirector/test", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["http_status"] == 404
    assert data["trace"][2]["status"] == "not_found"


def test_dns_dissector():
    """Verify Meridian Base32 DNS TXT chunking and RFC 1035 compliance."""
    payload = {
        "payload_text": "whoami /priv && net user",
        "session_id": "B8E101",
        "domain_suffix": "c2.lab.local",
    }
    response = client.post("/api/dns/dissect", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["byte_length"] > 0
    assert data["total_packets"] >= 1
    assert len(data["packets"]) == data["total_packets"]

    for pkt in data["packets"]:
        assert pkt["label_safe"] is True
        assert pkt["chunk_len"] <= 36
        assert pkt["generated_query"].endswith(".c2.lab.local")
        assert "b8e101" in pkt["generated_query"].lower()



def test_payload_studio():
    """Verify payload studio provides stagers and detection profiles for all 5 frameworks."""
    response = client.get("/api/payloads")
    assert response.status_code == 200
    data = response.json()
    for fw in ["meridian", "sliver", "havoc", "adaptix", "mythic"]:
        assert fw in data
        assert "stagers" in data[fw]
        assert "detection" in data[fw]
        assert len(data[fw]["stagers"]) >= 1


def test_fleet_sessions():
    """Verify fleet radar returns session list."""
    response = client.get("/api/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)
VHOST_SAMPLE = """<VirtualHost *:80>
    RewriteCond %{REQUEST_URI} ^/cdn/media/stream(/|$)
    RewriteCond %{HTTP:X-Lab} ^tok-1$ [NC]
    RewriteRule ^(.*)$ http://mythic_http:80$1 [P,L]
    RewriteCond %{REQUEST_URI} ^/media/uploads(/|$)
    RewriteRule ^(.*)$ http://mythic_httpx:82$1 [P,L]
    RewriteCond %{REQUEST_URI} ^/s-prefix(/|$)
    RewriteCond %{REQUEST_URI} ^/
    RewriteRule ^(.*)$ http://sliver:80$1 [P,L]
    RewriteCond %{REQUEST_URI} ^/h-prefix(/|$)
    RewriteCond %{HTTP:X-Lab} ^tok-1$ [NC]
    RewriteRule ^(.*)$ http://havoc:80$1 [P,L]
    RewriteCond %{REQUEST_URI} ^/a-prefix(/|$)
    RewriteRule ^(.*)$ http://adaptix:80$1 [P,L]
    RewriteCond %{REQUEST_URI} ^/m-prefix(/|$)
    RewriteRule ^(.*)$ http://meridian:8080$1 [P,L]
</VirtualHost>
"""

HAVOC_SAMPLE = """Teamserver {
    Host = "0.0.0.0"
    Port = 40056
}
Listeners {
    Http {
        Hosts = [ "10.9.8.7" ]
        PortBind = 80
        PortConn = 80
        Uris = [ "/probe/path/" ]
        Headers = [ "X-Lab: tok-1" ]
    }
}
"""


def test_havoc_fields_parse():
    fields = _havoc_fields(HAVOC_SAMPLE)
    assert fields == {
        "victim_redirector_ip": "10.9.8.7",
        "redirector_http_port": "80",
        "havoc_uri_prefix": "/probe/path",
        "c2_header_name": "X-Lab",
        "c2_header_value": "tok-1",
    }


def test_vhost_fields_parse_positionally():
    fields = _vhost_fields(VHOST_SAMPLE)
    assert fields["mythic_uri_prefix"] == "/cdn/media/stream"
    assert fields["sliver_uri_prefix"] == "/s-prefix"
    assert fields["havoc_uri_prefix"] == "/h-prefix"
    assert fields["adaptix_uri_prefix"] == "/a-prefix"
    assert fields["meridian_uri_prefix"] == "/m-prefix"
    assert fields["c2_header_name"] == "X-Lab"
    assert fields["c2_header_value"] == "tok-1"


def test_config_get_schema():
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    for key in ("victim_redirector_ip", "havoc_uri_prefix",
                "c2_header_value", "meridian_dns_domain"):
        assert key in data["settings"]
        assert "source" in data["settings"][key]


def test_config_rejects_unknown_and_bad_values():
    r = client.post("/api/config", json={"values": {"bogus": "x"}})
    assert r.status_code == 400
    r = client.post("/api/config", json={"values": {"havoc_ts_port": "abc"}})
    assert r.status_code == 400
    r = client.post("/api/config",
                    json={"values": {"victim_redirector_ip": "1.2.3.4:80"}})
    assert r.status_code == 400


def test_config_roundtrip_and_clear():
    r = client.post("/api/config",
                    json={"values": {"c2_header_value": "roundtrip-1"}})
    assert r.status_code == 200
    assert client.get("/api/config").json(
    )["settings"]["c2_header_value"]["value"] == "roundtrip-1"
    r = client.post("/api/config",
                    json={"values": {}, "clear": ["c2_header_value"]})
    assert r.status_code == 200
    assert "c2_header_value" in r.json()["cleared"]
    # Clearing also forgets discovery: the value must fall back, not stick.
    assert client.get("/api/config").json(
    )["settings"]["c2_header_value"]["source"] in ("default", "env")


def test_config_env_file_excludes_portal_scope():
    r = client.get("/api/config/env-file")
    assert r.status_code == 200
    assert "HAVOC_TS_PORT=" in r.text
    assert "VICTIM_REDIRECTOR_IP=" not in r.text


def test_rendered_endpoint_reports_missing_gracefully():
    r = client.get("/api/config/rendered")
    assert r.status_code == 200
    assert set(r.json()["rendered"]) == {"redirector", "meridian", "havoc"}


def test_container_action_unavailable_when_missing(monkeypatch):
    monkeypatch.setattr("app.get_docker_containers", lambda: [])
    r = client.post("/api/containers/sliver/action?action=restart")
    assert r.status_code == 404
    assert "no unambiguous container" in r.json()["detail"].lower()


def test_container_selection_by_compose_labels():
    from app import _find_service_container
    mock_containers = [
        {
            "Id": "other_id",
            "Names": ["/other-project-sliver-1"],
            "Labels": {
                "com.docker.compose.project": "other-project",
                "com.docker.compose.service": "sliver",
            },
        },
        {
            "Id": "c2stack_id",
            "Names": ["/c2stack-sliver-1"],
            "Labels": {
                "com.docker.compose.project": "c2stack",
                "com.docker.compose.service": "sliver",
            },
        },
    ]
    # Finding sliver for default c2stack project must match c2stack_id, not other_id
    matched = _find_service_container(mock_containers, "sliver", project_name="c2stack")
    assert matched is not None
    assert matched["Id"] == "c2stack_id"
    assert _find_service_container(mock_containers[:1], "sliver", project_name="c2stack") is None
    duplicate = mock_containers[1] | {"Id": "second_owned", "Names": ["/c2stack-sliver-2"]}
    assert _find_service_container(
        mock_containers[1:] + [duplicate], "sliver", project_name="c2stack"
    ) is None


def test_operator_auth_enforcement(monkeypatch):
    monkeypatch.setenv("C2STACK_API_KEY", "secret-test-key-123")

    dispatched = []
    monkeypatch.setattr(
        "havoc_client.HavocClient",
        lambda: dispatched.append("havoc") or None,
    )

    # Unauthenticated mutating request must fail with 401
    r = client.post("/api/config", json={"values": {"c2_header_value": "tok"}})
    assert r.status_code == 401
    assert "unauthorized" in r.json()["detail"].lower()

    # Unauthenticated probe request must fail with 401
    r_probe = client.post("/api/ops/probe", json={"url_path": "/", "headers": {}, "method": "GET"})
    assert r_probe.status_code == 401
    assert "unauthorized" in r_probe.json()["detail"].lower()

    r_task = client.post(
        "/api/ops/task",
        json={"backend": "havoc", "session_id": "demo", "command": "whoami"},
    )
    assert r_task.status_code == 401
    assert dispatched == []

    # Tampered / mismatched key must fail with 401
    r_bad = client.post(
        "/api/config",
        json={"values": {"c2_header_value": "tok"}},
        headers={"X-API-Key": "wrong-key"},
    )
    assert r_bad.status_code == 401

    # Valid X-API-Key must succeed
    r = client.post(
        "/api/config",
        json={"values": {"c2_header_value": "tok"}},
        headers={"X-API-Key": "secret-test-key-123"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Valid Bearer token must succeed.
    r = client.post(
        "/api/config",
        json={"values": {"c2_header_value": "tok"}},
        headers={"Authorization": "Bearer secret-test-key-123"},
    )
    assert r.status_code == 200


def test_keyless_portal_is_loopback_only(monkeypatch):
    monkeypatch.delenv("C2STACK_API_KEY", raising=False)
    monkeypatch.delenv("PORTAL_API_KEY", raising=False)
    remote = TestClient(app, client=("192.0.2.44", 50000))
    assert remote.get("/api/status").status_code == 401
    assert client.get("/api/status").status_code == 200


def test_mythic_client_fails_closed_without_configured_password(monkeypatch):
    monkeypatch.setattr(cb, "MYTHIC_PASS", "")

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Mythic network request made without credentials")

    monkeypatch.setattr(cb.urllib.request, "urlopen", unexpected_request)
    with pytest.raises(cb.BackendError, match="MYTHIC_PASSWORD is required"):
        cb.MythicClient()._login()


def test_deprecated_havoc_get_never_dispatches(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET task route dispatched")

    monkeypatch.setattr("havoc_client.HavocClient", forbidden)
    response = client.get(
        "/api/ops/havoc/task", params={"demon_id": "demo1", "command": "whoami"}
    )
    assert response.status_code == 405


def test_havoc_task_post_route():
    # POST /api/ops/havoc/task requires demon_id and command
    r = client.post("/api/ops/havoc/task", json={"demon_id": "demo1", "command": "whoami", "wait": 1})
    # Since teamserver is not running live, expect 502, not a missing POST route.
    assert r.status_code == 502


def test_adaptix_task_route_returns_exact_correlation_id(monkeypatch):
    monkeypatch.delenv("C2STACK_API_KEY", raising=False)
    monkeypatch.delenv("PORTAL_API_KEY", raising=False)

    class FakeAdaptixClient:
        def login(self):
            return "token"

        def agent_command_raw(self, _agent_id, _command):
            return {"ok": True, "task_id": "task-41"}

        def completed_tasks(self, _agent_id, limit=10):
            raise AssertionError("task acceptance response must not depend on result polling")

    monkeypatch.setattr("adaptix_client.AdaptixClient", FakeAdaptixClient)
    response = client.post(
        "/api/ops/task",
        json={"backend": "adaptix", "session_id": "agent-1", "command": "shell whoami"},
    )
    assert response.status_code == 200
    assert response.json()["task_id"] == "task-41"
    assert response.json()["result"]["task_id"] == "task-41"


def test_accepted_mythic_task_is_not_failed_by_result_poll_error(monkeypatch):
    monkeypatch.delenv("C2STACK_API_KEY", raising=False)
    monkeypatch.delenv("PORTAL_API_KEY", raising=False)

    class FakeMythicClient:
        def post(self, _path, _payload):
            return {"id": 901, "status": "submitted"}

    def fail_if_polled(_task_id):
        raise cb.BackendError("temporary Mythic output database failure")

    monkeypatch.setattr(cb, "MythicClient", FakeMythicClient)
    monkeypatch.setattr(cb, "mythic_task_output", fail_if_polled)
    response = client.post(
        "/api/ops/task",
        json={"backend": "mythic", "session_id": "17", "command": "whoami", "wait": 1},
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == 901
    assert response.json()["result"]["status"] == "submitted"
