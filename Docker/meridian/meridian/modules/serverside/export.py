"""server/export — render an engagement report to markdown or JSON."""

from __future__ import annotations

from pathlib import Path

from ...reporting import write_report
from ..base import ServerModule, ServerModuleError


class ExportModule(ServerModule):
    name = "server/export"
    description = "Export an engagement report (markdown|json) to a file"
    safe = True

    def run(self, args: dict, context: dict) -> Path:
        session_id = args.get("session_id")
        fmt = args.get("format", "markdown")
        out = args.get("out", "report.md")
        if self.app is None:
            raise ServerModuleError("module not bound to an app")
        sessions = [s for s in self.app.sessions.list() if session_id in (None, s.id)]
        results = self.app.tasks.results(session_id)
        if not sessions:
            raise ServerModuleError("no sessions matched")
        path = write_report(
            self.app.db,
            sessions,
            results,
            Path(out),
            fmt,
        )
        return path
