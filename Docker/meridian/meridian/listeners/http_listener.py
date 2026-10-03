"""HTTP/HTTPS/WebSocket listener (aiohttp).

Serves:
    POST /api/v1/kex        plaintext KEX handshake
    POST /api/v1/checkin    AEAD envelope checkin, replies with pending tasks
    WS   /api/v1/ws         persistent channel (KEX first frame, then envelopes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl

import aiohttp
from aiohttp import web

from .base import Listener

log = logging.getLogger("meridian.listener")

MAX_BODY = 4 * 1024 * 1024

#: Cap on concurrent in-flight HTTP requests (finding #30). Past this the
#: listener answers 503 instead of queueing unbounded work.
MAX_HTTP_INFLIGHT = 128


class InflightCap:
    """Bounds concurrent requests; testable without a server.

    The middleware is a bound method (not the instance itself): aiohttp only
    recognises plain async functions / bound methods as new-style middleware,
    and an undecorated instance trips the old-style deprecation path.
    """

    def __init__(self, limit: int = MAX_HTTP_INFLIGHT):
        self.limit = limit
        self._lock = asyncio.Lock()
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    @web.middleware
    async def middleware(self, request: web.Request, handler) -> web.StreamResponse:
        async with self._lock:
            if self._inflight >= self.limit:
                return web.Response(status=503, text="busy")
            self._inflight += 1
        try:
            return await handler(request)
        finally:
            async with self._lock:
                self._inflight -= 1


class AioHttpListener(Listener):
    name = "http"
    transport = "http"  # http | https | ws | wss

    def __init__(self, app, cfg):
        super().__init__(app, cfg)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cap = InflightCap()

    def _shutdown(self) -> None:
        # Finding #11: stopping used to abandon the loop (run_forever never
        # returned) so the thread and the bound socket leaked — a stop/start
        # cycle ended in a bind failure. Now: clean the runner up, then stop
        # the loop. base.stop() joins the thread afterwards.
        if self._loop and not self._loop.is_closed():
            fut = asyncio.run_coroutine_threadsafe(self._close_runner(), self._loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _close_runner(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    # ------------------------------------------------------------- endpoints
    def _header_gate(self, request: web.Request) -> bool:
        """Backend header check (finding #3).

        The redirector gates on this header too, but anything that can reach
        the backend:H8080 directly (container network, published ports)
        bypassed it entirely. 404 — not 403 — so a scanner learns nothing
        about whether a listener lives here.
        """
        name = self.cfg.expect_header_name
        value = self.cfg.expect_header_value
        if not name or not value:
            return True
        return request.headers.get(name) == value

    def _reject(self, request: web.Request, exc: Exception) -> web.Response:
        # Finding #18: rejections were debug-only and carried no client info,
        # so scanning and crypto failures were invisible. Log at warning with
        # the remote and path — never the body, which can carry key material.
        log.warning(
            "listener %s rejected %s %s from %s: %s",
            self.name, request.method, request.path, request.remote, exc,
            extra={"event": "listener_reject", "listener": self.name,
                   "ip": request.remote},
        )
        return web.Response(status=400, text="bad request")

    async def on_kex(self, request: web.Request) -> web.Response:
        if not self._header_gate(request):
            return web.Response(status=404, text="not found")
        try:
            body = await request.read()
            reply = self.handle_kex(body)
            return web.json_response(reply)
        except Exception as exc:
            return self._reject(request, exc)

    async def on_checkin(self, request: web.Request) -> web.Response:
        if not self._header_gate(request):
            return web.Response(status=404, text="not found")
        try:
            body = json.loads(await request.read())
            session_id = body.get("session_id", "")
            payload = self.open_envelope(session_id, body)
            reply = self.handle_checkin(session_id, payload)
            return web.json_response(self.seal_reply(session_id, reply))
        except Exception as exc:
            return self._reject(request, exc)

    async def on_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._header_gate(request):
            return web.Response(status=404, text="not found")
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        session_id: str | None = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT and msg.type != aiohttp.WSMsgType.BINARY:
                    continue
                data = msg.data if isinstance(msg.data, bytes) else msg.data.encode()
                if session_id is None:
                    reply = self.handle_kex(data)
                    session_id = reply["session_id"]
                    await ws.send_json(reply)
                    continue
                env = json.loads(data)
                payload = self.open_envelope(session_id, env)
                if payload.get("type") == "checkin":
                    reply = self.handle_checkin(session_id, payload)
                    await ws.send_bytes(
                        json.dumps(self.seal_reply(session_id, reply)).encode()
                    )
        except Exception as exc:
            self._reject(request, exc)
        finally:
            await ws.close()
        return ws

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY, middlewares=[self._cap.middleware])
        prefix = (self.cfg.uri_prefix or "").rstrip("/")
        routes = [("/api/v1/kex", self.on_kex), ("/api/v1/checkin", self.on_checkin)]
        app.router.add_get("/api/v1/ws", self.on_ws)
        for path, handler in routes:
            app.router.add_post(path, handler)
            if prefix:
                app.router.add_post(prefix + path, handler)
        if prefix:
            # Finding #12: the WS route was missing under the prefix, so an
            # implant configured for the redirector could KEX but never open
            # the persistent channel.
            app.router.add_get(prefix + "/api/v1/ws", self.on_ws)
        return app

    # ------------------------------------------------------------------ run
    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.transport not in ("https", "wss"):
            return None
        if not (self.cfg.cert and self.cfg.key):
            raise RuntimeError(f"listener '{self.name}': cert/key required for {self.transport}")
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cfg.cert, self.cfg.key)
        if self.cfg.mTLS:
            if not self.cfg.ca:
                raise RuntimeError(f"listener '{self.name}': mTLS requires a CA bundle")
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(self.cfg.ca)
        return ctx

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        app = self._build_app()
        # Finding #18: access_log was None, so there was no record of who
        # talked to the listener at all.
        self._runner = web.AppRunner(
            app, access_log=logging.getLogger("meridian.access")
        )
        self._loop.run_until_complete(self._runner.setup())
        self._site = web.TCPSite(
            self._runner, self.cfg.host, self.cfg.port, ssl_context=self._ssl_context()
        )
        self._loop.run_until_complete(self._site.start())
        self._loop.run_forever()
