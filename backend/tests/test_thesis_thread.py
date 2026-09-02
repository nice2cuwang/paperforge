"""Tests for W1 (thesis thread) and W2 (cross-section context).

Previously every section was generated independently from title + evidence
cards: no shared argument line, no transition between sections. Now a
``thesis_thread`` node distills a thesis before drafting, and each section
receives the previous section's tail + the next section's title.
"""
from __future__ import annotations

import app.services.writing_service as ws


def _make_card(ev_id: str, claim: str, source_type: str = "academic") -> dict:
    return {
        "id": ev_id,
        "claim": claim,
        "evidence_type": "paper",
        "strength": "high",
        "source_type": source_type,
    }


def test_build_thesis_statement_llm_path(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": "核心主张：多智能体协作显著提升复杂任务效果。证据支柱：框架对比与基准评测。预期结论：给出工程建议。"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    thesis = ws.build_thesis_statement(
        "多智能体系统",
        "多智能体协作能否提升任务效果？",
        "wechat_article",
        [_make_card("card-1", "多智能体协作提升任务效果")],
    )
    assert "核心主张" in thesis
    # Evidence overview must reach the thesis prompt.
    assert "多智能体协作提升任务效果" in captured["user_prompt"]
    assert "证据支柱" in captured["user_prompt"]


def test_build_thesis_statement_fallback_on_llm_error(monkeypatch):
    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    thesis = ws.build_thesis_statement("主题A", "问题B？", "wechat_article", [])
    # Workflow must not block: fallback thesis is still non-empty and useful.
    assert thesis
    assert "问题B" in thesis


def test_llm_write_section_receives_thesis_and_cross_section_context(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": "本节正文。"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    result = ws._llm_write_section(
        section="第二章",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[_make_card("card-1", "某项证据结论")],
        word_count=600,
        section_index=1,
        total_sections=3,
        thesis_statement="核心主张是X。",
        prev_tail="上一节结尾句。",
        next_section="第三章标题",
    )
    assert "本节正文" in result
    prompt = captured["user_prompt"]
    assert "核心主张是X" in prompt          # W1: thesis reaches the section
    assert "上一节结尾句" in prompt          # W2: previous tail
    assert "第三章标题" in prompt            # W2: next section title
    assert "全文连贯" in prompt              # W2: explicit requirement
    assert "自然引出下一节" in prompt


def test_build_draft_markdown_chains_sections_sequentially(monkeypatch):
    """Each section must receive the previous section's tail and next title."""
    sections = ["一、背景", "二、核心分析", "三、结论"]
    captured: list[dict] = []

    def fake_write_section(**kwargs):
        idx = kwargs["section_index"]
        captured.append(kwargs)
        return f"第{idx}节正文。这是该节的结尾句。"

    monkeypatch.setattr(ws, "_generate_topic_sections", lambda *a, **k: sections)
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
    )

    assert got_sections == sections
    # Thesis is writing context, not body text: it reaches every section call.
    assert all(kwargs["thesis_statement"] == "主线：X。" for kwargs in captured)
    # Section 0: no previous tail, next title present.
    assert captured[0]["prev_tail"] == ""
    assert captured[0]["next_section"] == "二、核心分析"
    # Section 1: previous tail = tail of section 0's output; next title present.
    assert "第0节正文" in captured[1]["prev_tail"]
    assert captured[1]["next_section"] == "三、结论"
    # Section 2: tail chains, no next section.
    assert "第1节正文" in captured[2]["prev_tail"]
    assert captured[2]["next_section"] == ""


def test_honest_mode_also_receives_thesis_and_context(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return {"content": "知识性分析内容。"}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    ws._llm_write_section(
        section="背景",
        project_title="主题",
        research_question="问题？",
        article_type="wechat_article",
        section_cards=[],
        word_count=600,
        thesis_statement="主线Y。",
        prev_tail="上节尾。",
        next_section="下节标题",
    )
    assert "主线Y" in captured["user_prompt"]
    assert "上节尾" in captured["user_prompt"]
    assert "下节标题" in captured["user_prompt"]
