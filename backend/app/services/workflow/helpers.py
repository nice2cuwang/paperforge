from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Draft, EvidenceCard, Paper, Project

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_paper_or_404(paper_id: str, db: Session) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return paper


def _get_draft_or_404(draft_id: str, project_id: str, db: Session) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found in project")
    return draft


def _next_draft_version(project_id: str, db: Session) -> int:
    current = db.scalar(select(func.max(Draft.version)).where(Draft.project_id == project_id))
    return int(current or 0) + 1


def _paper_to_dict(paper: Paper) -> dict:
    return {
        "id": paper.id,
        "project_id": paper.project_id,
        "title": paper.title,
        "authors": paper.authors,
        "year": paper.year,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
        "venue": paper.venue,
        "abstract": paper.abstract,
        "source": paper.source,
        "source_url": paper.source_url,
        "pdf_url": paper.pdf_url,
        "oa_status": paper.oa_status,
        "license": paper.license,
        "local_pdf_path": paper.local_pdf_path,
        "local_tei_path": paper.local_tei_path,
        "relevance_score": paper.relevance_score,
        "selected": paper.selected,
        "parse_status": paper.parse_status,
        "metadata_json": paper.metadata_json,
        "created_at": paper.created_at.isoformat(),
        "updated_at": paper.updated_at.isoformat(),
    }


def _evidence_to_dict(card: EvidenceCard) -> dict:
    return {
        "id": card.id,
        "paper_id": card.paper_id,
        "chunk_ids": card.chunk_ids,
        "claim": card.claim,
        "supporting_text": card.supporting_text,
        "evidence_type": card.evidence_type,
        "strength": card.strength,
        "limitations": card.limitations,
        "page_start": card.page_start,
        "page_end": card.page_end,
    }


def _normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("https://doi.org/"):
        text = text[len("https://doi.org/") :]
    elif lowered.startswith("http://doi.org/"):
        text = text[len("http://doi.org/") :]
    elif lowered.startswith("doi:"):
        text = text[4:]
    text = text.strip()
    return text or None


def _extract_doi_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(10\.\d{4,9}/[^\s\"'<>]+)", value, flags=re.IGNORECASE)
    if not match:
        return None
    return _normalize_doi(match.group(1).rstrip(").,;"))


def _extract_arxiv_id(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(
        r"arxiv\.org/(?:abs|pdf)/([a-zA-Z\-\.]+/\d{7}|\d{4}\.\d{4,5}(?:v\d+)?)",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    arxiv_id = match.group(1)
    if arxiv_id.lower().endswith(".pdf"):
        arxiv_id = arxiv_id[:-4]
    return arxiv_id.strip() or None


def _extract_pdf_from_openalex_work(work: dict[str, Any]) -> str | None:
    locations: list[dict[str, Any]] = []
    for key in ("best_oa_location", "primary_location"):
        loc = work.get(key)
        if isinstance(loc, dict):
            locations.append(loc)
    raw_locations = work.get("locations")
    if isinstance(raw_locations, list):
        locations.extend([item for item in raw_locations if isinstance(item, dict)])

    for loc in locations:
        pdf_url = loc.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url.strip():
            return pdf_url.strip()
        landing = loc.get("landing_page_url")
        if isinstance(landing, str) and landing.strip().lower().endswith(".pdf"):
            return landing.strip()
    return None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
