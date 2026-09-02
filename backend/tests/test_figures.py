"""Tests for F1 (figure planning) and F3 (captions + cross-references).

Previously images were injected by fuzzy section-name matching with no plan,
no numbering and no captions -- nothing tied a figure to the claim it proves.
Now ``plan_figures`` grounds each figure in an evidence card before drafting,
the writing prompt references figures via ``{{ref:fig:N}}`` placeholders, and
``finalize_figures`` numbers images, adds bold captions, and resolves refs.
"""
from __future__ import annotations

import app.services.image_service as img_service
import app.services.writing_service as ws


def _make_card(ev_id: str, claim: str) -> dict:
    return {"id": ev_id, "claim": claim, "evidence_type": "paper", "strength": "high"}


def test_plan_figures_grounds_each_figure_in_best_evidence():
    cards = [
        _make_card("card-a", "背景证据显示该课题此前已有大量研究积累。"),
        _make_card("card-b", "核心分析表明性能提升 23%，显著优于基线。"),
    ]
    plans = img_service.plan_figures(["一、背景", "二、核心分析"], cards)
    assert len(plans) == 2
    # Each plan picks the card whose claim best matches its section.
    assert plans[0]["evidence_id"] == "card-a"
    assert plans[0]["caption"].startswith("背景证据")
    assert plans[1]["evidence_id"] == "card-b"
    assert plans[0]["ref_key"] == "fig:1"
    assert plans[1]["ref_key"] == "fig:2"
    # Quantitative claims plan a chart, descriptive ones an illustration.
    assert plans[0]["kind"] == "illustration"
    assert plans[1]["kind"] == "chart"


def test_plan_figures_keeps_section_even_without_matching_evidence():
    plans = img_service.plan_figures(["冷门章节"], [])
    assert len(plans) == 1
    assert plans[0]["evidence_id"] == ""
    assert "冷门章节" in plans[0]["caption"]


def test_plan_figures_empty_inputs():
    assert img_service.plan_figures([], []) == []


def test_finalize_figures_replaces_cross_reference_placeholders():
    content = "实验数据见 {{ref:fig:1}}，详细对比见{{ ref:fig:2 }}。"
    out = img_service.finalize_figures(content, [])
    assert "（如图1所示）" in out
    assert "（如图2所示）" in out
    assert "{{" not in out


def test_finalize_figures_numbers_images_and_adds_captions():
    content = "# 标题\n\n正文一。\n\n![图A](/api/a.png)\n\n正文二。\n\n![图B](/api/b.png)"
    images = [
        {"path": "/api/a.png", "caption": "架构对比示意"},
        {"path": "/api/b.png", "prompt": "性能基准提示词"},
    ]
    out = img_service.finalize_figures(content, images)
    assert "**图1：** 架构对比示意" in out
    assert "**图2：** 性能基准提示词" in out
    # Image lines are kept, numbered in order of appearance.
    assert out.index("![图A](/api/a.png)") < out.index("**图1：**")
    assert out.index("![图B](/api/b.png)") < out.index("**图2：**")


def test_build_draft_markdown_uses_provided_sections_and_figure_plans(monkeypatch):
    sections = ["一、背景", "二、核心分析", "三、结论"]
    plans = [
        {
            "fig_index": 1,
            "section": "一、背景",
            "kind": "illustration",
            "evidence_id": "card-1",
            "caption": "背景证据显示该课题此前已有大量研究积累。",
            "ref_key": "fig:1",
        }
    ]
    captured: list[dict] = []

    def fake_write_section(**kwargs):
        captured.append(kwargs)
        return f"第{kwargs['section_index']}节正文。"

    def boom(*a, **k):
        raise AssertionError("_generate_topic_sections must not be re-run when sections are provided")

    monkeypatch.setattr(ws, "_generate_topic_sections", boom)
    monkeypatch.setattr(ws, "_llm_generate_abstract", lambda *a, **k: "摘要。")
    monkeypatch.setattr(ws, "_llm_write_section", fake_write_section)

    content, got_sections = ws.build_draft_markdown(
        project_title="主题",
        research_question="证据能否支撑结论？",
        article_type="wechat_article",
        citation_style="numbers",
        evidence_cards=[
            _make_card("card-1", "背景证据显示该课题此前已有大量研究积累。"),
            _make_card("card-2", "核心证据支撑本文的主要结论判断。"),
            _make_card("card-3", "结论证据进一步证实研究问题的重要性。"),
        ],
        thesis_statement="主线：X。",
        sections=sections,
        figure_plans=plans,
    )
    assert got_sections == sections
    # Section 0 carries the figure plan; section 1 (no plan) carries none.
    assert "图1（illustration）" in captured[0]["figure_plan"]
    assert "card-1" in captured[0]["figure_plan"]
    assert captured[1]["figure_plan"] == ""


def test_llm_write_section_prompt_mentions_figure_placeholder(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": "正文。"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "某项证据结论明确可信。")],
        word_count=600,
        figure_plan="图1（illustration）：某项证据结论明确可信。；数据证据：card-1",
    )
    prompt = captured["user_prompt"]
    assert "本节配图规划" in prompt
    assert "{{ref:fig:N}}" in prompt
    assert "如图N所示" in prompt


def test_finalize_figures_resolves_ref_to_actual_figure_number():
    """A {{ref:fig:N}} placeholder resolves to the referenced image's actual
    displayed number, not the plan index N -- so an extracted image sitting
    ahead of a planned figure no longer breaks the cross-reference."""
    content = (
        "## 结果\n\n"
        "正文见 {{ref:fig:1}}。\n\n"
        "![extracted](/api/extracted.png)\n\n"
        "![planned](/api/planned.png)"
    )
    images = [
        {"path": "/api/extracted.png"},                    # no ref_key: extracted/social
        {"path": "/api/planned.png", "ref_key": "fig:1"},  # plan figure, sits 2nd
    ]
    out = img_service.finalize_figures(content, images)
    # Planned figure is the 2nd image line -> 图2; the ref must point at 图2.
    assert "（如图2所示）" in out
    assert "（如图1所示）" not in out
    assert "**图1：**" in out
    assert "**图2：**" in out
    assert "{{" not in out


def test_finalize_figures_collapses_double_wrapped_refs():
    """A placeholder the writer wrapped in its own parentheses
    (「（{{ref:fig:6}}）」) must not produce 「（（如图6所示））」."""
    content = "详情（{{ref:fig:6}}）。"
    out = img_service.finalize_figures(content, [])
    assert "（（如图6所示））" not in out
    assert "（如图6所示）" in out


def test_finalize_figures_neutralizes_ref_when_plan_figure_missing():
    """When images exist but a placeholder's ref_key has no anchor (its planned
    figure was never generated), the ref is DELETED instead of falling back to
    the plan index N -- which would point at the Nth image by position, an
    unrelated figure. (An earlier revision neutralized to "下图", which then
    stranded mid-sentence or at paragraph ends.)"""
    content = "见 {{ref:fig:1}} 与 {{ref:fig:2}}。"
    out = img_service.finalize_figures(content, [{"path": "/api/a.png"}])
    # Must NOT emit a numbered ref (would mispoint at the positional Nth image)
    # nor leave a dangling word behind.
    assert "（如图1所示）" not in out
    assert "（如图2所示）" not in out
    assert "{{" not in out
    assert "下图" not in out
    assert "见 与 。" not in out  # placeholder gaps collapse cleanly


def test_finalize_figures_skips_numbering_for_social_proof_cards():
    """Social proof cards are credibility cards, not numbered figures: they
    stay in the body but get no 图N caption, so a planned figure appearing
    after one is still 图1 (not bumped to 图2)."""
    content = (
        "## 结果\n\n"
        "![social](/api/social.svg)\n\n"
        "![planned](/api/planned.png)"
    )
    images = [
        {"path": "/api/social.svg", "source": "social_proof"},
        {"path": "/api/planned.png", "ref_key": "fig:1"},
    ]
    out = img_service.finalize_figures(content, images)
    # Social card image is preserved but gets no caption line.
    assert "![social](/api/social.svg)" in out
    social_idx = out.index("![social](/api/social.svg)")
    planned_idx = out.index("![planned](/api/planned.png)")
    assert "**图" not in out[social_idx:planned_idx]
    # Planned figure is the first numbered one -> 图1; social card stole no number.
    assert "**图1：**" in out
    assert "**图2：**" not in out
