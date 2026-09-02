"""Tests for L3 (review dimension: figure-text consistency).

The evidence reviewer previously checked the text against evidence cards only --
nothing tied figure captions to the claims of their section. Now the reviewer
prompt carries a dedicated figure-text consistency dimension that flags
contradictions between body text and figure captions (``issue_type: figure``).
"""
from __future__ import annotations

import app.services.debate_service as debate


def test_evidence_reviewer_prompt_has_figure_text_consistency_dimension():
    prompt = debate._EVIDENCE_REVIEWER_SYSTEM
    assert "图文一致性" in prompt
    assert "figure-text consistency" in prompt
    # It must be explicit about what to check: caption data vs section data.
    assert "图注" in prompt
    assert "图号" in prompt
    # Severity guidance: contradictions are high severity.
    assert "判为 high 问题" in prompt


def test_evidence_reviewer_prompt_issue_types_include_figure():
    # The JSON contract in the prompt must advertise the figure issue type so
    # the LLM emits issues the rest of the pipeline understands.
    assert "figure" in debate._EVIDENCE_REVIEWER_SYSTEM


def test_parse_issues_keeps_figure_issue_type():
    import json

    raw = json.dumps({
        "reasoning": "分析过程",
        "issues": [{
            "severity": "high",
            "location": "paragraph-2",
            "claim": "图注显示提升 30%，正文却写提升 50%。",
            "description": "图注数据与正文数据矛盾。",
            "suggestion": "统一为 30%。",
            "issue_type": "figure",
        }],
    })
    normalized = debate._parse_issues(raw, "evidence_reviewer")
    assert normalized[0]["issue_type"] == "figure"
    assert normalized[0]["description"] == "图注数据与正文数据矛盾。"


def test_evidence_reviewer_receives_figure_blocks_in_content(monkeypatch):
    """The reviewer must see image lines and captions to check consistency."""
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", **kwargs):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {"content": '{"reasoning": "' + "分析" * 80 + '", "issues": []}'}

    monkeypatch.setattr(debate, "chat_completion", fake_chat)

    content = (
        "## 实验结果\n\n"
        "性能提升显著。<!-- evidence: card-001 -->\n\n"
        "![性能对比](/api/a.png)\n\n"
        "**图1：** 性能提升 30%\n\n"
        "以上结论来自基线对比。"
    )
    issues = debate._evidence_reviewer(
        content,
        "证据卡文本",
        "academic_draft",
        {"level": "低", "text_length": 100, "evidence_count": 1, "paragraph_count": 2},
    )
    assert issues == []
    assert "![性能对比](/api/a.png)" in captured["user_prompt"]
    assert "**图1：** 性能提升 30%" in captured["user_prompt"]
