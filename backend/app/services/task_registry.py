from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class TaskRecord:
    task_id: str
    status: str = "running"
    progress: int = 0
    current_step: str = "queued"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_task_store: dict[str, TaskRecord] = {}
_task_lock = Lock()


def _touch(task: TaskRecord) -> None:
    task.updated_at = datetime.now(timezone.utc)


def create_task(step: str) -> TaskRecord:
    task = TaskRecord(task_id=str(uuid4()), current_step=step, logs=[f"start: {step}"])
    with _task_lock:
        _task_store[task.task_id] = task
    return task


def set_progress(task_id: str, progress: int, step: str | None = None) -> None:
    with _task_lock:
        task = _task_store[task_id]
        task.progress = max(0, min(100, progress))
        if step is not None:
            task.current_step = step
        _touch(task)


def add_log(task_id: str, message: str) -> None:
    with _task_lock:
        task = _task_store[task_id]
        task.logs.append(message)
        _touch(task)


def complete_task(task_id: str, result: dict[str, Any] | None = None) -> None:
    with _task_lock:
        task = _task_store[task_id]
        task.status = "completed"
        task.progress = 100
        task.current_step = "done"
        if result is not None:
            task.result = result
        task.logs.append("completed")
        _touch(task)


def fail_task(task_id: str, reason: str, result: dict[str, Any] | None = None) -> None:
    with _task_lock:
        task = _task_store[task_id]
        task.status = "failed"
        task.current_step = "failed"
        if result is not None:
            task.result = result
        task.logs.append(f"failed: {reason}")
        _touch(task)


def get_task(task_id: str) -> dict[str, Any] | None:
    with _task_lock:
        task = _task_store.get(task_id)
        if task is None:
            return None
        data = asdict(task)
        data["updated_at"] = task.updated_at.isoformat()
        return data
