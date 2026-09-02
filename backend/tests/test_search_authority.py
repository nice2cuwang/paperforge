"""Tests for S1: evidence authority weighting in search_service.

Providers previously faked ``relevance_score`` with a position-based decay
(``1.0 - idx * 0.02``) that only encoded list order, so a random first hit
outranked a 50k-citation classic. Now each candidate carries its real
``cited_by_count`` and an authority score blending citations + recency.
"""
from __future__ import annotations

import pytest

import app.services.search_service as search_service


def _candidate(doi: str, title: str, cited: int, year: int | None = 2024) -> search_service.PaperCandidate:
    return search_service.PaperCandidate(
        title=title,
        authors=[],
        year=year,
        doi=doi,
        arxiv_id=None,
        venue="test",
        abstract=None,
        source="test",
        source_url=None,
        pdf_url=None,
        oa_status=None,
        license=None,
        relevance_score=0.5,
        cited_by_count=cited,
    )


def test_authority_score_prefers_cited_recent_work():
    recent_cited = search_service._authority_score(50000, 2025)
    old_uncited = search_service._authority_score(0, 1999)
    assert recent_cited >= 0.9
    assert old_uncited < 0.4
    assert recent_cited > old_uncited
    # No citation metadata at all -> recency-agnostic middle ground, not 0.
    assert 0.15 < search_service._authority_score(0, None) < 0.2


def test_authority_score_log_scales_citations():
    # 1000 citations -> log10(1001)/3 ~ 1.0, already capped; 100 -> ~0.67.
    assert search_service._authority_score(100, 2025) > search_service._authority_score(10, 2025)
    assert search_service._authority_score(10000, 2025) >= search_service._authority_score(100, 2025)


def test_openalex_fills_cited_by_count_and_authority(monkeypatch):
    canned = {
        "results": [
            {
                "title": "Attention Is All You Need",
                "authorships": [{"author": {"display_name": "Vaswani"}}],
                "publication_year": 2017,
                "doi": "https://doi.org/10.5555/3295222",
                "primary_location": {"source": {"display_name": "NeurIPS"}},
                "abstract_inverted_index": None,
                "id": "https://openalex.org/W1",
                "locations": [],
                "open_access": {"oa_status": "green"},
                "cited_by_count": 50000,
            }
        ]
    }
    monkeypatch.setattr(search_service, "_safe_get_json", lambda *a, **k: canned)
    result = search_service._search_openalex("transformer", 10)[0]
    assert result.cited_by_count == 50000
    assert result.relevance_score == pytest.approx(search_service._authority_score(50000, 2017), abs=1e-4)


def test_crossref_fills_is_referenced_by_count(monkeypatch):
    canned = {
        "message": {
            "items": [
                {
                    "title": ["A Benchmark Paper"],
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "issued": {"date-parts": [[2020]]},
                    "DOI": "10.1000/bench",
                    "container-title": ["Nature"],
                    "abstract": "abstract text",
                    "URL": "https://doi.org/10.1000/bench",
                    "is-referenced-by-count": 12345,
                }
            ]
        }
    }
    monkeypatch.setattr(search_service, "_safe_get_json", lambda *a, **k: canned)
    result = search_service._search_crossref("benchmark", 10)[0]
    assert result.cited_by_count == 12345
    assert result.relevance_score == pytest.approx(search_service._authority_score(12345, 2020), abs=1e-4)


def test_arxiv_falls_back_to_recency_only(monkeypatch):
    atom_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        "<entry>"
        "<title>Fresh Preprint</title>"
        "<summary>No citation count exists on arXiv.</summary>"
        "<id>http://arxiv.org/abs/2601.00001v1</id>"
        "<published>2026-01-01T00:00:00Z</published>"
        '<link rel="related" type="application/pdf" href="http://arxiv.org/pdf/2601.00001v1"/>'
        "</entry>"
        "</feed>"
    )
    monkeypatch.setattr(search_service, "_safe_get_text", lambda *a, **k: atom_xml)
    result = search_service._search_arxiv("preprint", 10)[0]
    assert result.cited_by_count == 0
    assert result.relevance_score == search_service._authority_score(0, 2026)


def test_search_papers_ranks_high_citation_work_first(monkeypatch):
    """Blend must use authority (30%), not the old position-fake score."""
    low = _candidate("10.1/a", "Paper Alpha", cited=0)
    high = _candidate("10.1/b", "Paper Beta", cited=3000)

    monkeypatch.setattr(search_service, "_build_query_variants", lambda q: [q])
    monkeypatch.setattr(search_service, "_search_openalex", lambda q, limit: [low, high])
    monkeypatch.setattr(search_service, "_search_crossref", lambda q, limit: [])
    monkeypatch.setattr(search_service, "_search_arxiv", lambda q, limit: [])
    monkeypatch.setattr(search_service, "_query_match_score", lambda q, item: 0.5)
    monkeypatch.setattr(search_service, "_facet_coverage_ratio", lambda q, item: (1.0, 1))

    ranked = search_service.search_papers("test query", limit=2)
    assert [p.doi for p in ranked] == ["10.1/b", "10.1/a"]
