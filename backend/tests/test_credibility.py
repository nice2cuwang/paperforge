"""Tests for S3: source credibility weighting.

Academic / web / community / llm_knowledge sources previously competed for
evidence slots on equal footing -- a Reddit post could outrank a Nature paper
in the writer's prompt. Now every card carries a ``credibility_weight``, the
relevance filter is stricter for low-credibility sources, and the writing
prompt must label weak sources instead of stating them as fact.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.evidence_service import credibility_weight
from app.services.workflow.helpers import _evidence_to_dict
from app.services.writing_service import _format_cards_for_prompt


def test_credibility_weight_by_source_type():
    assert credibility_weight("academic") == 1.0
    assert credibility_weight("web") == 0.5
    assert credibility_weight("community") == 0.3
    assert credibility_weight("llm_knowledge") == 0.2
    assert credibility_weight(None) == 1.0  # default: paper chunks are academic
    assert credibility_weight("unknown_source") == 0.5
    assert credibility_weight("WEB") == 0.5  # case-insensitive


def test_credibility_weight_doi_bonus():
    assert credibility_weight("web", has_doi=True) == 0.6
    assert credibility_weight("academic", has_doi=True) == 1.0  # capped


def test_evidence_to_dict_carries_credibility():
    card = SimpleNamespace(
        id="ev-1",
        paper_id="p1",
        chunk_ids=["c1"],
        claim="claim",
        supporting_text="support",
        evidence_type="empirical_result",
        source_type="community",
        strength="medium",
        limitations=None,
        page_start=1,
        page_end=2,
        paper=SimpleNamespace(doi=None),
    )
    data = _evidence_to_dict(card)
    assert data["credibility_weight"] == 0.3

    card.paper.doi = "10.1000/verified"
    assert _evidence_to_dict(card)["credibility_weight"] == 0.4


def test_format_cards_for_prompt_surfaces_credibility():
    out = _format_cards_for_prompt(
        [{"id": "ev-1", "claim": "某社区观点", "source_type": "community", "strength": "low"}]
    )
    assert "credibility=0.3" in out
    out = _format_cards_for_prompt(
        [{"id": "ev-2", "claim": "论文结论", "source_type": "academic", "strength": "high"}]
    )
    assert "credibility=1.0" in out


def test_evidence_to_dict_carries_doi():
    """B6 regression: the card dict must expose the paper's DOI, otherwise
    verify_evidence_dois (fact check layer) never sees a DOI and DOI
    verification silently no-ops."""
    card = SimpleNamespace(
        id="ev-2",
        paper_id="p1",
        chunk_ids=["c1"],
        claim="claim",
        supporting_text="support",
        evidence_type="empirical_result",
        source_type="paper",
        strength="high",
        limitations=None,
        page_start=1,
        page_end=2,
        paper=SimpleNamespace(doi="10.1234/abc"),
    )
    assert _evidence_to_dict(card)["doi"] == "10.1234/abc"
