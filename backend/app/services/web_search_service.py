"""Web search service for gathering evidence from internet sources.

Uses DuckDuckGo for search (no API key required) and httpx + BeautifulSoup
for page content extraction. Creates "virtual paper" records with source_type="web".
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Timeout for web page fetches
WEB_FETCH_TIMEOUT = 15.0
SEARCH_TIMEOUT = 20.0
MAX_WEB_RESULTS = 12


def search_web(query: str, max_results: int = MAX_WEB_RESULTS) -> list[dict[str, Any]]:
    """Search the web using DuckDuckGo and return results with extracted text.

    Returns a list of dicts with: title, url, snippet, full_text, source_domain
    """
    results: list[dict[str, Any]] = []

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("duckduckgo-search not installed, skipping web search")
        return results

    try:
        with DDGS(timeout=SEARCH_TIMEOUT) as ddgs:
            raw_results = list(ddgs.text(query, max_results=max_results))
    except Exception:
        logger.exception("DuckDuckGo search failed")
        return results

    for item in raw_results:
        title = (item.get("title") or "").strip()
        url = (item.get("href") or "").strip()
        snippet = (item.get("body") or "").strip()

        if not url or not title:
            continue

        # Extract domain for source attribution
        domain = _extract_domain(url)

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_domain": domain,
            "source_type": "web",
            "full_text": None,  # Will be fetched later
        })

    return results


def fetch_page_text(url: str, timeout: float = WEB_FETCH_TIMEOUT) -> str | None:
    """Fetch and extract readable text from a web page."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.debug("Failed to fetch page: %s", url, exc_info=True)
        return None

    return _extract_text_from_html(html)


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, removing scripts, styles, and navigation."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside", "iframe", "noscript"]):
        tag.decompose()

    # Try to find main content area
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find(attrs={"class": re.compile(r"(content|article|post|entry|body)", re.I)})
    )

    if main_content:
        text = main_content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Clean up: remove excessive blank lines and whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    cleaned = "\n".join(lines)

    # Limit to ~5000 chars to avoid excessive token usage
    return cleaned[:5000]


def _extract_domain(url: str) -> str:
    """Extract domain name from URL."""
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "unknown"


def build_web_evidence(
    project_id: str,
    web_results: list[dict[str, Any]],
    db: Any,
) -> list[Any]:
    """Create Paper + EvidenceCard records from web search results.

    Creates lightweight "virtual paper" records for web sources, then
    builds evidence cards from the content.
    """
    from app.models import Paper, EvidenceCard
    from app.services.evidence_service import build_evidence_from_chunks, infer_evidence_type, infer_strength
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    created_evidence = []

    for result in web_results:
        title = result["title"]
        url = result["url"]
        snippet = result.get("snippet", "")
        full_text = result.get("full_text")

        # Create a virtual Paper record for this web source
        paper_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            project_id=project_id,
            title=title,
            authors=[],
            year=None,
            doi=None,
            arxiv_id=None,
            venue=result.get("source_domain", "web"),
            abstract=snippet[:2000] if snippet else None,
            source="web_search",
            source_type="web",
            source_url=url,
            pdf_url=None,
            oa_status=None,
            license=None,
            local_pdf_path=None,
            local_tei_path=None,
            relevance_score=0.5,
            selected=True,
            parse_status="parsed",
            metadata_json={"source_domain": result.get("source_domain", ""), "web_url": url},
            created_at=now,
            updated_at=now,
        )
        db.add(paper)

        # Build evidence from the text content
        text_for_evidence = full_text if full_text and len(full_text) > 100 else snippet
        if not text_for_evidence or len(text_for_evidence) < 40:
            continue

        # Create chunk-like payload for evidence building
        chunk_payload = [{
            "id": str(uuid4()),
            "text": text_for_evidence[:2400],
            "page_start": None,
            "page_end": None,
        }]

        evidence_items = build_evidence_from_chunks(paper_id, chunk_payload, limit=3)
        for item in evidence_items:
            ev = EvidenceCard(
                id=str(uuid4()),
                project_id=project_id,
                paper_id=paper_id,
                chunk_ids=item["chunk_ids"],
                claim=item["claim"],
                supporting_text=item["supporting_text"],
                evidence_type=item.get("evidence_type", "web_source"),
                source_type="web",
                strength=item.get("strength", "medium"),
                limitations="Web source evidence; credibility should be verified.",
                page_start=None,
                page_end=None,
                citation_key=None,
                used_in_draft=False,
                created_at=now,
                updated_at=now,
            )
            db.add(ev)
            created_evidence.append(ev)

    return created_evidence
