"""Token usage aggregation over the LLM audit log.

Every LLM invocation is recorded in ``audit_logs`` with its ``usage`` JSON
(prompt/completion/total tokens). ``set_task_context`` in llm_service
attributes each row to the running task and project, so a project's total
consumption - and the cost of each individual workflow run ("一篇文章") -
is a plain aggregation over that table.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def _usage_tokens(usage: dict | None) -> tuple[int, int, int]:
    """Extract (prompt, completion, total) from a usage dict, tolerating
    missing keys / non-numeric values (some providers omit usage entirely)."""
    if not isinstance(usage, dict):
        return 0, 0, 0

    def _int(key: str) -> int:
        value = usage.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    prompt = _int("prompt_tokens")
    completion = _int("completion_tokens")
    total = _int("total_tokens")
    if total == 0:
        total = prompt + completion
    return prompt, completion, total


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def get_project_token_usage(project_id: str, db: Session) -> dict[str, Any]:
    """Aggregate token consumption for one project.

    Returns totals, a per-model breakdown, and a per-task (per workflow run)
    breakdown. Rows without a task_id (e.g. chat-style calls) are grouped
    under ``task_id: null`` in ``by_task``.
    """
    rows = list(
        db.scalars(
            select(AuditLog).where(AuditLog.project_id == project_id).order_by(AuditLog.created_at)
        ).all()
    )

    total_calls = 0
    failed_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    total_latency_ms = 0
    first_call_at: datetime | None = None
    last_call_at: datetime | None = None

    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    by_task: dict[str | None, dict[str, Any]] = {}

    for row in rows:
        total_calls += 1
        if row.error:
            failed_calls += 1
        p, c, t = _usage_tokens(row.usage)
        prompt_tokens += p
        completion_tokens += c
        total_tokens += t
        total_latency_ms += row.latency_ms or 0
        if row.created_at is not None:
            if first_call_at is None:
                first_call_at = row.created_at
            last_call_at = row.created_at

        model_key = (row.provider or "unknown", row.model or "unknown")
        model_stat = by_model.setdefault(
            model_key,
            {
                "provider": model_key[0],
                "model": model_key[1],
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )
        model_stat["calls"] += 1
        model_stat["prompt_tokens"] += p
        model_stat["completion_tokens"] += c
        model_stat["total_tokens"] += t

        task_stat = by_task.setdefault(
            row.task_id,
            {
                "task_id": row.task_id,
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "first_call_at": _fmt_dt(row.created_at),
                "last_call_at": _fmt_dt(row.created_at),
            },
        )
        task_stat["calls"] += 1
        task_stat["prompt_tokens"] += p
        task_stat["completion_tokens"] += c
        task_stat["total_tokens"] += t
        task_stat["last_call_at"] = _fmt_dt(row.created_at)

    # 最新一次运行排最前
    task_list = sorted(
        by_task.values(),
        key=lambda s: s["last_call_at"] or "",
        reverse=True,
    )

    return {
        "project_id": project_id,
        "total_calls": total_calls,
        "failed_calls": failed_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "total_latency_ms": total_latency_ms,
        "avg_latency_ms": (total_latency_ms // total_calls) if total_calls else 0,
        "first_call_at": _fmt_dt(first_call_at),
        "last_call_at": _fmt_dt(last_call_at),
        "by_model": sorted(
            by_model.values(), key=lambda s: s["total_tokens"], reverse=True
        ),
        "by_task": task_list,
    }
