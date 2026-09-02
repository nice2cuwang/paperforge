"""Tests for 批次8 (figure presentation quality).

- Chart numbers must pass deterministic verbatim validation against the
  evidence corpus (F2 backstop, no more hallucinated numbers on charts).
- Ultra-wide banner shapes classify as decorative even without a vision model
  (logos/branding no longer leak into articles via the degraded path).
- Over-length drafts mark the debate result as truncated and revoke the
  publication_prepared verdict.
- Plan/image section matching canonicalizes section keys instead of exact
  string matching.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.services.chart_service as chart
import app.services.debate_service as debate
import app.services.image_service as image
from app.services.figure_extraction_service import _heuristic_category


# ── Verbatim number validation ───────────────────────────────────────────


def test_matches_evidence_number_variants():
    text = "GPT-4 在 MMLU 上达到 86.4%，训练成本 3000 万美元，共 8 项基准"
    assert chart.matches_evidence_number(86.4, text)
    assert chart.matches_evidence_number("86.4", text)
    assert chart.matches_evidence_number(3000, text)
    assert chart.matches_evidence_number(8, text)
    # Hallucinated values absent from the corpus must fail.
    assert not chart.matches_evidence_number(92.7, text)
    assert not chart.matches_evidence_number("95%", text)
    # Empty corpus: nothing to verify against.
    assert not chart.matches_evidence_number(86.4, "")


def test_generate_charts_drops_hallucinated_scores(tmp_path, monkeypatch):
    """Entries whose score is not verbatim in the evidence must not be charted."""
    monkeypatch.setattr(
        chart, "_extract_structured_data",
        lambda *a, **k: {"benchmarks": [
            {"model": "模型甲", "benchmark": "MMLU", "score": 86.4},    # in evidence
            {"model": "模型乙", "benchmark": "MMLU", "score": 70.0},    # in evidence
            {"model": "模型甲", "benchmark": "GSM8K", "score": 72.1},   # in evidence
            {"model": "模型乙", "benchmark": "GSM8K", "score": 99.9},   # hallucinated
        ]},
    )
    captured: list[list] = []

    def _fake_bar(data, output_path, *a, **k):
        captured.append(data)
        return str(output_path)

    monkeypatch.setattr(chart, "render_benchmark_comparison", _fake_bar)
    monkeypatch.setattr(chart, "render_results_table", lambda *a, **k: None)

    cards = [SimpleNamespace(claim="模型甲 MMLU 86.4 GSM8K 72.1", supporting_text="模型乙 MMLU 70.0")]
    chart.generate_charts_from_evidence(cards, "p1", "t", tmp_path)
    assert captured  # charts still render from the verified entries
    # The hallucinated 99.9 entry must not have reached any renderer.
    for data in captured:
        assert all(b["score"] != 99.9 for b in data)
    # GSM8K group survives with the single verified entry.
    gsm_groups = [d for d in captured if any(b["benchmark"] == "GSM8K" for b in d)]
    assert gsm_groups and len(gsm_groups[0]) == 1


# ── Decorative fallback ──────────────────────────────────────────────────


def test_heuristic_category_flags_banners_as_decorative():
    # Ultra-wide, very short banner near the top of page 1: a logo.
    assert _heuristic_category({"width": 900, "height": 80, "page": 1}) == "decorative"
    # Wide but tall enough: still a legitimate result table.
    assert _heuristic_category({"width": 900, "height": 300, "page": 5}) == "result_table"


# ── Debate truncation ────────────────────────────────────────────────────


def test_is_truncated_and_result_carries_flag():
    assert not debate.is_truncated("x" * 100)
    assert debate.is_truncated("x" * (debate.MAX_CONTENT_LENGTH + 1))
    result = debate.DebateResult(issues=[], truncated=True)
    assert result.truncated is True


def test_truncated_draft_blocks_publication_prepared(monkeypatch):
    """An over-length draft's publication verdict must be revoked: the
    reviewers only saw a prefix, so the tail was never certified."""
    import app.services.debate_service as debate_service
    import app.services.review_service as review_service

    def _fake_debate(content_md, evidence_cards, article_type=None, *, task_id=None):
        return debate_service.DebateResult(
            issues=[], truncated=debate_service.is_truncated(content_md)
        )

    monkeypatch.setattr(debate_service, "debate_review", _fake_debate)
    monkeypatch.setattr(review_service, "fact_check_draft", lambda *a, **k: ([], {}))

    cards = [{"id": "e1", "claim": "支持", "supporting_text": "支持文本", "strength": "high", "source_type": "paper"}]
    content_ok = "## 摘要\n\n论断<!-- evidence: e1 -->"
    _short_issues, short_metrics = review_service.debate_review_with_metrics(content_ok, cards)
    assert short_metrics["debate_truncated"] is False

    # Same content padded past MAX_CONTENT_LENGTH -> truncation exposed,
    # publication_prepared revoked regardless of other metrics.
    long_content = content_ok + "\n\n" + "填充段落<!-- evidence: e1 -->。\n\n" * 3000
    _long_issues, long_metrics = review_service.debate_review_with_metrics(long_content, cards)
    assert long_metrics["debate_truncated"] is True
    assert long_metrics["publication_prepared"] is False


# ── Canonical section matching ───────────────────────────────────────────


def test_resolve_section_key_canonicalizes_plan_and_image_sections():
    assert image.resolve_section_key("实验结果与分析") == "results"
    assert image.resolve_section_key("Results") == "results"
    assert image.resolve_section_key("研究背景与相关工作") == "background"
    assert image.resolve_section_key("技术架构") == "framework"
    assert image.resolve_section_key("结论与展望") == "conclusion"
    # Unknown topical heading passes through unchanged (stable join key).
    assert image.resolve_section_key("自定义主题节") == "自定义主题节"
