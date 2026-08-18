"""Task dispatch and module plumbing."""

from __future__ import annotations

import logging

from .db import Database
from .models import Task, TaskResult, new_id

log = logging.getLogger("meridian.task")


class TaskManager:
    def __init__(self, db: Database, registry=None):
        self._db = db
        self._registry = registry  # ModuleRegistry (may be None)

    def create(self, session_id: str, module: str, args: dict | None = None) -> Task:
        task = Task(
            id=new_id(),
            session_id=session_id,
            module=module,
            args=args or {},
        )
        self._db.insert_task(task)
        log.info(
            "task %s queued: %s %s", task.id[:8], module, args,
            extra={"event": "task_queued", "task_id": task.id, "session_id": session_id,
                   "mod": module, "task_args": args},
        )
        return task

    def pending(self, session_id: str) -> list[Task]:
        return self._db.pending_tasks(session_id)

    def get(self, task_id: str) -> Task | None:
        return self._db.get_task(task_id)

    def submit_result(self, session_id: str, r: TaskResult) -> None:
        self._db.insert_result(r)
        if self._registry is not None:
            self._registry.on_result(r, session_id)

    def results(self, session_id: str | None = None) -> list[TaskResult]:
        return self._db.list_results(session_id)
