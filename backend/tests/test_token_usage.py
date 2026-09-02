"""Tests for token usage accounting.

Every LLM call writes an ``audit_logs`` row. ``set_task_context`` (set by
the workflow runner and the step routes) attributes rows to the running
task + project; ``get_project_token_usage`` then aggregates per project,
per model and per run ("一篇文章"). These tests pin the attribution and
the aggregation.
"""

from __future__ import annotations

from datetime import datetime, timezone

import app.services.llm_service as llm_service
from app.models.audit_log import AuditLog
from app.services.usage_service import get_project_token_usage


class _FakeDB:
    """Captures AuditLog rows without touching any real database."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def scalar(self, *_args, **_kwargs):
        return None  # no active LLM config -> fast-fail path

    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_task_context_attributed_to_audit_rows(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(llm_service, "SessionLocal", lambda: fake)

    llm_service.set_task_context("task-1", "proj-1")
    try:
        llm_service.chat_completion("sys", "user")
    finally:
        llm_service.clear_task_context()

    assert len(fake.added) == 1
    row = fake.added[0]
    assert row.task_id == "task-1"
    assert row.project_id == "proj-1"

    # Context cleared: next call is unattributed.
    fake.added.clear()
    llm_service.chat_completion("sys", "user")
    assert fake.added[0].task_id is None
    assert fake.added[0].project_id is None


def test_explicit_task_id_overrides_context(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(llm_service, "SessionLocal", lambda: fake)

    llm_service.set_task_context("ctx-task", "proj-1")
    try:
        llm_service.chat_completion("sys", "user", task_id="explicit-task")
    finally:
        llm_service.clear_task_context()

    assert fake.added[0].task_id == "explicit-task"
    assert fake.added[0].project_id == "proj-1"


def test_task_context_manager_clears_on_exception(monkeypatch):
    fake = _FakeDB()
    monkeypatch.setattr(llm_service, "SessionLocal", lambda: fake)

    try:
        with llm_service.task_context("t", "p"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert llm_service.get_task_context() is None


def _audit_row(
    task_id: str | None,
    project_id: str | None,
    model: str,
    usage: dict | None,
    error: str | None = None,
) -> AuditLog:
    return AuditLog(
        id=f"row-{model}-{task_id}-{id(usage)}",
        call_id="call-1",
        task_id=task_id,
        project_id=project_id,
        provider="openai",
        model=model,
        system_prompt_hash="h1",
        user_prompt_hash="h2",
        latency_ms=100,
        usage=usage,
        error=error,
        created_at=datetime.now(timezone.utc),
    )


def test_usage_aggregation_groups_by_model_and_task(test_session_factory):
    db = test_session_factory()
    try:
        db.add_all(
            [
                _audit_row("task-a", "proj-1", "gpt-4o", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}),
                _audit_row("task-a", "proj-1", "gpt-4o", {"prompt_tokens": 60, "completion_tokens": 40, "total_tokens": 100}),
                _audit_row("task-a", "proj-1", "gpt-4o-mini", None),  # usage missing entirely
                _audit_row("task-b", "proj-1", "gpt-4o", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, error="timeout"),
                _audit_row("task-x", "proj-2", "gpt-4o", {"prompt_tokens": 999, "completion_tokens": 999, "total_tokens": 1998}),
            ]
        )
        db.commit()

        usage = get_project_token_usage("proj-1", db)

        assert usage["project_id"] == "proj-1"
        assert usage["total_calls"] == 4
        assert usage["failed_calls"] == 1
        assert usage["prompt_tokens"] == 170
        assert usage["completion_tokens"] == 95
        assert usage["total_tokens"] == 265

        by_model = {(m["provider"], m["model"]): m for m in usage["by_model"]}
        assert by_model[("openai", "gpt-4o")]["total_tokens"] == 265
        assert by_model[("openai", "gpt-4o")]["calls"] == 3
        assert by_model[("openai", "gpt-4o-mini")]["total_tokens"] == 0

        by_task = {t["task_id"]: t for t in usage["by_task"]}
        assert set(by_task) == {"task-a", "task-b"}
        assert by_task["task-a"]["total_tokens"] == 250
        assert by_task["task-a"]["calls"] == 3
        assert by_task["task-b"]["total_tokens"] == 15
    finally:
        db.close()


def test_usage_aggregation_empty_project(test_session_factory):
    db = test_session_factory()
    try:
        usage = get_project_token_usage("proj-empty", db)
        assert usage["total_calls"] == 0
        assert usage["total_tokens"] == 0
        assert usage["by_model"] == []
        assert usage["by_task"] == []
        assert usage["avg_latency_ms"] == 0
    finally:
        db.close()


def test_usage_aggregation_fills_missing_total(test_session_factory):
    """Some providers omit total_tokens - it must be derived from p+c."""
    db = test_session_factory()
    try:
        db.add(
            _audit_row(
                "task-a", "proj-1", "glm-4", {"prompt_tokens": 30, "completion_tokens": 20}
            )
        )
        db.commit()
        usage = get_project_token_usage("proj-1", db)
        assert usage["total_tokens"] == 50
    finally:
        db.close()
