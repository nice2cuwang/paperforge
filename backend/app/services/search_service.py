from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Any
import xml.etree.ElementTree as ET

from app.services.http_client import create_httpx_client


@dataclass
class PaperCandidate:
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    venue: str | None
    abstract: str | None
    source: str
    source_url: str | None
    pdf_url: str | None
    oa_status: str | None
    license: str | None
    relevance_score: float

    def key(self) -> str:
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        return f"title:{normalize_title(self.title)}"


FACET_LIBRARY: dict[str, dict[str, list[str]]] = {
    "ai_model": {
        "triggers": [
            "ai",
            "人工智能",
            "大模型",
            "llm",
            "machine learning",
            "deep learning",
            "neural network",
            "机器学习",
            "深度学习",
        ],
        "signals": [
            "ai",
            "artificial intelligence",
            "machine learning",
            "deep learning",
            "large language model",
            "llm",
            "transformer",
            "人工智能",
            "机器学习",
            "深度学习",
            "大模型",
        ],
    },
    "learning_path": {
        "triggers": [
            "学习路线",
            "学习路径",
            "路线图",
            "roadmap",
            "learning path",
            "pathway",
            "curriculum",
            "course sequence",
            "培养方案",
        ],
        "signals": [
            "learning path",
            "learning roadmap",
            "curriculum",
            "course design",
            "syllabus",
            "pathway",
            "curricular",
            "learning trajectory",
            "学习路线",
            "学习路径",
            "路线图",
            "课程体系",
            "培养方案",
        ],
    },
    "college_student": {
        "triggers": [
            "大学生",
            "本科生",
            "高校",
            "college student",
            "undergraduate",
            "university student",
            "higher education",
        ],
        "signals": [
            "college student",
            "undergraduate",
            "university student",
            "higher education",
            "tertiary education",
            "大学生",
            "本科生",
            "高校",
            "高等教育",
        ],
    },
}


# Common CJK function words — removing them early prevents noisy bigrams
# when long CJK phrases are tokenised.
_CJK_STOPWORDS: set[str] = {
    "的", "了", "在", "是", "和", "与", "为", "对", "等", "之",
    "及", "或", "有", "一个", "一种", "以及", "对于", "关于",
    "通过", "进行", "采用", "基于", "使用", "提出", "实现",
}


def normalize_title(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else " " for ch in value)
    # Keep CJK character sequences intact so multi-character words survive.
    # Only insert boundaries between CJK and Latin/digit tokens.
    text = re.sub(r"([a-z0-9])([一-鿿])", r"\1 \2", text)
    text = re.sub(r"([一-鿿])([a-z0-9])", r"\1 \2", text)
    # Strip common CJK function words to reduce noise in tokenisation
    for sw in _CJK_STOPWORDS:
        text = text.replace(sw, " ")
    return " ".join(text.split())


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value).strip() or None


def _safe_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"User-Agent": "PaperForge/0.2 (+https://paperforge.local)"}
    with create_httpx_client(timeout=6.0, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _safe_get_text(url: str, params: dict[str, Any] | None = None) -> str:
    headers = {"User-Agent": "PaperForge/0.2 (+https://paperforge.local)"}
    with create_httpx_client(timeout=6.0, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.text


def _contains_term(text: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9 ]+", term):
        compact = " ".join(term.split())
        if not compact:
            return False
        return re.search(rf"(?<![a-z0-9]){re.escape(compact)}(?![a-z0-9])", text) is not None
    return term in text


def _extract_query_terms(query: str) -> list[str]:
    normalized = normalize_title(query)
    if not normalized:
        return []

    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "what",
        "how",
        "why",
        "where",
        "when",
        "study",
        "research",
        "about",
        "paper",
        "report",
        # CJK stopwords — common function words that carry no semantic weight
        "的",
        "了",
        "在",
        "是",
        "和",
        "与",
        "为",
        "对",
        "等",
        "之",
        "及",
        "或",
        "有",
        "一个",
    }

    terms: list[str] = []
    for raw in normalized.split():
        token = raw.strip()
        if not token or token in stopwords:
            continue
        if re.fullmatch(r"[a-z0-9]+", token):
            if len(token) >= 2:
                terms.append(token)
            continue
        # For non-Latin tokens (CJK, etc.): single characters have no
        # discriminative power and cause every CJK document to look like a match.
        if len(token) < 2:
            continue
        # Keep CJK phrases up to 6 chars intact (common technical terms like
        # "自然语言处理").  Only shard extremely long runs into bigrams.
        if len(token) <= 6:
            terms.append(token)
        else:
            terms.extend(token[i : i + 2] for i in range(0, len(token) - 1))

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def _detect_required_facets(query: str) -> list[str]:
    text = normalize_title(query)
    required: list[str] = []
    for facet, data in FACET_LIBRARY.items():
        if any(_contains_term(text, normalize_title(trigger)) for trigger in data["triggers"]):
            required.append(facet)
    return required


# Facets whose signals are genuine technical/academic synonyms worth
# blending into search queries.  Audience/intent facets (learning_path,
# college_student) expand to generic terms that lose the original subject.
_SEARCH_EXPAND_FACETS = {"ai_model"}


def _build_query_variants(query: str) -> list[str]:
    base = query.strip()
    if not base:
        return [query]

    variants = [base]

    key_terms = _extract_query_terms(base)
    if key_terms:
        # Focused variant: drop noise words so databases match the salient concepts
        variants.append(" ".join(key_terms[:8]))

    required = _detect_required_facets(base)
    # For CJK-heavy queries, facet expansion tends to produce generic
    # English phrases (e.g. "learning path curriculum") that ignore the
    # core entity (e.g. "deepseek").  Skip expansion in that case.
    cjk_ratio = sum(1 for ch in base if "一" <= ch <= "鿿") / max(1, len(base))
    if cjk_ratio < 0.35 and required:
        english_terms: list[str] = []
        for facet in required:
            if facet not in _SEARCH_EXPAND_FACETS:
                continue
            for signal in FACET_LIBRARY[facet]["signals"]:
                signal_norm = normalize_title(signal)
                if not signal_norm:
                    continue
                if re.fullmatch(r"[a-z0-9 ]+", signal_norm):
                    english_terms.append(signal_norm)
        if english_terms:
            variants.append(" ".join(dict.fromkeys(english_terms[:8])))
            variants.append(f"{base} {' '.join(dict.fromkeys(english_terms[:5]))}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        candidate = " ".join(item.split()).strip()
        if len(candidate) < 2:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped[:4]


def _decode_openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    if not inverted_index:
        return None
    position_words: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            position_words.append((int(pos), word))
    if not position_words:
        return None
    position_words.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in position_words)


def _search_openalex(query: str, limit: int) -> list[PaperCandidate]:
    data = _safe_get_json(
        "https://api.openalex.org/works",
        {
            "search": query,
            "per-page": min(limit, 35),
            "filter": "has_abstract:true",
            "sort": "relevance_score:desc",
        },
    )
    results: list[PaperCandidate] = []
    for idx, work in enumerate(data.get("results", []), start=1):
        pdf_url = None
        locations = work.get("locations") or []
        for loc in locations:
            pdf_url = _clean((loc.get("pdf_url") or loc.get("landing_page_url")))
            if pdf_url:
                break
        results.append(
            PaperCandidate(
                title=_clean(work.get("title")) or "Untitled",
                authors=[
                    _clean(author_item.get("author", {}).get("display_name")) or "Unknown"
                    for author_item in (work.get("authorships") or [])
                ],
                year=work.get("publication_year"),
                doi=_clean(work.get("doi")),
                arxiv_id=None,
                venue=_clean((work.get("primary_location") or {}).get("source", {}).get("display_name")),
                abstract=_decode_openalex_abstract(work.get("abstract_inverted_index")),
                source="openalex",
                source_url=_clean(work.get("id")),
                pdf_url=pdf_url,
                oa_status=_clean((work.get("open_access") or {}).get("oa_status")),
                license=_clean((work.get("open_access") or {}).get("oa_status")),
                relevance_score=max(0.0, 1.0 - (idx * 0.02)),
            )
        )
    return results


def _search_crossref(query: str, limit: int) -> list[PaperCandidate]:
    data = _safe_get_json(
        "https://api.crossref.org/works",
        {
            "query.bibliographic": query,
            "rows": min(limit, 25),
            "filter": "type:journal-article",
            "mailto": "paperforge@local.dev",
        },
    )
    results: list[PaperCandidate] = []
    for idx, work in enumerate(data.get("message", {}).get("items", []), start=1):
        title_list = work.get("title") or []
        authors: list[str] = []
        for author in (work.get("author") or []):
            name = " ".join(filter(None, [author.get("given"), author.get("family")])).strip()
            if name:
                authors.append(name)
        year = None
        date_parts = (work.get("issued") or {}).get("date-parts") or []
        if date_parts and date_parts[0]:
            year = date_parts[0][0]
        results.append(
            PaperCandidate(
                title=_clean(title_list[0] if title_list else None) or "Untitled",
                authors=authors,
                year=year,
                doi=_clean(work.get("DOI")),
                arxiv_id=None,
                venue=_clean(((work.get("container-title") or [None])[0])),
                abstract=_clean(work.get("abstract")),
                source="crossref",
                source_url=_clean(work.get("URL")),
                pdf_url=None,
                oa_status="unknown",
                license=None,
                relevance_score=max(0.0, 0.88 - (idx * 0.02)),
            )
        )
    return results


def _search_arxiv(query: str, limit: int) -> list[PaperCandidate]:
    xml_text = _safe_get_text(
        "https://export.arxiv.org/api/query",
        {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(limit, 20),
        },
    )
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    results: list[PaperCandidate] = []
    for idx, entry in enumerate(root.findall("atom:entry", ns), start=1):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").replace("\n", " ").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").replace("\n", " ").strip()
        identifier = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
        published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None
        arxiv_id = identifier.rsplit("/", 1)[-1] if identifier else None
        doi = _clean(entry.findtext("arxiv:doi", default=None, namespaces=ns))
        authors = [
            (node.text or "").strip()
            for node in entry.findall("atom:author/atom:name", ns)
            if (node.text or "").strip()
        ]

        pdf_url = None
        for link in entry.findall("atom:link", ns):
            if link.attrib.get("type") == "application/pdf":
                pdf_url = _clean(link.attrib.get("href"))
                break
        if not pdf_url and arxiv_id:
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        results.append(
            PaperCandidate(
                title=title or "Untitled",
                authors=authors,
                year=year,
                doi=doi,
                arxiv_id=arxiv_id,
                venue="arXiv",
                abstract=summary or None,
                source="arxiv",
                source_url=identifier or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None),
                pdf_url=pdf_url,
                oa_status="open",
                license="arxiv",
                relevance_score=max(0.0, 0.9 - (idx * 0.02)),
            )
        )
    return results


def dedupe_candidates(candidates: list[PaperCandidate]) -> list[PaperCandidate]:
    unique: list[PaperCandidate] = []
    seen_doi: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: item.relevance_score, reverse=True):
        if candidate.doi:
            doi_key = candidate.doi.lower().strip()
            if doi_key in seen_doi:
                continue
            seen_doi.add(doi_key)

        title_key = normalize_title(candidate.title)
        if any(SequenceMatcher(a=title_key, b=normalize_title(existing.title)).ratio() >= 0.92 for existing in unique):
            continue
        unique.append(candidate)
    return unique


def _query_match_score(query: str, candidate: PaperCandidate) -> float:
    terms = _extract_query_terms(query)
    if not terms:
        return 0.0
    haystack = normalize_title(" ".join([candidate.title or "", candidate.abstract or "", candidate.venue or ""]))
    if not haystack:
        return 0.0
    hits = sum(1 for term in terms if _contains_term(haystack, term))
    token_ratio = hits / max(1, len(terms))
    title_ratio = SequenceMatcher(a=normalize_title(query), b=normalize_title(candidate.title)).ratio()
    return max(0.0, min(1.0, 0.7 * token_ratio + 0.3 * title_ratio))


def _facet_coverage_ratio(query: str, candidate: PaperCandidate) -> tuple[float, int]:
    required = _detect_required_facets(query)
    if not required:
        return 1.0, 0

    haystack = normalize_title(" ".join([candidate.title or "", candidate.abstract or "", candidate.venue or ""]))
    covered = 0
    for facet in required:
        facet_terms = [normalize_title(term) for term in FACET_LIBRARY[facet]["signals"]]
        if any(_contains_term(haystack, term) for term in facet_terms if term):
            covered += 1
    return covered / max(1, len(required)), len(required)


def search_papers(query: str, limit: int = 20) -> list[PaperCandidate]:
    from app.middleware.metrics import metrics_inc_tagged

    variants = _build_query_variants(query)
    providers = [_search_openalex, _search_crossref, _search_arxiv]

    collected: list[PaperCandidate] = []
    provider_limit = max(12, min(40, int(limit * 1.6)))
    for variant in variants:
        for provider in providers:
            try:
                collected.extend(provider(variant, provider_limit))
                metrics_inc_tagged("paperforge_search_api_calls", f"{provider.__name__}.ok")
            except Exception:
                metrics_inc_tagged("paperforge_search_api_calls", f"{provider.__name__}.err")
                continue

    if not collected:
        return []

    deduped = dedupe_candidates(collected)

    scored_rows: list[tuple[PaperCandidate, float, float, int]] = []
    for item in deduped:
        query_score = _query_match_score(query, item)
        coverage_ratio, required_count = _facet_coverage_ratio(query, item)
        blended = 0.42 * query_score + 0.33 * coverage_ratio + 0.25 * float(item.relevance_score or 0.0)
        item.relevance_score = round(max(0.0, min(1.0, blended)), 4)
        scored_rows.append((item, query_score, coverage_ratio, required_count))

    # If the query has multiple intent facets, prefer papers that cover at least two thirds.
    strict_candidates = [
        row[0]
        for row in scored_rows
        if row[3] >= 2 and row[2] >= 0.67
    ]
    if strict_candidates:
        ranked = sorted(strict_candidates, key=lambda item: item.relevance_score, reverse=True)
    else:
        # Fallback to soft threshold, then to pure score sorting.
        soft = [row[0] for row in scored_rows if row[1] >= 0.18 or row[2] >= 0.5]
        ranked = sorted((soft or [row[0] for row in scored_rows]), key=lambda item: item.relevance_score, reverse=True)

    if not ranked:
        ranked = sorted(deduped, key=lambda item: item.relevance_score, reverse=True)
    return ranked[: max(1, min(limit, 100))]
