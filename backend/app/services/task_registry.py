from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock as Lock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _workflow_error_detail(exc: Exception) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, HTTPException):
        if isinstance(exc.detail, dict):
            detail = dict(exc.detail)
            reason = str(detail.get("message") or detail.get("title") or detail.get("code") or "request failed")
            detail.setdefault("http_status", exc.status_code)
            return reason, detail
        message = str(exc.detail)
        return message, {"code": "HTTP_ERROR", "message": message, "http_status": exc.status_code}
    message = str(exc)
    return message, {"code": "UNEXPECTED_ERROR", "message": message}


def _fail_task_for_exception(task_id: str, exc: Exception) -> None:
    reason, detail = _workflow_error_detail(exc)
    fail_task(task_id, reason, detail)

# Default TTL: 24 hours for completed/failed tasks, 2 hours for running tasks.
_TASK_COMPLETED_TTL_SECONDS = int(60 * 60 * 24)
_TASK_RUNNING_TTL_SECONDS = int(60 * 60 * 2)

_PERSIST_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_PERSIST_DIR.mkdir(parents=True, exist_ok=True)
_PERSIST_PATH = _PERSIST_DIR / "tasks.json"


def _dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _record_to_dict(task: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "status": task.status,
        "progress": task.progress,
        "current_step": task.current_step,
        "logs": task.logs,
        "result": task.result,
        "created_at": _dt_to_str(task.created_at),
        "updated_at": _dt_to_str(task.updated_at),
        "started_at": _dt_to_str(task.started_at),
    }


def _dict_to_record(data: dict[str, Any]) -> TaskRecord:
    return TaskRecord(
        task_id=data["task_id"],
        status=data.get("status", "running"),
        progress=data.get("progress", 0),
        current_step=data.get("current_step", "queued"),
        logs=data.get("logs", []),
        result=data.get("result", {}),
        created_at=_str_to_dt(data["created_at"]),
        updated_at=_str_to_dt(data["updated_at"]),
        started_at=_str_to_dt(data.get("started_at", data["created_at"])),
    )


def _load() -> dict[str, TaskRecord]:
    if not _PERSIST_PATH.exists():
        return {}
    try:
        with open(_PERSIST_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        records = {}
        orphaned = 0
        for tid, data in payload.items():
            try:
                rec = _dict_to_record(data)
                if rec.status == "running":
                    rec.status = "failed"
                    rec.current_step = "failed"
                    rec.logs.append("failed: 服务端重启，任务中断（无恢复机制）")
                    _touch(rec)
                    orphaned += 1
                records[tid] = rec
            except Exception:
                logger.warning("skipped corrupted task record: %s", tid)
                continue
        if orphaned:
            logger.warning("marked %d orphaned running task(s) as failed", orphaned)
            _save_from_records(records)
        logger.info("loaded %d task(s) from %s", len(records), _PERSIST_PATH)
        return records
    except Exception as exc:
        logger.warning("failed to load tasks from %s: %s", _PERSIST_PATH, exc)
        return {}


def _save_from_records(records: dict[str, TaskRecord]) -> None:
    try:
        payload = {tid: _record_to_dict(t) for tid, t in records.items()}
        tmp = _PERSIST_PATH.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, _PERSIST_PATH)
    except Exception as exc:
        logger.warning("failed to persist tasks: %s", exc)


def _save() -> None:
    _save_from_records(_task_store)


@dataclass
class TaskRecord:
    task_id: str
    status: str = "running"
    progress: int = 0
    current_step: str = "queued"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_task_store: dict[str, TaskRecord] = _load()
_task_lock = Lock()


def _touch(task: TaskRecord) -> None:
    task.updated_at = datetime.now(timezone.utc)


def _is_expired(task: TaskRecord) -> bool:
    now = datetime.now(timezone.utc)
    if task.status in {"completed", "failed"}:
        return (now - task.updated_at).total_seconds() > _TASK_COMPLETED_TTL_SECONDS
    return (now - task.created_at).total_seconds() > _TASK_RUNNING_TTL_SECONDS


def cleanup_expired_tasks() -> int:
    """Remove expired tasks from the in-memory store. Returns number removed."""
    removed = 0
    with _task_lock:
        expired_ids = [tid for tid, t in _task_store.items() if _is_expired(t)]
        for tid in expired_ids:
            del _task_store[tid]
            removed += 1
        if removed:
            _save()
    if removed:
        logger.info("cleaned up %d expired task(s)", removed)
    return removed


def create_task(step: str) -> TaskRecord:
    task = TaskRecord(task_id=str(uuid4()), current_step=step, logs=[f"start: {step}"])
    with _task_lock:
        _task_store[task.task_id] = task
        _save()
    try:
        from app.middleware.metrics import metrics_inc
        metrics_inc("paperforge_tasks_created")
    except Exception:
        pass
    return task


def set_progress(task_id: str, progress: int, step: str | None = None) -> None:
    with _task_lock:
        task = _task_store.get(task_id)
        if task is None:
            return
        task.progress = max(0, min(100, progress))
        if step is not None:
            task.current_step = step
        _touch(task)
        _save()


def add_log(task_id: str, message: str) -> None:
    with _task_lock:
        task = _task_store.get(task_id)
        if task is None:
            return
        task.logs.append(message)
        _touch(task)
        _save()


def add_structured_log(
    task_id: str,
    step: str,
    message: str,
    *,
    paper_id: str | None = None,
    provider: str | None = None,
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Add a structured JSON log entry for traceability."""
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "message": message,
    }
    if paper_id:
        payload["paper_id"] = paper_id
    if provider:
        payload["provider"] = provider
    if error_code:
        payload["error_code"] = error_code
    if extra:
        payload.update(extra)
    add_log(task_id, json.dumps(payload, ensure_ascii=False))


def complete_task(task_id: str, result: dict[str, Any] | None = None) -> None:
    with _task_lock:
        task = _task_store.get(task_id)
        if task is None:
            return
        task.status = "completed"
        task.progress = 100
        task.current_step = "done"
        if result is not None:
            task.result = result
        task.logs.append("completed")
        _touch(task)
        duration_seconds = (task.updated_at - task.started_at).total_seconds()
        _save()
    try:
        from app.middleware.metrics import metrics_inc, metrics_observe
        metrics_inc("paperforge_tasks_completed")
        metrics_observe("paperforge_task_duration_seconds", duration_seconds)
        if result and isinstance(result, dict):
            if result.get("evidence_count", 0) > 0:
                metrics_inc("paperforge_evidence_cards_generated", result["evidence_count"])
            if "publication_prepared" in result:
                metrics_inc("paperforge_publication_gate_total")
                if result["publication_prepared"]:
                    metrics_inc("paperforge_publication_gate_passed")
    except Exception:
        pass


def fail_task(task_id: str, reason: str, result: dict[str, Any] | None = None) -> None:
    with _task_lock:
        task = _task_store.get(task_id)
        if task is None:
            return
        task.status = "failed"
        task.current_step = "failed"
        if result is not None:
            task.result = result
        task.logs.append(f"failed: {reason}")
        _touch(task)
        _save()
    try:
        from app.middleware.metrics import metrics_inc
        metrics_inc("paperforge_tasks_failed")
    except Exception:
        pass


def get_task(task_id: str) -> dict[str, Any] | None:
    with _task_lock:
        # Auto-cleanup on read to prevent unbounded growth
        cleanup_expired_tasks()
        task = _task_store.get(task_id)
        if task is None:
            return None
        data = asdict(task)
        data["updated_at"] = task.updated_at.isoformat()
        data["created_at"] = task.created_at.isoformat()
        data["expired"] = _is_expired(task)
        return data


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    with _task_lock:
        cleanup_expired_tasks()
        sorted_tasks = sorted(
            _task_store.values(), key=lambda t: t.created_at, reverse=True
        )
        return [get_task(t.task_id) for t in sorted_tasks[:limit] if get_task(t.task_id) is not None]
