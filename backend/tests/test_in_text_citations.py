"""Tests for in-text citation rendering (批次5).

Previously ``citation_style`` was accepted by the writer but never used: the
final draft stripped evidence comments and carried no citations at all.
``render_in_text_citations`` now converts ``<!-- evidence: id -->`` markers
into ``[N]`` citations and appends a references section in the project's
citation style.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.citation_service import render_in_text_citations


def _paper(title, year=2024, doi="10.1000/xyz", authors=("Zhang, San",), venue="Nature"):
    return SimpleNamespace(
        id="p-" + title[:6].replace(" ", ""),
        title=title,
        authors=list(authors),
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id="",
        source_url="https://example.com",
        pdf_url="",
    )


def _card(card_id, paper):
    return SimpleNamespace(id=card_id, paper=paper)


def test_citations_rendered_by_first_appearance_and_paper_shared():
    p1 = _paper("Deep Learning Advances")
    p2 = _paper("Benchmark Study")
    cards = [_card("e1", p1), _card("e2", p2), _card("e3", p1)]

    content = "甲论断<!-- evidence: e2 -->\n\n乙论断<!-- evidence: e1 -->\n\n丙论断<!-- evidence: e3 -->"
    out = render_in_text_citations(content, cards, "GB/T 7714")

    # First appearance: e2 -> [1], e1 -> [2], e3 shares e1's paper number.
    assert "甲论断[1]" in out
    assert "乙论断[2]" in out
    assert "丙论断[2]" in out
    # References section appended, numbered, GB/T 7714 formatted.
    assert "## 参考文献" in out
    assert "[1] Zhang, San. 2024. Benchmark Study." in out
    assert "[2] Zhang, San. 2024. Deep Learning Advances." in out
    assert "DOI:10.1000/xyz" in out
    # No leftover comment markers.
    assert "evidence:" not in out


def test_non_paper_card_marker_removed_without_reference():
    cards = [_card("e1", None)]
    out = render_in_text_citations("论断<!-- evidence: e1 -->", cards, "APA 7")
    assert "论断" in out
    assert "[1]" not in out and "## 参考文献" not in out


def test_multiple_ids_in_one_marker_collapse():
    p1 = _paper("Paper One")
    p2 = _paper("Paper Two")
    cards = [_card("e1", p1), _card("e2", p2)]
    out = render_in_text_citations("论断<!-- evidence: e1, e2 -->", cards, "GB/T 7714")
    assert "论断[1,2]" in out


def test_no_cards_returns_content_unchanged():
    out = render_in_text_citations("正文", [], "GB/T 7714")
    assert out.strip() == "正文"


def test_style_alias_apa():
    p1 = _paper("Alias Paper")
    out = render_in_text_citations("x<!-- evidence: e1 -->", [_card("e1", p1)], "APA 7")
    assert "## 参考文献" in out
    assert "(2024)." in out  # APA year format
    assert "https://doi.org/10.1000/xyz" in out
