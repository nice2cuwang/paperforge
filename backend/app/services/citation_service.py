"""Citation formatting and DOI resolution service.

Supports GB/T 7714, APA 7, MLA 9, and BibTeX output.
Can query CrossRef to enrich incomplete metadata.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.http_client import create_httpx_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Author utilities
# ---------------------------------------------------------------------------


def _normalize_authors(authors: list[Any]) -> list[str]:
    """Normalize a list of author strings/dicts into 'Family, Given' form."""
    result: list[str] = []
    for a in authors:
        if isinstance(a, dict):
            given = str(a.get("given") or a.get("first") or "").strip()
            family = str(a.get("family") or a.get("last") or "").strip()
            if family and given:
                result.append(f"{family}, {given}")
            elif family:
                result.append(family)
            elif given:
                result.append(given)
        else:
            text = str(a).strip()
            if text:
                result.append(text)
    return result


def _author_surname(authors: list[str]) -> str:
    """Extract the surname of the first author."""
    if not authors:
        return ""
    first = authors[0]
    if "," in first:
        return first.split(",", 1)[0].strip()
    parts = first.split()
    return parts[-1] if parts else ""


def _author_initials(authors: list[str]) -> list[str]:
    """Return 'G. Family' for each author."""
    out: list[str] = []
    for au in authors:
        if "," in au:
            family, given = au.split(",", 1)
            family = family.strip()
            given = given.strip()
            initials = "".join(p[0] + "." for p in given.split() if p)
            out.append(f"{initials} {family}".strip())
        else:
            out.append(au)
    return out


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _gb7714(paper: Any) -> str:
    """GB/T 7714-2015 格式."""
    authors = _normalize_authors(paper.authors or [])
    author_text = ", ".join(authors) if authors else "佚名"
    year = paper.year or ""
    title = paper.title or "Untitled"
    venue = paper.venue or ""
    doi = paper.doi or ""
    arxiv = paper.arxiv_id or ""

    parts = [author_text]
    if year:
        parts.append(str(year))
    parts.append(title)
    if venue:
        parts.append(venue)
    if doi:
        parts.append(f"DOI:{doi}")
    elif arxiv:
        parts.append(f"arXiv:{arxiv}")
    return ". ".join(parts) + "."


def _apa7(paper: Any) -> str:
    """APA 7th edition 格式."""
    authors = _normalize_authors(paper.authors or [])
    if not authors:
        author_text = "佚名"
    elif len(authors) == 1:
        author_text = _author_initials(authors)[0]
    elif len(authors) == 2:
        a1, a2 = _author_initials(authors)
        author_text = f"{a1} & {a2}"
    else:
        a1 = _author_initials(authors)[0]
        author_text = f"{a1} et al."

    year = f"({paper.year})." if paper.year else ""
    title = paper.title or "Untitled"
    venue = paper.venue or ""
    doi = paper.doi or ""
    url = paper.source_url or paper.pdf_url or ""

    parts = [author_text, year, title]
    if venue:
        parts.append(f"*{_italic(venue)}*")
    if doi:
        parts.append(f"https://doi.org/{doi}")
    elif url:
        parts.append(url)
    return " ".join(parts)


def _mla9(paper: Any) -> str:
    """MLA 9th edition 格式."""
    authors = _normalize_authors(paper.authors or [])
    if not authors:
        author_text = "佚名"
    elif len(authors) == 1:
        author_text = _author_surname(authors)
    elif len(authors) == 2:
        a1, a2 = _author_surname(authors), _author_surname(authors[1:])
        author_text = f"{_author_surname([authors[0]])} and {_author_surname([authors[1]])}"
    else:
        author_text = f"{_author_surname(authors)} et al."

    year = f", {paper.year}" if paper.year else ""
    title = f'"{paper.title or "Untitled"}"'
    venue = paper.venue or ""
    doi = paper.doi or ""
    url = paper.source_url or paper.pdf_url or ""

    parts = [f"{author_text}{year}", title]
    if venue:
        parts.append(f"*{_italic(venue)}*")
    if doi:
        parts.append(f"doi:{doi}")
    elif url:
        parts.append(url)
    return ", ".join(parts) + "."


def _bibtex(paper: Any) -> str:
    """BibTeX entry."""
    authors = _normalize_authors(paper.authors or [])
    author_text = " and ".join(authors) if authors else "Unknown"
    key_source = paper.doi or paper.arxiv_id or f"paper_{paper.id[:8]}"
    key = str(key_source).replace("/", "_").replace(":", "_")
    lines = [
        f"@article{{{key},",
        f"  title = {{{paper.title or 'Untitled'}}},",
        f"  author = {{{author_text}}},",
    ]
    if paper.year:
        lines.append(f"  year = {{{paper.year}}},")
    if paper.venue:
        lines.append(f"  journal = {{{paper.venue}}},")
    if paper.doi:
        lines.append(f"  doi = {{{paper.doi}}},")
    if paper.arxiv_id:
        lines.append(f"  eprint = {{{paper.arxiv_id}}},")
        lines.append("  archivePrefix = {arXiv},")
    lines.append("}")
    return "\n".join(lines)


def _italic(text: str) -> str:
    return text  # Markdown 斜体由调用方处理


_FORMATTERS: dict[str, Any] = {
    "gb7714": _gb7714,
    "apa7": _apa7,
    "mla9": _mla9,
    "bibtex": _bibtex,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def format_citation(paper: Any, style: str = "gb7714") -> str:
    """Format a single paper into the requested citation style."""
    fmt = _FORMATTERS.get(style, _gb7714)
    return fmt(paper)


def format_bibliography(papers: list[Any], style: str = "gb7714") -> str:
    """Format a list of papers into a bibliography string."""
    if not papers:
        return ""
    fmt = _FORMATTERS.get(style, _gb7714)
    entries = [fmt(p) for p in papers]
    if style == "bibtex":
        return "\n\n".join(entries)
    return "\n".join(f"[{i + 1}] {entry}" for i, entry in enumerate(entries))


# ---------------------------------------------------------------------------
# In-text citation rendering
# ---------------------------------------------------------------------------

_EVIDENCE_COMMENT_RE = re.compile(r"<!--\s*evidence:\s*([^>]+?)\s*-->", re.IGNORECASE)

# Project-level citation_style strings (free-form UI input) -> formatter keys.
_CITATION_STYLE_ALIASES = {
    "gb/t 7714": "gb7714", "gb7714": "gb7714", "gb/t7714": "gb7714", "国标": "gb7714",
    "g b/t 7714": "gb7714",
    "apa": "apa7", "apa7": "apa7", "apa 7": "apa7", "apa 7th": "apa7",
    "mla": "mla9", "mla9": "mla9", "mla 9": "mla9",
}


def _normalize_citation_style(style: str | None) -> str:
    key = re.sub(r"\s+", " ", str(style or "").strip().lower())
    return _CITATION_STYLE_ALIASES.get(key, "gb7714")


def render_in_text_citations(content_md: str, cards: Any, citation_style: str | None = None) -> str:
    """Turn ``<!-- evidence: id -->`` markers into visible ``[N]`` citations
    and append a formatted references section.

    *cards* is any iterable of evidence-card objects exposing ``id`` and a
    ``paper`` relationship (paper-backed academic cards). Markers whose card
    has no backing paper (web/community sources) are simply removed; the
    BibTeX export keeps the full per-paper list unchanged. Numbering follows
    first appearance in the text, with all cards of one paper sharing a number.
    """
    card_paper: dict[str, Any] = {}
    knowledge_cited = False
    for card in cards:
        try:
            paper = getattr(card, "paper", None)
        except Exception:
            paper = None
        if paper is not None:
            # llm_knowledge 证据背后的 paper 是虚拟占位（无作者/年份/出处），
            # 「佚名. xxx. llm_knowledge.」不是可核验的引用，不进参考文献。
            source_type = str(getattr(card, "source_type", "") or "").lower()
            if source_type == "llm_knowledge":
                knowledge_cited = True
                continue
            card_paper[str(card.id)] = paper

    paper_number: dict[int, int] = {}
    ordered_papers: list[Any] = []

    def _cite_number(paper: Any) -> int:
        # Key by object identity: ORM papers are unhashable SimpleNamespace-like
        # instances and one paper object is shared by all its cards.
        key = id(paper)
        if key not in paper_number:
            paper_number[key] = len(ordered_papers) + 1
            ordered_papers.append(paper)
        return paper_number[key]

    def _replace(match: re.Match[str]) -> str:
        ids = [part.strip() for part in match.group(1).split(",") if part.strip()]
        numbers: list[int] = []
        for ev_id in ids:
            paper = card_paper.get(ev_id)
            if paper is not None:
                num = _cite_number(paper)
                if num not in numbers:
                    numbers.append(num)
        if not numbers:
            return ""
        numbers.sort()
        return "[" + ",".join(str(n) for n in numbers) + "]"

    cited = _EVIDENCE_COMMENT_RE.sub(_replace, content_md)

    if not ordered_papers:
        if knowledge_cited:
            return (
                cited.rstrip()
                + "\n\n## 参考文献\n\n本文部分内容基于模型已有知识整理，无可引用的外部文献；请读者核实关键事实。\n"
            )
        return cited
    style = _normalize_citation_style(citation_style)
    bibliography = format_bibliography(ordered_papers, style=style)
    out = cited.rstrip() + "\n\n## 参考文献\n\n" + bibliography + "\n"
    if knowledge_cited:
        out += "\n> 注：文中标注「基于模型已有知识」的段落由 AI 生成，不属于上述参考文献的支撑范围。\n"
    return out


def query_crossref(doi: str, timeout: float = 10.0) -> dict[str, Any]:
    """Query CrossRef API for DOI metadata.

    Returns a dict with keys: title, authors, year, venue, doi, url, or
    an empty dict on failure.
    """
    if not doi:
        return {}
    url = f"https://api.crossref.org/works/{doi}"
    try:
        with create_httpx_client(timeout=timeout) as client:
            resp = client.get(url, headers={"User-Agent": "PaperForge/0.2 (mailto:contact@example.com)"})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("CrossRef query failed for DOI %s: %s", doi, exc)
        return {}

    message = data.get("message", {})
    title_list = message.get("title", [])
    title = title_list[0] if title_list else ""

    authors_raw = message.get("author", [])
    authors = []
    for au in authors_raw:
        given = au.get("given", "")
        family = au.get("family", "")
        if given and family:
            authors.append(f"{family}, {given}")
        elif family:
            authors.append(family)

    year = None
    published = message.get("published-print") or message.get("published-online") or {}
    date_parts = published.get("date-parts", [[]])
    if date_parts and date_parts[0]:
        year = date_parts[0][0]

    venue = ""
    container = message.get("container-title", [])
    if container:
        venue = container[0]

    url = message.get("URL", "")

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "url": url,
    }


def enrich_paper_metadata(paper: Any) -> dict[str, Any]:
    """Try to enrich a paper's metadata via CrossRef if DOI is present.

    Returns the enrichment delta (what changed) without mutating the paper.
    """
    if not paper.doi:
        return {}
    crossref = query_crossref(paper.doi)
    if not crossref:
        return {}
    delta: dict[str, Any] = {}
    if crossref.get("title") and not paper.title:
        delta["title"] = crossref["title"]
    if crossref.get("authors") and not paper.authors:
        delta["authors"] = crossref["authors"]
    if crossref.get("year") and not paper.year:
        delta["year"] = crossref["year"]
    if crossref.get("venue") and not paper.venue:
        delta["venue"] = crossref["venue"]
    if crossref.get("url") and not paper.source_url:
        delta["source_url"] = crossref["url"]
    return delta
