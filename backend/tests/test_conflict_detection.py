"""Tests for S4: evidence conflict detection.

Cards with opposite conclusions on the same topic previously coexisted
silently -- the writer could cite both without comparing, or pick one at
random. Now ``detect_conflict_groups`` groups them (LLM + polarity-keyword
fallback), the writing prompt forces critical comparison, and the evidence
reviewer flags drafts that cite both sides without discussing the conflict.
"""
from __future__ import annotations

import app.services.debate_service as debate
import app.services.evidence_service as ev
import app.services.writing_service as ws


def _card(ev_id: str, claim: str) -> dict:
    return {"id": ev_id, "claim": claim, "strength": "high", "evidence_type": "paper"}


# ── Heuristic fallback ───────────────────────────────────────────


def test_heuristic_detects_opposite_conclusions():
    cards = [
        _card("a", "新方法使推理性能显著提升 30%，优于基线。"),
        _card("b", "实测中该方法性能不升反降 10%，低于基线。"),
    ]
    groups = ev._heuristic_conflict_groups(cards)
    assert len(groups) == 1
    assert set(groups[0]["card_ids"]) == {"a", "b"}


def test_heuristic_same_direction_is_not_a_conflict():
    cards = [
        _card("a", "该方法性能提升 30%。"),
        _card("b", "该方法在多语言任务上也显著优于基线。"),
    ]
    assert ev._heuristic_conflict_groups(cards) == []


def test_heuristic_unrelated_topics_not_conflicts():
    cards = [
        _card("a", "Transformer 训练速度显著提升。"),
        _card("b", "对比学习在图像分类上性能下降明显。"),
    ]
    assert ev._heuristic_conflict_groups(cards) == []


# ── Public API ───────────────────────────────────────────────────


def test_detect_conflict_groups_llm_path(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": '[{"card_ids": ["0", "1"], "topic": "推理性能"}]'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    cards = [_card("card-a", "性能提升 30%。"), _card("card-b", "性能下降 10%。")]
    groups = ev.detect_conflict_groups(cards, "性能如何？")
    assert len(groups) == 1
    assert groups[0]["group_id"] == "G1"
    assert set(groups[0]["card_ids"]) == {"card-a", "card-b"}
    assert groups[0]["topic"] == "推理性能"
    # Claims must reach the conflict prompt.
    assert "性能提升 30%" in captured["user_prompt"]


def test_detect_conflict_groups_falls_back_on_llm_failure(monkeypatch):
    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    cards = [_card("a", "性能显著提升 30%。"), _card("b", "性能不升反降 10%。")]
    groups = ev.detect_conflict_groups(cards, "性能如何？")
    assert len(groups) == 1
    assert set(groups[0]["card_ids"]) == {"a", "b"}


def test_detect_conflict_groups_requires_at_least_two_cards():
    assert ev.detect_conflict_groups([_card("a", "一句话。")], "问题？") == []


# ── Writing prompt integration ───────────────────────────────────


def test_build_draft_markdown_passes_conflict_note_to_section(monkeypatch):
    sections = ["一、背景", "二、核心分析"]
    conflict_groups = [
        {"group_id": "G1", "card_ids": ["card-a", "card-b"], "topic": "性能提升与下降"}
    ]
    captured: list[dict] = []

    def fake_write_section(**kwargs):
        captured.append(kwargs)
        return f"第{kwargs['section_index']}节正文。"

    monkeypatch.setattr(ws, "_llm_generate_abstract", lambda *a, **k: "摘要。")
    monkeypatch.setattr(ws, "_llm_write_section", fake_write_section)

    ws.build_draft_markdown(
        project_title="主题",
        research_question="证据能否支撑结论？",
        article_type="wechat_article",
        citation_style="numbers",
        evidence_cards=[
            _card("card-a", "背景证据显示该方法支撑结论：性能显著提升 30%，优于基线。"),
            _card("card-b", "背景证据显示该方法支撑结论：性能不升反降 10%，低于基线。"),
            _card("card-c", "核心证据支撑本文的主要结论判断。"),
        ],
        sections=sections,
        conflict_groups=conflict_groups,
    )
    # Both conflicting cards land in section 0 -> it gets the conflict note.
    note = captured[0]["conflict_note"]
    assert "G1" in note
    assert "批判性对比" in note
    assert "card-a" in note and "card-b" in note
    # Section 1 has no conflicting cards -> no note.
    assert captured[1]["conflict_note"] == ""


def test_llm_write_section_prompt_contains_conflict_instruction(monkeypatch):
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
        section_cards=[_card("card-a", "证据结论明确可信。")],
        word_count=600,
        conflict_note="本节证据存在冲突：G1（冲突主题：性能提升与下降）。引用时必须批判性对比。",
    )
    assert "证据冲突提示" in captured["user_prompt"]
    assert "批判性对比" in captured["user_prompt"]


# ── Reviewer integration ─────────────────────────────────────────


def test_evidence_brief_tags_conflict_groups():
    brief = debate._build_evidence_brief(
        [
            {
                "id": "card-a",
                "claim": "性能提升 30%。",
                "supporting_text": "实验结果显示性能提升 30%。",
                "strength": "high",
                "evidence_type": "empirical_result",
                "conflict_group": "G1",
            }
        ]
    )
    formatted = debate._format_evidence_for_reviewer(brief)
    assert "conflict_group=G1" in formatted
