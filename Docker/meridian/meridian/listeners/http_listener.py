"""HTTP/HTTPS/WebSocket listener (aiohttp).

Serves:
    POST /api/v1/kex        plaintext KEX handshake
    POST /api/v1/checkin    AEAD envelope checkin, replies with pending tasks
    WS   /api/v1/ws         persistent channel (KEX first frame, then envelopes)
"""

from __future__ import annotations

import asyncio
import json
import ssl

import aiohttp
from aiohttp import web

from .base import Listener

MAX_BODY = 4 * 1024 * 1024


class AioHttpListener(Listener):
    name = "http"
    transport = "http"  # http | https | ws | wss

    def __init__(self, app, cfg):
        super().__init__(app, cfg)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _shutdown(self) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._close_runner(), self._loop)

    async def _close_runner(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    # ------------------------------------------------------------- endpoints
    async def on_kex(self, request: web.Request) -> web.Response:
        try:
            body = await request.read()
            reply = self.handle_kex(body)
            return web.json_response(reply)
        except Exception as exc:
            log_abort(exc, self.name)
            return web.Response(status=400, text="bad request")

    async def on_checkin(self, request: web.Request) -> web.Response:
        try:
            body = json.loads(await request.read())
            session_id = body.get("session_id", "")
            payload = self.open_envelope(session_id, body)
            reply = self.handle_checkin(session_id, payload)
            return web.json_response(self.seal_reply(session_id, reply))
        except Exception as exc:
            log_abort(exc, self.name)
            return web.Response(status=400, text="bad request")

    async def on_ws(self, request: web.Request) -> web.WebSocketResponse:
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
            log_abort(exc, self.name)
        finally:
            await ws.close()
        return ws

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY)
        app.router.add_post("/api/v1/kex", self.on_kex)
        app.router.add_post("/api/v1/checkin", self.on_checkin)
        app.router.add_get("/api/v1/ws", self.on_ws)
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
        self._runner = web.AppRunner(app, access_log=None)
        self._loop.run_until_complete(self._runner.setup())
        self._site = web.TCPSite(
            self._runner, self.cfg.host, self.cfg.port, ssl_context=self._ssl_context()
        )
        self._loop.run_until_complete(self._site.start())
        self._loop.run_forever()


def log_abort(exc: Exception, listener: str) -> None:
    import logging

    logging.getLogger("meridian.listener").debug(
        "listener %s rejected a request: %s", listener, exc
    )
