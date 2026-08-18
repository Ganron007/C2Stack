"""server/summary — operator-facing overview of sessions and task counts."""

from __future__ import annotations

from ..base import ServerModule


class SummaryModule(ServerModule):
    name = "server/summary"
    description = "Summarize active sessions and pending/completed task counts"
    safe = True

    def run(self, args: dict, context: dict) -> dict:
        if self.app is None:
            return {"error": "not bound"}
        sessions = self.app.sessions.list()
        pending = sum(
            self.app.db.count_pending_tasks(s.id) for s in sessions
        )
        total_results = len(self.app.tasks.results())
        return {
            "sessions": len(sessions),
            "pending_tasks": pending,
            "completed_results": total_results,
        }
