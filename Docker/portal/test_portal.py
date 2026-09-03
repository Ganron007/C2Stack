"""Test suite for C2Stack Flight Control Portal API and UI."""

import pytest
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)


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
        "domain_suffix": "c2.cadre.local",
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
        assert pkt["generated_query"].endswith(".c2.cadre.local")
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
    assert len(data["sessions"]) >= 1
