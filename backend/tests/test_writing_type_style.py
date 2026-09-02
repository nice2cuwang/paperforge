"""Tests for W5 (de-AI tone, enforced in the writing prompt + light
post-processing) and W6 (article-type / section-role differentiated writing).

Previously every article type got the same generic writing style, and every
section was written identically; AI-flavoured template openers were neither
discouraged in the prompt nor cleaned up afterwards.
"""
from __future__ import annotations

import app.services.de_ai_service as de_ai
import app.services.writing_service as ws


# ── W6: article-type-specific writing emphasis ───────────────────


def test_writing_system_prompt_contains_base_style_and_w5_rules():
    prompt = ws._writing_system_prompt("academic_draft")
    assert "去 AI 腔（W5）" in prompt
    assert "随着" in prompt  # the banned template opener is named explicitly
    assert "长短交替" in prompt


def test_writing_system_prompt_differs_by_article_type():
    academic = ws._writing_system_prompt("academic_draft")
    wechat = ws._writing_system_prompt("wechat_article")
    assert academic != wechat
    # Each type gets its own emphasis block.
    assert "学术严谨" in academic
    assert "可读性优先" in wechat
    assert "政策导向" in ws._writing_system_prompt("policy_report")
    assert "批判综述" in ws._writing_system_prompt("literature_review")
    # Unknown types still get the base rules without crashing.
    assert "写作风格要求" in ws._writing_system_prompt("something_else")


# ── W6: section-role instruction ─────────────────────────────────


def test_section_role_instruction_by_position():
    assert "引言" in ws._section_role_instruction("背景", 0, 5)
    assert "结论" in ws._section_role_instruction("结论", 4, 5)
    # Conclusion forbids introducing new evidence.
    assert "禁止引入" in ws._section_role_instruction("总结", 4, 5)


def test_section_role_instruction_by_keyword():
    assert "方法" in ws._section_role_instruction("技术方案", 2, 5)
    assert "结果" in ws._section_role_instruction("实验结果", 2, 5)
    assert "讨论" in ws._section_role_instruction("讨论与启示", 2, 5)


def test_section_role_instruction_middle_generic_section_is_empty():
    assert ws._section_role_instruction("相关工作", 2, 5) == ""


# ── W5: de-AI post-processing ────────────────────────────────────


def test_de_ai_markdown_preserves_headings_and_structure():
    content = "# 标题\n\n第一段正文。\n\n> 引用块\n\n- 列表项\n\n第二段正文。"
    out = de_ai.de_ai_markdown(content)
    assert "# 标题" in out
    assert "> 引用块" in out
    assert "- 列表项" in out
    assert "第一段正文。" in out
    assert "第二段正文。" in out


def test_de_ai_markdown_preserves_evidence_comments():
    content = "该模型表现优异。<!-- evidence: card-001 -->"
    out = de_ai.de_ai_markdown(content)
    assert "<!-- evidence: card-001 -->" in out


def test_de_ai_markdown_removes_template_paragraph_openers():
    content = "随着深度学习的发展，该领域取得了长足进步。\n\n核心结论保持不变。"
    out = de_ai.de_ai_markdown(content)
    assert not out.startswith("随着")
    assert "核心结论保持不变" in out


def test_de_ai_markdown_preserves_image_lines():
    # Image lines must survive the post-processor untouched (W5 + L2 safety:
    # figure blocks stay in the content through the revise loop).
    content = "![图A](/api/a.png)\n\n正文围绕图A展开。"
    out = de_ai.de_ai_markdown(content)
    assert "![图A](/api/a.png)" in out


def test_de_ai_paragraph_varies_connectors_and_softens_tone():
    # 首先/因此 are deterministic connectors the transformer must replace.
    text = "首先，该方法有效。因此，可以推广。"
    out = de_ai.de_ai_paragraph(text, intensity=0.2)
    assert "首先" not in out
    assert "因此" not in out


def test_de_ai_metrics_reports_ai_pattern_density():
    # The 随着-的发展 template only counts when it starts a sentence/paragraph.
    metrics = de_ai.de_ai_metrics("随着技术的发展，进步明显。首先结论确定。因此推广。")
    assert metrics["connector_hits"] >= 2
    assert metrics["template_hits"] >= 1
    assert 0.0 <= metrics["connector_variety_score"] <= 1.0
