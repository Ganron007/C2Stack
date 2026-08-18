"""Listener manager: maps transport names to implementations."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("meridian.listener")


def build_listener(app: Any, cfg: Any):
    from .dns_listener import DnsListener
    from .http_listener import AioHttpListener

    if cfg.transport in ("http", "https"):
        return AioHttpListener(app, cfg)
    if cfg.transport in ("ws", "wss"):
        return AioHttpListener(app, cfg)
    if cfg.transport == "dns":
        return DnsListener(app, cfg)
    raise ValueError(f"unsupported transport: {cfg.transport}")


class ListenerHandle:
    def __init__(self, listener):
        self._listener = listener

    def stop(self) -> None:
        self._listener.stop()


class ListenerManager:
    def __init__(self, app: Any):
        self.app = app

    def start(self, cfg) -> ListenerHandle:
        listener = build_listener(self.app, cfg)
        listener.start()
        return ListenerHandle(listener)
