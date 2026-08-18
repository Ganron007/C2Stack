"""Module registry and discovery."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import ServerModule

log = logging.getLogger("meridian.module")


class ModuleRegistry:
    """Holds server-side modules and dispatches result hooks."""

    def __init__(self):
        self._modules: dict[str, ServerModule] = {}
        self.app = None

    def bind(self, app) -> None:
        self.app = app

    def register(self, module: ServerModule) -> None:
        module.app = self.app or module.app
        if not module.name:
            raise ValueError("module must define a name")
        self._modules[module.name] = module
        log.debug("registered module %s", module.name)

    def discover(self, package: str = "meridian.modules.serverside") -> int:
        try:
            pkg = importlib.import_module(package)
        except ModuleNotFoundError:
            log.warning("module package %s not found", package)
            return 0
        count = 0
        for info in pkgutil.iter_modules(pkg.__path__):
            module = importlib.import_module(f"{package}.{info.name}")
            for attr in dir(module):
                obj = getattr(module, attr)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, ServerModule)
                    and obj is not ServerModule
                ):
                    try:
                        self.register(obj())
                        count += 1
                    except Exception as exc:  # noqa: BLE001
                        log.error("failed to load module %s: %s", info.name, exc)
        return count

    def get(self, name: str) -> ServerModule | None:
        return self._modules.get(name)

    def __getitem__(self, name: str) -> ServerModule:
        return self._modules[name]

    def __contains__(self, name: object) -> bool:
        return name in self._modules

    def __len__(self) -> int:
        return len(self._modules)

    def list(self) -> dict[str, ServerModule]:
        return dict(self._modules)

    def on_result(self, result, session_id: str) -> None:
        for module in self._modules.values():
            try:
                if module.safe:
                    module.on_result(result, session_id)
            except Exception as exc:  # noqa: BLE001
                log.error("module %s on_result failed: %s", module.name, exc)
