"""Tests for F5: semantic tagging of extracted paper figures.

Extracted figures previously carried only physical attributes (width, height,
page) so selection and section assignment were geometry-based guesswork. Now
``tag_figures_with_categories`` adds a content category + description (LLM,
with heuristic fallback), and ``select_best_figures`` weights the category.
"""
from __future__ import annotations

import pytest

import app.services.figure_extraction_service as fx


def _fig(page: int, w: int, h: int, title: str = "Paper X") -> dict:
    return {"path": f"/api/fig_{page}_{w}_{h}.png", "page": page, "width": w, "height": h, "paper_title": title, "source": "embedded"}


# ── LLM tagging ──────────────────────────────────────────────────


def test_tag_figures_with_categories_llm_path(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": '[{"index": 0, "category": "result_table", "description": "宽幅结果对比表"}, {"index": 1, "category": "architecture", "description": "系统架构图"}]'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    figs = [_fig(5, 2000, 400), _fig(2, 900, 700)]
    out = fx.tag_figures_with_categories(figs, "多智能体系统")
    assert out[0]["category"] == "result_table"
    assert out[0]["description"] == "宽幅结果对比表"
    assert out[1]["category"] == "architecture"
    # Figure metadata must reach the LLM prompt.
    assert "aspect=" in captured["user_prompt"]


def test_tag_figures_falls_back_to_heuristics(monkeypatch):
    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    figs = [_fig(5, 2000, 400), _fig(2, 900, 700)]
    out = fx.tag_figures_with_categories(figs, "主题")
    # Wide figure -> result_table; early square -> architecture.
    assert out[0]["category"] == "result_table"
    assert out[1]["category"] == "architecture"


def test_heuristic_category_by_aspect_and_page():
    assert fx._heuristic_category(_fig(5, 2000, 400)) == "result_table"
    assert fx._heuristic_category(_fig(2, 900, 700)) == "architecture"
    assert fx._heuristic_category(_fig(10, 1200, 800)) == "experiment_curve"
    assert fx._heuristic_category(_fig(2, 400, 500)) == "framework_overview"
    assert fx._heuristic_category(_fig(20, 300, 900)) == "other"


# ── Selection weighting ──────────────────────────────────────────


def test_select_best_figures_prefers_semantic_categories(monkeypatch):
    # Identical geometry: category must break the tie.
    figs = [
        {**_fig(5, 2000, 400), "category": "other"},
        {**_fig(5, 2000, 400), "category": "result_table"},
    ]
    picked = fx.select_best_figures(figs, max_count=1)
    assert picked[0]["category"] == "result_table"


# ── Graph node integration ───────────────────────────────────────


def test_image_node_uses_category_for_section(monkeypatch):
    pytest.importorskip("langgraph")
    from app.services.workflow import graph as graph_mod

    import app.services.chart_service as chart_svc
    import app.services.figure_extraction_service as fx_mod
    import app.services.image_service as img_svc
    import app.services.social_proof_service as social_svc

    def fake_tag(figs, title):
        for f in figs:
            f["category"] = "architecture"
            f["description"] = "多智能体协作系统的整体架构图"
        return figs

    # The node imports helpers from their source modules at call time, so
    # patch them there (patching graph_mod attributes would not take effect).
    monkeypatch.setattr(fx_mod, "tag_figures_with_categories", fake_tag)
    monkeypatch.setattr(fx_mod, "select_best_figures", lambda figs, max_count: figs[:1])
    monkeypatch.setattr(graph_mod, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "set_artifact", lambda *a, **k: None)
    # Prevent actual chart/social/decorative generation.
    monkeypatch.setattr(chart_svc, "generate_charts_from_evidence", lambda **k: [])
    monkeypatch.setattr(social_svc, "generate_social_proof_cards", lambda **k: [])
    monkeypatch.setattr(img_svc, "generate_article_images", lambda **k: [])
    monkeypatch.setattr(img_svc, "inject_images_into_markdown", lambda content, images: content)
    monkeypatch.setattr(img_svc, "finalize_figures", lambda content, images: content)

    draft = type("D", (), {"content_md": "# t\n\n## 方法\n正文。", "id": "d-1", "version": 1})()
    state = {
        "task_id": "t1",
        "project_id": "p1",
        "project": type("P", (), {"title": "主题", "article_type": "wechat_article", "research_question": "多智能体协作研究"})(),
        "draft": draft,
        "draft_sections": ["一、背景"],
        "cards": [],
        "selected_papers": [],
        "extracted_figures": [{"path": "/api/f.png", "page": 5, "width": 2000, "height": 400, "source": "embedded", "paper_title": "多智能体协作系统研究"}],
        "db": type("DB", (), {"flush": lambda self: None, "commit": lambda self: None})(),
        "figure_plans": [],
        "conflict_groups": [],
    }
    result = graph_mod.image_generation_node(state)
    images = result["generated_images"]
    assert any(img["section"] == "Framework" for img in images if img.get("source") == "extracted_figure")
