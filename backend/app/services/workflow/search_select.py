from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Paper
from app.database import backend_dir
from app.services.search_service import normalize_title, search_papers
from app.services.task_registry import add_log, set_progress

logger = logging.getLogger(__name__)


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("https://doi.org/"):
        text = text[len("https://doi.org/"):]
    elif lowered.startswith("http://doi.org/"):
        text = text[len("http://doi.org/"):]
    elif lowered.startswith("doi:"):
        text = text[4:]
    text = text.strip()
    return text or None


def _query_tokens(text: str) -> set[str]:
    normalized = normalize_title(text)
    stop_tokens = {
        "ai",
        "a i",
        "model",
        "models",
        "llm",
        "大模型",
        "模型",
        "人工智能",
    }
    tokens: set[str] = set()
    for token in normalized.split():
        if len(token) >= 2:
            if token in stop_tokens:
                continue
            tokens.add(token)
    cjk = [ch for ch in normalized if 0x4E00 <= ord(ch) <= 0x9FFF]
    for idx in range(len(cjk) - 1):
        token2 = cjk[idx] + cjk[idx + 1]
        if token2 in stop_tokens:
            continue
        tokens.add(token2)
    for idx in range(len(cjk) - 2):
        token3 = cjk[idx] + cjk[idx + 1] + cjk[idx + 2]
        if token3 in stop_tokens:
            continue
        tokens.add(token3)
    return tokens


def _text_query_score(text: str, query: str) -> float:
    tokens = _query_tokens(query)
    if not tokens:
        return 0.0
    haystack = normalize_title(text or "")
    if not haystack:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    return hits / max(1, len(tokens))


def _required_facets(text: str) -> list[str]:
    query = (text or "").lower()
    facets: list[str] = []
    if any(token in query for token in ["ai", "人工智能", "大模型", "llm", "machine learning"]):
        facets.append("ai")
    if any(token in query for token in ["学习路线", "学习路径", "路线图", "roadmap", "learning path", "pathway", "curriculum"]):
        facets.append("path")
    if any(token in query for token in ["小白", "入门", "新手", "beginner", "novice", "introductory"]):
        facets.append("beginner")
    return facets


def _paper_facet_coverage(paper: Paper, query: str) -> float:
    facets = _required_facets(query)
    if not facets:
        return 1.0
    hay = normalize_title(" ".join([paper.title or "", paper.abstract or "", paper.venue or ""]))
    if not hay:
        return 0.0
    covered = 0
    for facet in facets:
        if facet == "ai":
            if any(token in hay for token in ["ai", "artificial intelligence", "llm", "machine learning", "deep learning", "人工智能", "大模型"]):
                covered += 1
        elif facet == "path":
            if any(token in hay for token in ["learning path", "roadmap", "pathway", "curriculum", "课程", "学习路径", "学习路线"]):
                covered += 1
        elif facet == "beginner":
            if any(token in hay for token in ["beginner", "novice", "introductory", "for dummies", "入门", "新手", "小白"]):
                covered += 1
    return covered / max(1, len(facets))


def _paper_query_score(paper: Paper, query: str) -> float:
    tokens = _query_tokens(query)
    if not tokens:
        return 0.0
    haystack = normalize_title(" ".join([paper.title or "", paper.abstract or "", paper.venue or ""]))
    if not haystack:
        return 0.0
    hits = sum(1 for token in tokens if token in haystack)
    lexical = hits / max(1, len(tokens))
    facet = _paper_facet_coverage(paper, query)
    return 0.6 * lexical + 0.4 * facet


def _candidate_identity_keys(candidate: Any) -> set[str]:
    keys: set[str] = set()
    doi = _normalize_doi(getattr(candidate, "doi", None))
    if doi:
        keys.add(f"doi:{doi.lower()}")
    title = normalize_title(str(getattr(candidate, "title", "") or ""))
    if title:
        keys.add(f"title:{title}")
    return keys


def _paper_identity_keys(paper: Paper) -> set[str]:
    keys: set[str] = set()
    doi = _normalize_doi(paper.doi)
    if doi:
        keys.add(f"doi:{doi.lower()}")
    title = normalize_title(paper.title or "")
    if title:
        keys.add(f"title:{title}")
    return keys


def _resolve_local_pdf_path(local_pdf_path: str | None) -> Path | None:
    if not local_pdf_path:
        return None
    raw = local_pdf_path.strip()
    if not raw:
        return None
    candidates: list[Path] = []
    direct = Path(raw)
    candidates.append(direct)
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/app/data/"):
        suffix = normalized[len("/app/data/"):]
        candidates.append(backend_dir / "data" / suffix)
    marker = "/data/"
    if marker in normalized:
        suffix = normalized.split(marker, 1)[1]
        candidates.append(backend_dir / "data" / suffix)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _resolve_pdf_url(paper: Paper) -> str | None:
    if paper.pdf_url:
        return paper.pdf_url
    if paper.arxiv_id:
        return f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
    return None


def _paper_has_download_potential(paper: Paper) -> bool:
    if _resolve_local_pdf_path(paper.local_pdf_path):
        return True
    if _resolve_pdf_url(paper):
        return True
    if paper.doi:
        return True
    if paper.arxiv_id:
        return True
    return False


def _upsert_search_candidates(project_id: str, query: str, candidates: list, db: Session) -> int:
    existing = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
    existing_doi = {item.doi.lower().strip() for item in existing if item.doi}
    existing_titles = {normalize_title(item.title) for item in existing}

    inserted = 0
    now = datetime.now(timezone.utc)
    for candidate in candidates:
        doi = candidate.doi.lower().strip() if candidate.doi else None
        title_key = normalize_title(candidate.title)
        if doi and doi in existing_doi:
            continue
        if title_key in existing_titles:
            continue
        paper = Paper(
            id=str(uuid4()),
            project_id=project_id,
            title=candidate.title,
            authors=candidate.authors,
            year=candidate.year,
            doi=candidate.doi,
            arxiv_id=candidate.arxiv_id,
            venue=candidate.venue,
            abstract=candidate.abstract,
            source=candidate.source,
            source_url=candidate.source_url,
            pdf_url=candidate.pdf_url,
            oa_status=candidate.oa_status,
            license=candidate.license,
            local_pdf_path=None,
            local_tei_path=None,
            relevance_score=candidate.relevance_score,
            selected=False,
            parse_status="pending",
            metadata_json={"query": query},
            created_at=now,
            updated_at=now,
        )
        db.add(paper)
        inserted += 1
        if doi:
            existing_doi.add(doi)
        existing_titles.add(title_key)
    return inserted


def run_search_and_select(
    project_id: str,
    query: str,
    auto_select_limit: int,
    keep_manual_selection: bool,
    max_results: int,
    db: Session,
    task_id: str,
) -> tuple[list[Paper], int, bool]:
    """Run paper search and auto-selection for the workflow.

    Returns (selected_papers, inserted_count, reselection_triggered).
    """
    set_progress(task_id, 5, "searching papers")
    candidates = search_papers(query=query, limit=max_results)
    inserted = _upsert_search_candidates(project_id=project_id, query=query, candidates=candidates, db=db)
    db.flush()
    add_log(task_id, f"search done: candidates={len(candidates)}, inserted={inserted}")

    papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
    selected_papers = [item for item in papers if item.selected]
    preselected_papers = list(selected_papers)

    candidate_keys: set[str] = set()
    for candidate in candidates:
        candidate_keys.update(_candidate_identity_keys(candidate))
    query_scoped_papers = [item for item in papers if _paper_identity_keys(item) & candidate_keys]
    if query_scoped_papers:
        add_log(task_id, f"query-scoped pool prepared: {len(query_scoped_papers)} papers from current search")

    auto_selected_count = 0
    reselection_triggered = False
    if selected_papers and not keep_manual_selection:
        reselection_triggered = True
        now = datetime.now(timezone.utc)
        for item in selected_papers:
            item.selected = False
            item.updated_at = now
        selected_papers = []
        add_log(task_id, "manual selection reset: auto workflow runs with query-driven reselection by default")

    if selected_papers:
        selected_with_potential = [item for item in selected_papers if _paper_has_download_potential(item)]
        selected_scores = [_paper_query_score(item, query) for item in selected_papers]
        selected_facets = [_paper_facet_coverage(item, query) for item in selected_papers]
        max_selected_score = max(selected_scores) if selected_scores else 0.0
        avg_selected_score = (sum(selected_scores) / len(selected_scores)) if selected_scores else 0.0
        max_facet = max(selected_facets) if selected_facets else 0.0
        avg_facet = (sum(selected_facets) / len(selected_facets)) if selected_facets else 0.0
        low_relevance_selection = (
            max_selected_score < 0.16
            and avg_selected_score < 0.10
            and max_facet < 0.67
            and avg_facet < 0.5
        )
        if (
            len(selected_with_potential) == 0
            or len(selected_papers) > auto_select_limit
            or (low_relevance_selection and not keep_manual_selection)
        ):
            reselection_triggered = True
            now = datetime.now(timezone.utc)
            for item in selected_papers:
                item.selected = False
                item.updated_at = now
            selected_papers = []
            add_log(
                task_id,
                "manual selection reset: stale selection had low download potential, weak relevance, or exceeded limit",
            )

    if not selected_papers and not query_scoped_papers and not keep_manual_selection:
        rescue_pool = [
            item
            for item in preselected_papers
            if (item.source or "").strip().lower() != "fallback"
            and (_resolve_local_pdf_path(item.local_pdf_path) is not None or _paper_has_download_potential(item))
        ]
        if rescue_pool:
            rescue_ranked = sorted(
                rescue_pool,
                key=lambda item: (
                    1 if _resolve_local_pdf_path(item.local_pdf_path) else 0,
                    1 if item.parse_status == "parsed" else 0,
                    _paper_query_score(item, query),
                    float(item.relevance_score or 0.0),
                ),
                reverse=True,
            )[:auto_select_limit]
            now = datetime.now(timezone.utc)
            for item in rescue_ranked:
                item.selected = True
                item.updated_at = now
            selected_papers = rescue_ranked
            add_log(
                task_id,
                f"search empty fallback: reused {len(selected_papers)} manually selected trusted papers",
            )

    if not selected_papers:
        selection_pool = query_scoped_papers or papers
        non_fallback_pool = [item for item in selection_pool if (item.source or "").strip().lower() != "fallback"]
        if non_fallback_pool:
            selection_pool = non_fallback_pool
        if query_scoped_papers:
            add_log(task_id, "auto-select scope: current-search papers only")
        else:
            add_log(task_id, "auto-select scope: all project papers (no current-search overlap)")

        required_facets = _required_facets(query)
        scored_pool: list[tuple[float, float, Paper]] = [
            (_paper_query_score(item, query), _paper_facet_coverage(item, query), item) for item in selection_pool
        ]
        max_query_score = max((score for score, _, _ in scored_pool), default=0.0)

        if len(required_facets) >= 2:
            strict = [item for score, facet, item in scored_pool if score >= 0.18 and facet >= 0.67]
            if strict:
                scoped_pool = strict
            else:
                scoped_pool = [item for score, facet, item in scored_pool if score >= 0.12 and facet >= 0.5]
        elif max_query_score >= 0.10:
            floor = max(0.06, max_query_score * 0.5)
            scoped_pool = [item for score, _, item in scored_pool if score >= floor]
        else:
            scoped_pool = [item for _, _, item in scored_pool]

        ranked = sorted(
            scoped_pool,
            key=lambda item: (
                _paper_query_score(item, query),
                _paper_facet_coverage(item, query),
                float(item.relevance_score or 0.0),
                1 if (item.source or "").strip().lower() != "fallback" else 0,
                1 if _paper_has_download_potential(item) else 0,
                1 if _resolve_local_pdf_path(item.local_pdf_path) else 0,
                1 if _resolve_pdf_url(item) else 0,
                1 if item.doi else 0,
            ),
            reverse=True,
        )
        selected_papers = ranked[:auto_select_limit]
        now = datetime.now(timezone.utc)
        for item in selected_papers:
            item.selected = True
            item.updated_at = now
            if (item.source or "").strip().lower() == "fallback":
                item.local_pdf_path = None
                item.pdf_url = None
                item.parse_status = "pending"
        auto_selected_count = len(selected_papers)
        add_log(
            task_id,
            f"auto-selected papers: {auto_selected_count}"
            + (" (reselection mode)" if reselection_triggered else ""),
        )
    else:
        add_log(task_id, f"using manually selected papers: {len(selected_papers)}")

    return selected_papers, inserted, reselection_triggered
