"""Application core: wires config, db, sessions, tasks and listeners together."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .db import Database
from .logging import setup as setup_logging
from .modules.registry import ModuleRegistry
from .sessions import SessionManager
from .tasks import TaskManager


class App:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(
            config.db_path,
            master_key=config.master_key,
            encrypt_results=config.store_results == "encrypted",
        )
        self.sessions = SessionManager(
            self.db, config.server_priv_b64, config.server_pub_b64
        )
        self.modules = ModuleRegistry()
        self.modules.bind(self)
        self.tasks = TaskManager(self.db, registry=self.modules)
        self._listeners: dict[str, object] = {}

    @classmethod
    def load(cls, state_dir: Path | None = None) -> App:
        cfg = Config.load(state_dir)
        setup_logging(cfg.state_dir)
        app = cls(cfg)
        app.modules.discover()
        return app

    @property
    def listeners(self) -> dict[str, object]:
        return self._listeners

    def start_listener(self, listener_cfg) -> None:
        from .listeners.manager import ListenerManager

        mgr = ListenerManager(self)
        if listener_cfg.name in self._listeners:
            raise ValueError(f"listener '{listener_cfg.name}' already running")
        handle = mgr.start(listener_cfg)
        self._listeners[listener_cfg.name] = handle

    def stop_listener(self, name: str) -> None:
        handle = self._listeners.pop(name, None)
        if handle is not None:
            handle.stop()

    def shutdown(self) -> None:
        for name in list(self._listeners):
            self.stop_listener(name)
