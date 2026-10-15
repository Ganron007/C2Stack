"""Real-browser checks for the standalone C2Stack portal UI."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright

from app import app


@contextmanager
def _serve_portal() -> Iterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=0,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started or not server.servers:
        server.should_exit = True
        thread.join(timeout=3)
        raise RuntimeError("C2Stack test portal did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def _browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        options = {"channel": "chrome"} if sys.platform == "win32" and not os.getenv("CI") else {}
        browser = playwright.chromium.launch(headless=True, **options)
        try:
            yield browser
        finally:
            browser.close()


def test_portal_csp_allows_app_and_blocks_inline_scripts_and_handlers(monkeypatch) -> None:
    monkeypatch.delenv("C2STACK_API_KEY", raising=False)
    monkeypatch.delenv("PORTAL_API_KEY", raising=False)
    hostile = '<img src=x onerror="window.__c2stackXss = 1">'
    task_requests: list[str] = []
    status_requests: list[str] = []

    with _serve_portal() as base_url, _browser() as browser:
        page: Page = browser.new_page()
        page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
        page.route("https://fonts.gstatic.com/**", lambda route: route.abort())

        def api_fixture(route) -> None:
            path = route.request.url.split("?", 1)[0]
            if path.endswith("/api/ops/task"):
                task_requests.append(path)
            if path.endswith("/api/status"):
                status_requests.append(path)
                payload = {
                    "services": {
                        name: {
                            "name": name,
                            "role": "test service",
                            "state": "running",
                            "ports": {},
                            "container": None,
                        }
                        for name in (
                            "redirector",
                            "meridian",
                            "sliver",
                            "havoc",
                            "adaptix",
                            "mythic",
                        )
                    },
                    "redirector_http_port": 80,
                    "c2_header": "X-Request-ID: test",
                    "victim_redirector_ip": "192.0.2.10",
                }
            elif path.endswith("/api/ops/sessions"):
                payload = {
                    "sessions": [
                        {
                            "id": hostile,
                            "backend": "havoc",
                            "hostname": hostile,
                            "username": "operator",
                            "process": "agent.exe",
                            "transport": "http",
                            "is_alive": True,
                        }
                    ],
                    "backends": {"havoc": {"ok": False, "error": hostile}},
                }
            elif path.endswith("/api/ops/catalogues"):
                payload = {"catalogues": {}}
            elif path.endswith("/api/config"):
                payload = {"settings": []}
            else:
                payload = {}
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
            )

        page.route("**/api/**", api_fixture)
        response = page.goto(f"{base_url}/", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        csp = response.headers.get("content-security-policy", "")
        script_policy = next(
            (directive for directive in csp.split(";") if directive.strip().startswith("script-src ")),
            "",
        )
        assert script_policy.strip() == "script-src 'self'"
        assert page.locator("[onclick]").count() == 0

        page.evaluate(
            """() => {
              window.__c2stackXss = 0;
              const script = document.createElement('script');
              script.textContent = 'window.__c2stackXss = 2';
              document.body.appendChild(script);
              const probe = document.createElement('button');
              probe.setAttribute('onclick', 'window.__c2stackXss = 3');
              document.body.appendChild(probe);
              probe.click();
            }"""
        )
        assert page.evaluate("window.__c2stackXss") == 0

        page.locator("#sync-docker-status-btn").click()
        page.wait_for_timeout(50)
        assert len(status_requests) >= 2

        page.locator('.tab-btn[data-tab="tab-ops"]').click()
        row = page.locator("#ops-tbody tr").first
        row.wait_for(state="visible")
        assert hostile in (row.text_content() or "")
        assert page.locator("#ops-tbody img, #backend-chips img").count() == 0
        assert page.locator("#backend-chips").get_by_text(hostile, exact=False).count() > 0
        assert page.evaluate("window.__c2stackXss") == 0
        assert not task_requests
        page.close()
