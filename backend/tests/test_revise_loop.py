"""Tests for the review -> revise loop (L1 fix).

Previously ``revise_node`` truncated review issues to
``issue_type/severity/location``, dropping ``description/suggestion/claim/
evidence_ids``. The reviser LLM therefore received ``description=None,
suggestion=None`` and literally could not know what to fix -- the 3-round
review<->revise loop was blind. These tests verify the full issue context now
reaches the reviser prompt.
"""
from __future__ import annotations

import pytest

import app.services.review_service as review_service


def _make_issue(**overrides) -> dict:
    base = {
        "issue_type": "logic",
        "severity": "high",
        "location": "paragraph-1",
        "claim": "该模型在 MMLU 上达到 95% 准确率。",
        "description": "将相关性表述为因果性，缺乏因果证据。",
        "suggestion": "改用谨慎语气，明确这是观察到的相关性。",
        "evidence_ids": ["card-001", "card-007"],
    }
    base.update(overrides)
    return base


def test_revise_draft_passes_full_issue_context_to_reviser(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "修订后的段落，明确为观察到的相关性。"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    content = "## 引言\n\n该模型在 MMLU 上达到 95% 准确率，因此能显著提升下游任务效果。"
    revised = review_service.revise_draft(content, [_make_issue()])

    prompt = captured["user_prompt"]
    # description / suggestion / claim text / evidence ids must reach the
    # reviser -- before the fix these were all None or absent.
    assert "将相关性表述为因果性" in prompt
    assert "改用谨慎语气" in prompt
    assert "95% 准确率" in prompt
    assert "card-001" in prompt and "card-007" in prompt
    # No bare "None" placeholders in the review-issues section.
    issues_section = prompt.split("审校意见", 1)[1]
    assert "None" not in issues_section
    assert "修订后的段落" in revised


def test_revise_node_forwards_full_issues_not_truncated(monkeypatch):
    """revise_node must forward description/suggestion/claim/evidence_ids."""
    pytest.importorskip("langgraph")
    from app.services.workflow import graph as graph_mod

    captured: dict = {}

    def fake_revise_draft(content_md, issues):
        captured["issues"] = issues
        return "revised content"

    monkeypatch.setattr(graph_mod, "revise_draft", fake_revise_draft)
    monkeypatch.setattr(graph_mod, "add_log", lambda *a, **k: None)

    state = {
        "task_id": "t1",
        "current_content": "orig",
        "current_issues": [_make_issue()],
        "revision_round": 0,
    }
    result = graph_mod.revise_node(state)

    forwarded = captured["issues"][0]
    assert forwarded["description"] == "将相关性表述为因果性，缺乏因果证据。"
    assert forwarded["suggestion"] == "改用谨慎语气，明确这是观察到的相关性。"
    assert forwarded["claim"] == "该模型在 MMLU 上达到 95% 准确率。"
    assert forwarded["evidence_ids"] == ["card-001", "card-007"]
    assert result["current_content"] == "revised content"


def test_revise_paragraph_prompt_calls_out_high_severity_count(monkeypatch):
    """The reviser prompt should tell the LLM how many high-severity issues to fix."""
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        captured["user_prompt"] = user_prompt
        return "revised"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    issues = [
        _make_issue(severity="high", description="问题一"),
        _make_issue(severity="high", description="问题二", location="paragraph-1"),
        _make_issue(severity="low", description="小问题", location="paragraph-1"),
    ]
    review_service.revise_draft("## 标题\n\n正文段落。", issues)
    assert "2 条 high 级别意见必须正面回应" in captured["user_prompt"]


def test_revise_draft_uses_global_block_indexing(monkeypatch):
    """paragraph-N must refer to the Nth block *including headings*.

    B1 regression: the rule layer (review_draft_with_metrics) numbers blocks
    globally while revise used to count claim blocks only -- every issue
    landed on the wrong paragraph. Both sides now share _split_blocks +
    1-based global indexing.
    """
    captured_paragraphs: list[str] = []

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        captured_paragraphs.append(user_prompt.split("原段落：\n", 1)[1].split("\n\n")[0])
        return "修订段落。"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    content = (
        "# 标题\n\n"
        "## 引言\n\n"
        "第一段正文。\n\n"
        "## 方法\n\n"
        "第二段正文，包含术语。"
    )
    # Rule layer numbering: block 5 = "第二段正文" (title + heading + para + heading + para)
    issue = _make_issue(location="paragraph-5", claim="第二段正文")
    revised = review_service.revise_draft(content, [issue])

    assert captured_paragraphs == ["第二段正文，包含术语。"]
    assert "第二段正文，包含术语。" not in revised
    assert "第一段正文。" in revised  # untouched blocks preserved verbatim


def test_revise_draft_remaps_wrong_index_via_claim_text(monkeypatch):
    """LLM reviewer numbering drifts; a unique claim snippet overrides it."""
    captured_paragraphs: list[str] = []

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        captured_paragraphs.append(user_prompt.split("原段落：\n", 1)[1].split("\n\n")[0])
        return "修订段落。"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    content = "## 引言\n\n甲段落。\n\n## 实验\n\n乙段落讨论了 MMLU 基准。"
    issue = _make_issue(location="paragraph-2", claim="乙段落讨论了 MMLU 基准。")
    review_service.revise_draft(content, [issue])
    assert captured_paragraphs == ["乙段落讨论了 MMLU 基准。"]


def test_revise_draft_applies_global_issues_instead_of_dropping(monkeypatch):
    """B2 regression: location="global" issues must drive a full-doc revision."""
    calls: list[dict] = []

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        calls.append({"system": system_prompt, "user": user_prompt})
        if "全文级" in system_prompt:
            return "全文修订后的文稿。"
        return "段落修订。"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    content = "## 引言\n\n正文段落。"
    issue = _make_issue(location="global", description="章节之间缺乏过渡。")
    revised = review_service.revise_draft(content, [issue])

    assert any("全文级" in c["system"] for c in calls)
    assert "章节之间缺乏过渡" in calls[-1]["user"]
    assert revised == "全文修订后的文稿。"
