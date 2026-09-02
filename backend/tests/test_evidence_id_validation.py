"""Tests for W4: evidence id validation in section writing.

The writer previously could cite any ``<!-- evidence: id -->`` comment --
including ids that don't exist, which then counted as evidence coverage for
nothing. Now the valid id list is explicit in the prompt, hallucinated ids
trigger one regeneration, and any that survive are stripped from the output.
"""
from __future__ import annotations

import app.services.writing_service as ws


def _make_card(ev_id: str, claim: str) -> dict:
    return {"id": ev_id, "claim": claim, "strength": "high", "source_type": "academic"}


def test_llm_write_section_lists_valid_ids_in_prompt(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": "正文。<!-- evidence: card-1 -->"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "证据结论明确可信。"), _make_card("card-2", "另一条证据。")],
        word_count=600,
    )
    assert "可用引用 ID" in captured["user_prompt"]
    assert "'card-1'" in captured["user_prompt"] and "'card-2'" in captured["user_prompt"]


def test_llm_write_section_regenerates_on_hallucinated_ids(monkeypatch):
    calls: list[dict] = []

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        calls.append(user_prompt)
        if len(calls) == 1:
            return {"content": "正文一。<!-- evidence: fake-99 -->"}
        return {"content": "正文二。<!-- evidence: card-1 -->"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    result = ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "证据结论明确可信。")],
        word_count=600,
    )
    assert len(calls) == 2
    assert "fake-99" in calls[1]          # correction names the bad id
    assert "可用引用 ID" in calls[1]       # and repeats the valid list
    assert "正文二" in result
    assert "<!-- evidence: card-1 -->" in result  # valid citation kept


def test_llm_write_section_strips_invalid_ids_that_survive_retry(monkeypatch):
    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        # Always hallucinate -- retry can't fix it, safety net must clean up.
        return {"content": "正文。<!-- evidence: card-1 --> 以及 <!-- evidence: fake-9 -->"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    result = ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "证据结论明确可信。")],
        word_count=600,
    )
    assert "fake-9" not in result
    assert "<!-- evidence: card-1 -->" in result


def test_llm_write_section_no_retry_when_all_ids_valid(monkeypatch):
    calls = []

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        calls.append(user_prompt)
        return {"content": "正文。<!-- evidence: card-1 -->"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "证据结论明确可信。")],
        word_count=600,
    )
    assert len(calls) == 1
