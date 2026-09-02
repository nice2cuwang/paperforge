"""Tests for incremental draft persistence (crash recovery).

Background: the auto-workflow used to run in a single DB transaction committed
only at ``result_node``. A crash before that rolled back everything, including
the draft ``draft_node`` had already generated and flushed -- so a task killed
at 86% (debate review) lost its 8357-char draft. The fix commits the draft at
generation time and records a ``draft_id`` artifact on the task so an orphaned
running task points at the surviving draft.

These tests verify:
1. ``draft_node`` commits the draft; closing the session without ``result_node``
   (simulating a process kill) keeps the draft queryable from a fresh session.
2. ``set_artifact`` is called with the persisted draft's id/version.
3. On reload, an orphaned running task with a ``draft_id`` artifact is marked
   failed AND its log/result point at the surviving draft.
4. An orphaned task without an artifact still gets the legacy "无恢复机制" log.
5. ``set_artifact`` is visible via ``get_task``.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import Draft, Project


def _make_project(db) -> Project:
    project = Project(
        id="proj-crash",
        title="Harness 到底指什么",
        research_question="Harness 是什么",
        article_type="product",
    )
    db.add(project)
    db.commit()
    return project


def test_draft_survives_session_close_without_result_node(monkeypatch, test_session_factory):
    """draft_node commits immediately; a crash (session close) keeps the draft."""
    pytest.importorskip("langgraph")
    from app.services.workflow import graph as graph_mod

    # Stub the LLM-bound draft builder; we only care about persistence.
    monkeypatch.setattr(
        graph_mod, "build_draft_markdown",
        lambda **kw: ("# 草稿\n\n正文。", ["马具隐喻"]),
    )
    # Isolate from the on-disk task store.
    artifact_calls: list[tuple] = []
    monkeypatch.setattr(graph_mod, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(
        graph_mod, "set_artifact",
        lambda tid, key, val: artifact_calls.append((tid, key, val)),
    )

    db = test_session_factory()
    project = _make_project(db)
    state = {
        "task_id": "t-crash",
        "db": db,
        "project_id": project.id,
        "project": project,
        "payload": SimpleNamespace(draft_title=None),
        "cards": [],
    }
    graph_mod.draft_node(state)

    # set_artifact must have recorded the draft id/version.
    recorded = {k: v for (_tid, k, v) in artifact_calls}
    assert "draft_id" in recorded and "draft_version" in recorded
    assert recorded["draft_version"] == 1

    # Simulate a process kill: close the worker session WITHOUT result_node.
    # Capture the PK before closing: with expire_on_commit=True the commit in
    # draft_node expired every attribute, so post-close access would raise
    # DetachedInstanceError before the actual assertions run.
    project_id = project.id
    db.close()

    # A fresh session (as if the server restarted) must see the committed draft.
    db2 = test_session_factory()
    try:
        drafts = db2.scalars(
            select(Draft).where(Draft.project_id == project_id)
        ).all()
        assert len(drafts) == 1
        assert drafts[0].content_md == "# 草稿\n\n正文。"
        assert drafts[0].status == "draft"
        # The artifact pointer matches the persisted row.
        assert drafts[0].id == recorded["draft_id"]
    finally:
        db2.close()


def test_orphan_running_task_with_artifact_points_to_draft(tmp_path, monkeypatch):
    """A running task with a draft_id artifact, on reload, points at the draft."""
    from app.services import task_registry as tr

    persist = tmp_path / "tasks.json"
    persist.write_text(
        json.dumps(
            {
                "t-orphan": {
                    "task_id": "t-orphan",
                    "status": "running",
                    "progress": 86,
                    "current_step": "reviewing draft (multi-agent debate)",
                    "logs": ["start: run-auto-workflow"],
                    "result": {},
                    "artifacts": {"draft_id": "d-123", "draft_version": 3},
                    "created_at": "2026-08-10T02:57:34+00:00",
                    "updated_at": "2026-08-10T03:14:53+00:00",
                    "started_at": "2026-08-10T02:57:34+00:00",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tr, "_PERSIST_PATH", persist)

    records = tr._load()
    rec = records["t-orphan"]
    assert rec.status == "failed"
    assert rec.current_step == "failed"
    # The failure log names the surviving draft (not the bare "无恢复机制").
    assert any("d-123" in line and "v3" in line for line in rec.logs)
    assert rec.result["recoverable_draft_id"] == "d-123"
    assert rec.result["recoverable_draft_version"] == 3


def test_orphan_running_task_without_artifact_keeps_legacy_message(tmp_path, monkeypatch):
    """Backward compat: a running task with no artifact still fails cleanly."""
    from app.services import task_registry as tr

    persist = tmp_path / "tasks.json"
    persist.write_text(
        json.dumps(
            {
                "t-old": {
                    "task_id": "t-old",
                    "status": "running",
                    "progress": 50,
                    "current_step": "searching",
                    "logs": ["start: run-auto-workflow"],
                    "result": {},
                    # Note: no "artifacts" key, like pre-fix tasks.json.
                    "created_at": "2026-08-10T02:57:34+00:00",
                    "updated_at": "2026-08-10T03:00:00+00:00",
                    "started_at": "2026-08-10T02:57:34+00:00",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tr, "_PERSIST_PATH", persist)

    records = tr._load()
    rec = records["t-old"]
    assert rec.status == "failed"
    assert any("无恢复机制" in line for line in rec.logs)
    assert rec.artifacts == {}


def test_set_artifact_visible_via_get_task(tmp_path, monkeypatch):
    """set_artifact persists and get_task exposes the artifacts dict."""
    from app.services import task_registry as tr

    monkeypatch.setattr(tr, "_PERSIST_PATH", tmp_path / "tasks.json")
    monkeypatch.setattr(tr, "_task_store", {})
    task = tr.create_task("run-auto-workflow")
    tr.set_artifact(task.task_id, "draft_id", "d-9")
    tr.set_artifact(task.task_id, "draft_version", 2)

    data = tr.get_task(task.task_id)
    assert data is not None
    assert data["artifacts"] == {"draft_id": "d-9", "draft_version": 2}
