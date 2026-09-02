"""Tests for the evidence-starvation guard batch.

Root cause observed in the 2026-08-19 run: with only 1 evidence card the
revision loop discovered that deleting content raises the quality score
(fewer claims -> fewer unsupported-claim issues). Round 2 cut the article
from 7506 to 3585 chars, scored best, and got exported - with stub
sections and a mid-sentence truncation. These tests pin the guards:

1. Document-level length guard: a revision shrinking the draft below 70%
   is rejected wholesale.
2. Global-pass length guard: a truncated/deleting global revision is
   dropped, keeping the pre-global version.
3. Paragraph-level length guard: a paragraph halved by revision reverts.
4. Honest-mode llm-knowledge markers are backfilled deterministically
   (prompt rule is not reliably followed) and exempt the paragraph from
   the evidence gate.
5. Honest-mode writing retries once before falling back to the stub.
6. Stray LLM wrapper tags (</refine>) are stripped from writer/revise
   output before entering the draft.
"""

from __future__ import annotations

import app.services.review_service as review_service
from app.services.writing_service import (
    _llm_write_section_honest,
    _ensure_knowledge_markers,
    strip_stray_llm_tags,
)


def _para(seed: str, target_len: int) -> str:
    text = seed
    while len(text) < target_len:
        text += seed
    return text


def _make_issue(**overrides) -> dict:
    base = {
        "issue_type": "logic",
        "severity": "high",
        "location": "paragraph-1",
        "claim": "该模型在 MMLU 上达到 95% 准确率。",
        "description": "将相关性表述为因果性，缺乏因果证据。",
        "suggestion": "改用谨慎语气，明确这是观察到的相关性。",
        "evidence_ids": ["card-001"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- guards


def test_revise_draft_rejects_wholesale_content_loss(monkeypatch):
    """Doc-level guard: every paragraph individually revised to 60% keeps
    passing the per-paragraph threshold, but the aggregate draft drops
    below 70% -> the whole revision must be rejected."""
    # 3 claim paragraphs x 200 chars, each carrying an issue.
    paras = [_para(f"第{i}段论断：该模型在 MMLU 上达到 95% 准确率，因此效果显著。", 200) for i in (1, 2, 3)]
    content = "## 标题\n\n" + "\n\n".join(paras)
    assert len(content) >= 500

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        # ~55% of the original paragraph length: passes the 0.5 per-para
        # guard, fails the 0.7 doc guard in aggregate.
        return _para("修订后的段落，明确为观察到的相关性。", 110)

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    issues = [
        _make_issue(location="paragraph-2"),
        _make_issue(location="paragraph-4", claim=paras[1]),
        _make_issue(location="paragraph-6", claim=paras[2]),
    ]
    revised = review_service.revise_draft(content, issues)

    assert revised == content


def test_revise_draft_rejects_shrinking_global_pass(monkeypatch):
    """Global pass guard: a truncated global revision is dropped."""
    body = _para("正文段落，包含充分的论述内容。", 300)

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        if "全文级" in system_prompt:
            # Truncated mid-output: far shorter than the input draft.
            return "全文修订。"
        return body  # paragraph pass returns the same text

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    content = f"## 标题\n\n{body}"
    revised = review_service.revise_draft(content, [_make_issue(location="global")])

    # The truncated global output must NOT become the draft.
    assert revised == content
    assert "全文修订。" != revised


def test_revise_draft_keeps_paragraph_halved_by_revision(monkeypatch):
    """Paragraph guard: a 150-char paragraph revised down to a stub reverts."""
    para = _para("该模型在 MMLU 上达到 95% 准确率，因此能显著提升下游任务效果。", 150)

    def fake_chat(system_prompt="", user_prompt="", temperature=None, max_tokens=None, timeout=None):
        return "删。"

    monkeypatch.setattr(review_service, "chat_completion_text", fake_chat)

    revised = review_service.revise_draft(
        f"## 标题\n\n{para}",
        [_make_issue(location="paragraph-2", claim="该模型在 MMLU 上达到 95% 准确率，因此能显著提升下游任务效果。")],
    )
    assert para in revised
    assert "删。" not in revised


# --------------------------------------------------- knowledge markers


def test_ensure_knowledge_markers_backfills_per_paragraph():
    text = (
        "第一段知识性分析。\n\n"
        "## 小标题\n\n"
        "![图](fig.png)\n\n"
        "第二段已有标注。 <!-- evidence: card-1 -->"
    )
    out = _ensure_knowledge_markers(text)
    blocks = out.split("\n\n")
    assert blocks[0].endswith("<!-- evidence: llm-knowledge -->")
    assert blocks[1] == "## 小标题"  # headings untouched
    assert blocks[2] == "![图](fig.png)"  # image lines untouched
    assert blocks[3] == "第二段已有标注。 <!-- evidence: card-1 -->"  # already-cited untouched


def test_backfilled_marker_exempts_paragraph_from_evidence_gate():
    """End-to-end: a claim paragraph carrying the backfilled marker must not
    be flagged as '缺少 evidence_id' by the rule layer."""
    claim = "该模型在 MMLU 上达到 95% 准确率，因此能显著提升下游任务效果。"
    marked = _ensure_knowledge_markers(claim)

    issues = review_service.review_draft(f"## 标题\n\n{marked}", [])
    assert not any("缺少 evidence_id" in str(i.get("description", "")) for i in issues)

    # Control: without the marker the same paragraph IS flagged.
    issues_unmarked = review_service.review_draft(f"## 标题\n\n{claim}", [])
    assert any("缺少 evidence_id" in str(i.get("description", "")) for i in issues_unmarked)


# --------------------------------------------------- honest mode retry


def test_honest_write_retries_once_after_failure(monkeypatch):
    import app.services.llm_service as llm_service

    calls = {"n": 0}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient provider error")
        return {"content": "基于公开信息的深度分析段落。"}

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    text = _llm_write_section_honest(
        section="生态演化展望",
        project_title="Agent 工具评测",
        research_question="",
        article_type="tech_report",
        word_count=300,
    )
    assert calls["n"] == 2
    assert "深度分析段落" in text
    assert "<!-- evidence: llm-knowledge -->" in text  # marker backfilled


def test_honest_write_falls_back_to_stub_after_two_failures(monkeypatch):
    import app.services.llm_service as llm_service

    calls = {"n": 0}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, **kwargs):
        calls["n"] += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_service, "chat_completion", fake_chat)

    text = _llm_write_section_honest(
        section="生态演化展望",
        project_title="Agent 工具评测",
        research_question="",
        article_type="tech_report",
        word_count=300,
    )
    assert calls["n"] == 2
    assert "暂无直接证据支撑" in text


# ------------------------------------------------------- stray tags


def test_strip_stray_llm_tags():
    assert strip_stray_llm_tags("正文段落。</refine>") == "正文段落。"
    assert strip_stray_llm_tags("<refine>\n修订内容") == "修订内容"
    assert strip_stray_llm_tags("<OUTPUT>答案</output>") == "答案"
    # Legit markdown is untouched.
    intact = "含 **加粗** 与 <!-- evidence: card-1 --> 的段落"
    assert strip_stray_llm_tags(intact) == intact
    assert strip_stray_llm_tags("") == ""


def test_honest_write_strips_stray_tags(monkeypatch):
    import app.services.llm_service as llm_service

    monkeypatch.setattr(
        llm_service,
        "chat_completion",
        lambda system_prompt="", user_prompt="", max_tokens=None, timeout=None, **kw: {
            "content": "分析内容。</refine>"
        },
    )
    text = _llm_write_section_honest(
        section="管线架构溯源",
        project_title="Agent 工具评测",
        research_question="",
        article_type="tech_report",
        word_count=300,
    )
    assert "</refine>" not in text
