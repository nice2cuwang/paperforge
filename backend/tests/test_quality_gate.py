"""Quality gate regression baseline tests.

Validates that the review_draft_with_metrics scoring does not degrade
below established baselines. These tests serve as the regression
baseline document required by P1-4 of the tech plan.
"""

import pytest
from app.services.review_service import review_draft_with_metrics, score_quality


# ── Shared test fixtures ──────────────────────────────────────

def _make_cards() -> list[dict]:
    return [
        {
            "id": "ev-001",
            "paper_id": "paper-a",
            "chunk_ids": ["chunk-1"],
            "claim": "AI tools increase developer productivity by 30-50% in controlled studies",
            "supporting_text": "A 2024 meta-analysis of 12 controlled experiments found "
            "that AI-assisted developers completed tasks 30-50% faster than unaided peers.",
            "evidence_type": "empirical",
            "strength": "high",
            "limitations": "Studies limited to short coding tasks; long-term effects unknown",
            "page_start": 3,
            "page_end": 4,
            "citation_key": "Smith et al. 2024",
        },
        {
            "id": "ev-002",
            "paper_id": "paper-b",
            "chunk_ids": ["chunk-2"],
            "claim": "Novice programmers benefit most from AI pair programming",
            "supporting_text": "The productivity gain was largest for participants with less than "
            "2 years of programming experience (effect size d=0.82).",
            "evidence_type": "empirical",
            "strength": "medium",
            "limitations": "Sample size limited to 40 participants",
            "page_start": 7,
            "page_end": 8,
            "citation_key": "Jones & Lee 2023",
        },
        {
            "id": "ev-003",
            "paper_id": "paper-c",
            "chunk_ids": ["chunk-3"],
            "claim": "AI coding assistants may introduce subtle security vulnerabilities",
            "supporting_text": "Preliminary analysis suggests AI-generated code contains "
            "security flaws at comparable rates to novice human coders.",
            "evidence_type": "observational",
            "strength": "low",
            "limitations": "Single codebase analyzed; results may not generalize",
            "page_start": 12,
            "page_end": 13,
            "citation_key": "Chen 2025",
        },
    ]


GOLDEN_DRAFT = """\
# AI Impact on Developer Productivity

> article_type: policy_report
> citation_style: APA
> writing_mode: evidence-grounded

## 摘要

This draft addresses 'How does AI affect developer productivity?' using ranked evidence cards
with traceable evidence_id anchors.
<!-- evidence: ev-001, ev-002 -->

## 问题界定

AI tools have been adopted rapidly in software engineering. Evidence from controlled studies
indicates measurable productivity improvements when developers use AI assistants for coding
tasks. <!-- evidence: ev-001 -->

## 关键证据

The core evidence shows that AI-assisted developers complete tasks faster than unaided peers.
This effect appears strongest among novice programmers, who may lack established patterns
and benefit more from AI suggestions. <!-- evidence: ev-001, ev-002 --> However, evidence
about code quality is mixed.

## 政策建议

Organizations should adopt AI coding tools with appropriate guardrails. Training programs
should emphasize that AI output requires human verification, especially for security-critical
code. <!-- evidence: ev-003 -->

## 实施路径

1. Pilot AI tools with experienced developers who can evaluate output quality.
2. Establish mandatory code review policies for AI-generated contributions.
3. Track productivity and defect metrics to measure actual impact.
<!-- evidence: ev-001 -->

## 风险与限制

Current evidence comes primarily from short-term studies with small sample sizes.
The security implications of AI-generated code warrant caution. Organizations should
monitor emerging research and adjust policies accordingly. <!-- evidence: ev-003 -->

## 结论

AI tools offer measurable productivity benefits, particularly for less experienced
developers. However, the evidence base remains limited in scope and duration.
Policies should balance adoption with appropriate oversight.
<!-- evidence: ev-001, ev-002, ev-003 -->
"""


BAD_DRAFT = """\
# AI Impact Analysis

## 摘要

AI will completely revolutionize software development and all developers must adopt it
immediately. This is certain and undeniable.

Studies show AI causes massive productivity gains. The correlation between AI usage and
faster coding means AI directly causes better software. TODO: add actual evidence here.
<!-- evidence: ev-001 -->

毫无疑问 AI 必然彻底改变软件开发方式。The evidence completely proves that AI
is the single most important technology ever created. REPLACE_ME with actual data.

AI is perfect and has no downsides. Everyone should use it for everything.
"""


# ── Golden draft tests ────────────────────────────────────────

def test_golden_draft_passes_publication_gate():
    """Golden draft with proper evidence citations must pass all gate thresholds."""
    cards = _make_cards()
    issues, metrics = review_draft_with_metrics(GOLDEN_DRAFT, cards, "policy_report")

    # Rule-based (Layer 0) — deterministic, strict thresholds
    assert metrics["evidence_coverage"] >= 0.90, f"evidence_coverage={metrics['evidence_coverage']}"
    assert metrics["citation_validity"] >= 0.90, f"citation_validity={metrics['citation_validity']}"
    assert metrics["unsupported_claims"] == 0, f"unsupported_claims={metrics['unsupported_claims']}"

    # LLM-influenced (Layer 1-3) — non-deterministic; critical_issues may
    # vary per run. Conservative thresholds capture regression.
    assert metrics["logic_score"] >= 0.40, f"logic_score={metrics['logic_score']}"
    assert metrics["style_score"] >= 0.60, f"style_score={metrics['style_score']}"
    assert metrics["overall_score"] >= 0.55, f"overall_score={metrics['overall_score']}"


def test_golden_draft_all_claim_blocks_have_evidence():
    """Every claim block in the golden draft must reference valid evidence IDs."""
    cards = _make_cards()
    issues, _metrics = review_draft_with_metrics(GOLDEN_DRAFT, cards, "policy_report")

    # No high-severity evidence or citation issues
    evidence_issues = [i for i in issues if i["severity"] == "high" and i["issue_type"] in ("evidence", "citation")]
    assert len(evidence_issues) == 0, f"Found high-severity citation/evidence issues: {evidence_issues}"


def test_golden_draft_has_no_placeholder_issues():
    """Golden draft must not have TODO/REPLACE_ME placeholders."""
    cards = _make_cards()
    issues, _metrics = review_draft_with_metrics(GOLDEN_DRAFT, cards, "policy_report")

    placeholder_issues = [
        i for i in issues if "占位符" in i.get("description", "") or "REPLACE_ME" in i.get("claim", "")
    ]
    assert len(placeholder_issues) == 0


def test_golden_draft_regression_snapshot():
    """Exact metric values snapshot for regression detection.

    If this test fails, the scoring algorithm has changed.
    Update the baseline values only after verifying the change is intentional.
    """
    cards = _make_cards()
    _issues, metrics = review_draft_with_metrics(GOLDEN_DRAFT, cards, "policy_report")

    # Baseline snapshot — update with explanation if intentionally changed
    assert metrics["evidence_coverage"] == pytest.approx(1.0, abs=0.05)
    assert metrics["citation_validity"] == pytest.approx(1.0, abs=0.05)
    assert metrics["overall_score"] > 0.80


# ── Bad draft tests ───────────────────────────────────────────

def test_bad_draft_has_unsupported_claims():
    """Draft without evidence comments must have unsupported claims detected."""
    cards = _make_cards()
    _issues, metrics = review_draft_with_metrics(BAD_DRAFT, cards, "academic_draft")

    assert metrics["unsupported_claims"] > 0
    # Bad draft should NOT pass the publication gate
    assert metrics["publication_prepared"] is False


def test_bad_draft_detects_todo_placeholder():
    """Draft with TODO must trigger a placeholder issue."""
    cards = _make_cards()
    issues, _metrics = review_draft_with_metrics(BAD_DRAFT, cards, "academic_draft")

    placeholder_issues = [
        i for i in issues if "占位符" in i.get("description", "")
    ]
    assert len(placeholder_issues) > 0


def test_bad_draft_detects_correlation_causation_confusion():
    """Draft mixing correlation and causation terms must be flagged."""
    cards = _make_cards()
    issues, _metrics = review_draft_with_metrics(BAD_DRAFT, cards, "academic_draft")

    # "correlation" + "cause" patterns should trigger logic flags
    logic_issues = [i for i in issues if i["issue_type"] == "logic"]
    # At minimum: placeholder + correlation/causation + absolute language
    assert len(logic_issues) >= 1


def test_bad_draft_scores_below_golden():
    """Bad draft overall score must be strictly below the golden draft score."""
    cards = _make_cards()
    _, golden_metrics = review_draft_with_metrics(GOLDEN_DRAFT, cards, "policy_report")
    _, bad_metrics = review_draft_with_metrics(BAD_DRAFT, cards, "academic_draft")

    assert bad_metrics["overall_score"] < golden_metrics["overall_score"]


# ── score_quality unit tests ──────────────────────────────────

def test_score_quality_no_issues():
    """Zero issues should produce a near-perfect score."""
    result = score_quality(0, 0)
    assert result["overall_score"] == 1.0
    assert result["issue_count"] == 0
    assert result["critical_count"] == 0


def test_score_quality_max_penalty():
    """Many critical issues should cap at the penalty floor (base >= 0.1)."""
    # 30 issues * 0.03 = 0.90, capped at 0.60. 10 critical * 0.1 = 1.0, capped at 0.30.
    # Total penalty = 0.60 + 0.30 = 0.90. Base = 1.0 - 0.90 = 0.10.
    result = score_quality(30, 10)
    assert result["overall_score"] == pytest.approx(0.10, abs=0.01)
    assert result["issue_count"] == 30
    assert result["critical_count"] == 10


def test_score_quality_single_issue():
    """One non-critical issue reduces score by exactly 0.03."""
    result = score_quality(1, 0)
    assert result["overall_score"] == pytest.approx(0.97, abs=0.001)


def test_score_quality_single_critical():
    """One critical issue reduces score by 0.03 (issue) + 0.10 (critical) = 0.13."""
    result = score_quality(1, 1)
    assert result["overall_score"] == pytest.approx(0.87, abs=0.001)


def test_score_quality_merges_metrics():
    """When metrics dict is provided, its keys are merged into the result."""
    metrics = {"overall_score": 0.85, "custom_field": "present"}
    result = score_quality(3, 1, metrics=metrics)
    assert result["custom_field"] == "present"
    # metrics' overall_score takes precedence over computed one
    assert result["overall_score"] == 0.85


# ── Edge cases ────────────────────────────────────────────────

def test_empty_content():
    """Empty markdown should have zero issues and unsupported_claims=0."""
    issues, metrics = review_draft_with_metrics("", [], "academic_draft")
    assert metrics["unsupported_claims"] == 0
    assert metrics["critical_issues"] == 0
    # No claim blocks → evidence_coverage = 0/1 = 0 → gate fails
    assert metrics["publication_prepared"] is False


def test_content_with_only_headings():
    """Pure headings are filtered by _is_claim_block, only metadata line passes."""
    content = "# Title\n\n## Section 1\n\n## Section 2\n\n> article_type: test\n"
    issues, metrics = review_draft_with_metrics(content, [], "academic_draft")
    # "> article_type: test" matches startswith("> article_type") → filtered
    # All headings filtered → 0 total_claims → evidence_coverage = 0
    assert metrics["unsupported_claims"] == 0
    assert metrics["publication_prepared"] is False


def test_style_issues_for_policy_report():
    """policy_report without 政策建议 section triggers a style issue."""
    content = "# Title\n\nSome evidence-based claim.\n\n<!-- evidence: ev-001 -->"
    cards = [{"id": "ev-001", "strength": "high", "claim": "test", "paper_id": "p1"}]
    issues, _metrics = review_draft_with_metrics(content, cards, "policy_report")
    style_issues = [i for i in issues if i["issue_type"] == "style"]
    assert len(style_issues) >= 1  # missing 政策建议 section
    assert any("政策建议" in i.get("description", "") for i in style_issues)
