"""Tests for L2 (figures stay in sync with revised text).

Before L2, the revise loop rewrote the text while figure captions and numbers
were fixed at first generation -- a revision that changed a section's evidence
left stale captions behind. Now ``finalize_figures`` is idempotent (numbers
stable, captions replaced in place) and a ``refresh_figures`` node sits between
``revise`` and ``review``, re-syncing captions for sections whose cited
evidence ids changed.
"""
from __future__ import annotations

import pytest

import app.services.image_service as img_service


# ── finalize_figures idempotency (L2 core guarantee) ─────────────


def test_finalize_figures_replaces_existing_caption_keeping_number():
    content = (
        "## 结果\n\n"
        "正文。\n\n"
        "![图A](/api/a.png)\n\n"
        "**图3：** 旧图注\n\n"
        "![图B](/api/b.png)"
    )
    images = [
        {"path": "/api/a.png", "caption": "新图注A"},
        {"path": "/api/b.png", "caption": "图注B"},
    ]
    out = img_service.finalize_figures(content, images)
    # Number kept, caption replaced in place -- no duplicate caption lines.
    assert "**图3：** 新图注A" in out
    assert "**图3：**" not in out.replace("**图3：** 新图注A", "")
    assert out.count("**图3：**") == 1


def test_finalize_figures_is_idempotent_after_second_run():
    content = "## 结果\n\n正文。\n\n![图A](/api/a.png)\n\n**图1：** 首次图注"
    images = [{"path": "/api/a.png", "caption": "首次图注"}]
    once = img_service.finalize_figures(content, images)
    twice = img_service.finalize_figures(once, images)
    assert once == twice
    assert once.count("**图1：**") == 1


def test_finalize_figures_alternates_captions_between_refresh_runs():
    content = "## 结果\n\n正文。\n\n![图A](/api/a.png)"
    first = img_service.finalize_figures(content, [{"path": "/api/a.png", "caption": "旧图注"}])
    assert "**图1：** 旧图注" in first
    # A later revision changes the caption; the number must not shift.
    second = img_service.finalize_figures(first, [{"path": "/api/a.png", "caption": "新图注"}])
    assert "**图1：** 新图注" in second
    assert "旧图注" not in second
    assert second.count("**图1：**") == 1


# ── refresh_figures_node ─────────────────────────────────────────


@pytest.fixture(autouse=True)
def _skip_without_langgraph():
    pytest.importorskip("langgraph")


def _graph_module():
    from app.services.workflow import graph as graph_mod

    return graph_mod


def _content_with(evidence_ids: list[str]) -> str:
    comments = "".join(f"<!-- evidence: {eid} -->" for eid in evidence_ids)
    return (
        f"## 实验结果\n\n"
        f"该模型性能表现良好。{comments}\n\n"
        f"![图A](/api/a.png)\n\n"
        f"**图1：** 旧图注\n\n"
        f"## 结论\n\n"
        f"总结如上。<!-- evidence: card-003 -->"
    )


def test_refresh_node_returns_empty_when_no_deps_or_images():
    graph_mod = _graph_module()
    assert graph_mod.refresh_figures_node({"task_id": "t"}) == {}


def test_refresh_node_noop_when_evidence_unchanged(monkeypatch):
    graph_mod = _graph_module()
    logs: list[str] = []
    monkeypatch.setattr(graph_mod, "add_log", lambda *a: logs.append(a[-1]))

    content = _content_with(["card-001"])
    state = {
        "task_id": "t",
        "current_content": content,
        "generated_images": [{"path": "/api/a.png", "section": "实验结果"}],
        "figure_deps": [{"path": "/api/a.png", "section": "实验结果", "evidence_ids": ["card-001"]}],
    }
    result = graph_mod.refresh_figures_node(state)
    assert result == {}
    assert any("no evidence changes" in line for line in logs)


def test_refresh_node_updates_caption_when_section_evidence_changed(monkeypatch):
    graph_mod = _graph_module()
    monkeypatch.setattr(graph_mod, "add_log", lambda *a: None)

    content = _content_with(["card-002"])  # revision swapped in a new card
    state = {
        "task_id": "t",
        "current_content": content,
        "generated_images": [{"path": "/api/a.png", "section": "实验结果", "caption": "旧图注"}],
        "figure_deps": [{"path": "/api/a.png", "section": "实验结果", "evidence_ids": ["card-001"]}],
    }
    result = graph_mod.refresh_figures_node(state)
    refreshed = result["current_content"]
    # Unplanned image: caption falls back to the revised section's first
    # sentence, number stays stable, old caption gone.
    assert "**图1：** 该模型性能表现良好" in refreshed
    assert "旧图注" not in refreshed
    assert refreshed.count("**图1：**") == 1


def test_refresh_node_keeps_planned_caption(monkeypatch):
    graph_mod = _graph_module()
    monkeypatch.setattr(graph_mod, "add_log", lambda *a: None)

    content = _content_with(["card-002"])
    state = {
        "task_id": "t",
        "current_content": content,
        "generated_images": [{"path": "/api/a.png", "section": "实验结果", "caption": "基线提升 30%"}],
        "figure_deps": [{"path": "/api/a.png", "section": "实验结果", "evidence_ids": ["card-001"]}],
        "figure_plans": [{"section": "实验结果", "caption": "基线提升 30%"}],
    }
    result = graph_mod.refresh_figures_node(state)
    refreshed = result["current_content"]
    assert "**图1：** 基线提升 30%" in refreshed
    assert refreshed.count("**图1：**") == 1
