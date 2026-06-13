"""External fact-checking service.

Verifies:
- DOI existence and metadata match via CrossRef
- Factual claims against Wikipedia (lightweight)
- Hallucination-prone patterns (numbers, dates, proper nouns)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.citation_service import query_crossref
from app.services.http_client import create_httpx_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

_CLAIM_RE = re.compile(
    r"(\d{4}年|\d{4}|"
    r"第?\d+\s*[倍百千百万亿]?\s*[个个条项次人种]"
    r"|[A-Z][a-z]+\s+[A-Z][a-z]+"  # proper names (English)
    r"|[一-鿿]{2,6}(?:模型|算法|方法|理论|框架|系统))"
)

_HALLUCINATION_PATTERNS = [
    # Only flag vague authority claims without any supporting evidence nearby
    re.compile(r"\b据相关研究\s*显示\b"),
    re.compile(r"\b有研究\s*表明\b"),
    re.compile(r"\b实验证明\b"),
]


# ---------------------------------------------------------------------------
# CrossRef DOI verification
# ---------------------------------------------------------------------------


def verify_doi(doi: str) -> dict[str, Any]:
    """Verify a DOI exists and return metadata.

    Returns {"valid": bool, "metadata": dict, "confidence": float}.
    """
    if not doi:
        return {"valid": False, "metadata": {}, "confidence": 0.0}
    meta = query_crossref(doi)
    if meta and meta.get("title"):
        return {"valid": True, "metadata": meta, "confidence": 1.0}
    return {"valid": False, "metadata": {}, "confidence": 0.0}


def verify_evidence_dois(evidence_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch-verify DOIs from evidence cards.

    Returns a list of issue dicts for any invalid/unverifiable DOI.
    """
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in evidence_cards:
        doi = str(card.get("doi") or "").strip()
        if not doi or doi in seen:
            continue
        seen.add(doi)
        result = verify_doi(doi)
        if not result["valid"]:
            issues.append({
                "severity": "medium",
                "issue_type": "fact",
                "location": "global",
                "claim": f"DOI {doi}",
                "description": f"证据卡引用的 DOI '{doi}' 在 CrossRef 中未找到，可能为伪造或过期。",
                "suggestion": "请核实 DOI 准确性，或补充原始文献信息。",
                "evidence_ids": [str(card.get("id"))] if card.get("id") else [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# Wikipedia quick check
# ---------------------------------------------------------------------------


def _wiki_search(query: str, lang: str = "zh", timeout: float = 8.0) -> list[dict[str, Any]]:
    """Search Wikipedia for a query term. Returns list of result dicts."""
    domain = f"{lang}.wikipedia.org"
    url = f"https://{domain}/w/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": 3,
        "srprop": "",
    }
    try:
        with create_httpx_client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Wikipedia search failed for '%s': %s", query, exc)
        return []

    results = data.get("query", {}).get("search", [])
    return [{"title": r.get("title"), "snippet": r.get("snippet", "")} for r in results]


def _wiki_page_exists(title: str, lang: str = "zh", timeout: float = 8.0) -> bool:
    """Check if a Wikipedia page exists."""
    domain = f"{lang}.wikipedia.org"
    url = f"https://{domain}/w/api.php"
    params = {
        "action": "query",
        "titles": title,
        "format": "json",
        "prop": "info",
    }
    try:
        with create_httpx_client(timeout=timeout) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return False

    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if page.get("missing") is None:
            return True
    return False


def verify_proper_nouns(text: str) -> list[dict[str, Any]]:
    """Extract likely proper nouns and check them against Wikipedia.

    This is a lightweight heuristic — only applied to terms that look
    like they might be hallucinated (unusual combinations).
    """
    issues: list[dict[str, Any]] = []
    # Only check English capitalized phrases (2-4 words) — these are more
    # likely to be hallucinated model/method names than CJK terms.
    eng_terms = set(re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", text))

    checked: set[str] = set()
    for term in list(eng_terms)[:8]:
        if term in checked:
            continue
        # Skip common academic phrases that are likely real
        lower = term.lower()
        if any(skip in lower for skip in [
            "neural network", "deep learning", "machine learning",
            "natural language", "artificial intelligence", "computer vision",
            "reinforcement learning", "support vector", "decision tree",
            "random forest", "gradient descent", "cross validation",
            "linear regression", "logistic regression", "bayesian",
        ]):
            continue
        checked.add(term)
        results = _wiki_search(term)
        if not results:
            issues.append({
                "severity": "low",
                "issue_type": "fact",
                "location": "global",
                "claim": term,
                "description": f"英文术语 '{term}' 在 Wikipedia 中未找到对应条目，可能是生造术语或拼写错误。",
                "suggestion": "请核实术语拼写，或提供更通用的同义表达。",
                "evidence_ids": [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# Number / date consistency
# ---------------------------------------------------------------------------


def check_number_consistency(text: str) -> list[dict[str, Any]]:
    """Detect suspicious numeric claims that should have sources."""
    issues: list[dict[str, Any]] = []

    # Unsourced percentages
    pct_matches = list(re.finditer(r"(\d{1,3}(?:\.\d+)?)\s*%", text))
    for m in pct_matches:
        # Check if nearby has evidence or citation
        start = max(0, m.start() - 100)
        end = min(len(text), m.end() + 100)
        context = text[start:end]
        if "evidence" not in context.lower() and "[" not in context and "doi" not in context.lower():
            issues.append({
                "severity": "low",
                "issue_type": "fact",
                "location": "global",
                "claim": m.group(0),
                "description": f"百分比数据 '{m.group(0)}' 附近未检测到引用或证据注释。",
                "suggestion": "为具体数字添加证据来源或 DOI 引用。",
                "evidence_ids": [],
                "resolved": False,
            })

    # Unsourced years in the future (suspicious)
    year_matches = list(re.finditer(r"\b(20[3-9]\d)\b", text))
    for m in year_matches:
        year = int(m.group(1))
        if year > 2030:  # Far-future dates are often hallucinations
            issues.append({
                "severity": "medium",
                "issue_type": "fact",
                "location": "global",
                "claim": m.group(0),
                "description": f"引用了较远的未来年份 {year}，需确认是否为预测性声明而非事实。",
                "suggestion": "如为未来预测，请明确标注为展望；如为事实，请提供来源。",
                "evidence_ids": [],
                "resolved": False,
            })

    return issues


# ---------------------------------------------------------------------------
# Hallucination-pattern detection
# ---------------------------------------------------------------------------


def check_hallucination_markers(text: str) -> list[dict[str, Any]]:
    """Flag vague authority claims that are common AI hallucination patterns."""
    issues: list[dict[str, Any]] = []
    for pattern in _HALLUCINATION_PATTERNS:
        for m in pattern.finditer(text):
            issues.append({
                "severity": "low",
                "issue_type": "fact",
                "location": "global",
                "claim": m.group(0),
                "description": f"使用了模糊权威表述 '{m.group(0)}'，常见于 AI 幻觉。",
                "suggestion": "替换为具体的文献引用（作者+年份），或使用限定语。",
                "evidence_ids": [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fact_check_draft(
    content_md: str,
    evidence_cards: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run external fact-checking suite on a draft.

    Returns (issues, metrics).
    """
    all_issues: list[dict[str, Any]] = []

    if evidence_cards:
        all_issues.extend(verify_evidence_dois(evidence_cards))

    all_issues.extend(verify_proper_nouns(content_md))
    all_issues.extend(check_number_consistency(content_md))
    all_issues.extend(check_hallucination_markers(content_md))

    metrics = {
        "fact_check_issue_count": len(all_issues),
        "unverifiable_dois": len([i for i in all_issues if "DOI" in i.get("claim", "")]),
        "unverifiable_terms": len([i for i in all_issues if "术语" in i.get("description", "")]),
        "unsourced_numbers": len([i for i in all_issues if "数字" in i.get("description", "")]),
        "hallucination_markers": len([i for i in all_issues if "幻觉" in i.get("description", "")]),
    }
    return all_issues, metrics
