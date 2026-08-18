"""Server-side module interface.

Implant task modules (builtin/exec, builtin/shell, ...) run on the implant.
Server-side modules extend what the operator can do: enrich results, react to
events, or run entirely on the server (export, report, scan helpers).

Modules are discovered from the ``meridian.modules.serverside`` package and
from any additional path configured at runtime.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger("meridian.module")


class ServerModule(ABC):
    """Base class for server-side Meridian modules."""

    name: str = ""
    description: str = ""
    #: whether handle_result() may run arbitrary post-processing
    safe: bool = True

    def __init__(self, app: Any = None):
        self.app = app

    @abstractmethod
    def run(self, args: dict, context: dict) -> Any:
        """Invoked by the operator console: `module <name> <args>`."""

    def on_result(self, result, session_id: str) -> None:
        """Optional hook called for every completed task result."""
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name}>"


class ServerModuleError(Exception):
    """Raised when a module invocation fails."""
