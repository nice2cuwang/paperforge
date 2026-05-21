from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
from threading import Thread
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, backend_dir, get_db
from app.models import Draft, EvidenceCard, Paper, PaperChunk, Project, ReviewIssue
from app.schemas import (
    BuildEvidenceRequest,
    GenerateDraftRequest,
    GenerateOutlineRequest,
    ParsePaperRequest,
    RetrieveChunksRequest,
    RunAutoWorkflowRequest,
    ReviewDraftRequest,
    ReviseDraftRequest,
    SearchPapersRequest,
    SelectPaperRequest,
)
from app.services.export_service import (
    ensure_export_dir,
    export_bibtex,
    export_docx,
    export_json,
    export_markdown,
    export_pdf,
    export_quality_report,
)
from app.services.evidence_service import build_evidence_from_chunks
from app.services.http_client import create_httpx_client
from app.services.ingestion_service import chunk_text, extract_pdf_text, save_tei_placeholder, save_uploaded_pdf
from app.services.retrieval_service import rank_chunks
from app.services.review_service import review_draft_with_metrics, revise_draft, score_quality
from app.services.search_service import normalize_title, search_papers
from app.services.task_registry import add_log, complete_task, create_task, fail_task, set_progress
from app.services.writing_service import build_draft_markdown, build_outline
from app.services.workflow.search_select import (
    run_search_and_select,
    _upsert_search_candidates,
    _query_tokens,
    _text_query_score,
    _paper_query_score,
    _paper_facet_coverage,
)
from app.services import embedding_service, qdrant_service

router = APIRouter(prefix="/api", tags=["workflow"])
HTTP_HEADERS = {"User-Agent": "PaperForge/0.3 (+https://paperforge.local)"}
UNPAYWALL_EMAIL = "paperforge@local.dev"

# Download security boundaries
_DOWNLOAD_ALLOWLIST = {
    "arxiv.org",
    "export.arxiv.org",
    "api.openalex.org",
    "api.unpaywall.org",
    "api.crossref.org",
    "api.semanticscholar.org",
    "doi.org",
    "www.doi.org",
    "link.springer.com",
    "journals.plos.org",
    "www.biorxiv.org",
    "www.medrxiv.org",
    "hal.science",
    "hal.archives-ouvertes.fr",
}

_ALLOW_TLS_DOWNGRADE = (os.getenv("PAPERFORGE_ALLOW_TLS_DOWNGRADE") or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_private_ip(host: str) -> bool:
    """Detect private/internal IP addresses to block SSRF."""
    if not host:
        return True
    host = host.lower()
    # Block plain IP literals in production downloads (safer default)
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
        parts = [int(p) for p in host.split(".")]
        if parts[0] == 10:
            return True
        if parts[0] == 172 and 16 <= parts[1] <= 31:
            return True
        if parts[0] == 192 and parts[1] == 168:
            return True
        if parts[0] == 127:
            return True
        if parts[0] == 0:
            return True
        if parts[0] == 169 and parts[1] == 254:
            return True
    if host in {"localhost", "0.0.0.0", "::", "::1"}:
        return True
    return False


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Validate that a download URL is safe. Returns (ok, reason)."""
    url = (url or "").strip()
    if not url:
        return False, "URL is empty"
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False, f"Unsupported scheme: {scheme}"
    hostname = (parsed.hostname or "").lower()
    if _is_private_ip(hostname):
        return False, f"Private/internal address blocked: {hostname}"
    return True, ""


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


import logging

logger = logging.getLogger(__name__)

def _safe_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    try:
        with create_httpx_client(timeout=8.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("Failed to fetch JSON from %s: %s", url, exc)
        return None


def _probe_pdf_url(pdf_url: str) -> bool:
    url = pdf_url.strip()
    if not url:
        return False
    safe, reason = _is_safe_url(url)
    if not safe:
        logger.warning("SSRF block in probe: %s", reason)
        return False

    try:
        with create_httpx_client(timeout=8.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
            head = client.head(url)
            if head.status_code in {200, 206}:
                content_type = (head.headers.get("content-type") or "").lower()
                disposition = (head.headers.get("content-disposition") or "").lower()
                if "pdf" in content_type or ".pdf" in disposition:
                    return True
            if head.status_code not in {405, 403, 401}:
                return False
    except Exception:
        pass

    try:
        with create_httpx_client(timeout=12.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
            probe = client.get(url, headers={"Range": "bytes=0-1023"})
            if probe.status_code not in {200, 206}:
                return False
            content_type = (probe.headers.get("content-type") or "").lower()
            if "pdf" in content_type:
                return True
            return probe.content.startswith(b"%PDF")
    except Exception:
        return False


def _resolve_pdf_via_crossref(doi: str) -> str | None:
    payload = _safe_get_json(
        f"https://api.crossref.org/works/{quote(doi, safe='')}",
        params={"mailto": UNPAYWALL_EMAIL},
    )
    if not payload:
        return None
    message = payload.get("message")
    if not isinstance(message, dict):
        return None

    links = message.get("link")
    if not isinstance(links, list):
        return None

    for link in links:
        if not isinstance(link, dict):
            continue
        candidate = link.get("URL")
        if not isinstance(candidate, str):
            continue
        content_type = (link.get("content-type") or link.get("content_type") or "").lower()
        if "pdf" in content_type or candidate.lower().endswith(".pdf"):
            return candidate
    return None


def _resolve_pdf_via_unpaywall(doi: str) -> tuple[str | None, str | None, str | None]:
    payload = _safe_get_json(
        f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
        params={"email": UNPAYWALL_EMAIL},
    )
    if not payload:
        return None, None, None

    best = payload.get("best_oa_location")
    if isinstance(best, dict):
        pdf_url = best.get("url_for_pdf")
        if isinstance(pdf_url, str) and pdf_url.strip():
            return pdf_url.strip(), payload.get("oa_status"), best.get("license")

    locations = payload.get("oa_locations")
    if isinstance(locations, list):
        for item in locations:
            if not isinstance(item, dict):
                continue
            pdf_url = item.get("url_for_pdf")
            if isinstance(pdf_url, str) and pdf_url.strip():
                return pdf_url.strip(), payload.get("oa_status"), item.get("license")

    return None, payload.get("oa_status"), None


def _resolve_pdf_via_openalex(doi: str) -> tuple[str | None, str | None, str | None]:
    payload = _safe_get_json(f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}")
    if not payload:
        return None, None, None

    pdf_url = _extract_pdf_from_openalex_work(payload)
    open_access = payload.get("open_access") if isinstance(payload.get("open_access"), dict) else {}
    oa_status = open_access.get("oa_status") if isinstance(open_access, dict) else None
    license_name = None
    best_oa = payload.get("best_oa_location")
    if isinstance(best_oa, dict):
        license_name = best_oa.get("license")
    return pdf_url, oa_status, license_name


def _resolve_pdf_url_with_fallback(
    paper: Paper,
    task_id: str | None = None,
) -> tuple[str | None, list[str]]:
    trace: list[str] = []
    direct = _resolve_pdf_url(paper)
    if direct:
        trace.append("direct pdf_url/arxiv available")
        return direct, trace
    trace.append("direct pdf_url missing")

    doi = _normalize_doi(paper.doi) or _extract_doi_from_text(paper.source_url)
    if doi and not paper.doi:
        paper.doi = doi
        paper.updated_at = _now()
    if doi:
        trace.append(f"try doi: {doi}")

        openalex_pdf, oa_status, openalex_license = _resolve_pdf_via_openalex(doi)
        if openalex_pdf:
            trace.append("resolved via openalex")
            paper.pdf_url = openalex_pdf
            paper.oa_status = oa_status or paper.oa_status
            paper.license = openalex_license or paper.license
            paper.updated_at = _now()
            return openalex_pdf, trace
        trace.append("openalex: no pdf")

        unpaywall_pdf, unpaywall_status, unpaywall_license = _resolve_pdf_via_unpaywall(doi)
        if unpaywall_pdf:
            trace.append("resolved via unpaywall")
            paper.pdf_url = unpaywall_pdf
            paper.oa_status = unpaywall_status or paper.oa_status
            paper.license = unpaywall_license or paper.license
            paper.updated_at = _now()
            return unpaywall_pdf, trace
        trace.append("unpaywall: no pdf")

        crossref_pdf = _resolve_pdf_via_crossref(doi)
        if crossref_pdf:
            trace.append("resolved via crossref link")
            paper.pdf_url = crossref_pdf
            paper.updated_at = _now()
            return crossref_pdf, trace
        trace.append("crossref: no pdf")

    arxiv_id = paper.arxiv_id or _extract_arxiv_id(paper.source_url)
    if arxiv_id:
        trace.append(f"try arxiv id: {arxiv_id}")
        arxiv_pdf = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        paper.arxiv_id = arxiv_id
        paper.pdf_url = arxiv_pdf
        paper.oa_status = paper.oa_status or "open"
        paper.license = paper.license or "arxiv"
        paper.updated_at = _now()
        return arxiv_pdf, trace

    if task_id:
        add_log(task_id, f"fulltext lookup failed: {paper.title}")
    return None, trace


def _workflow_error_detail(exc: Exception) -> tuple[str, dict[str, Any]]:
    if isinstance(exc, HTTPException):
        if isinstance(exc.detail, dict):
            detail = dict(exc.detail)
            reason = str(detail.get("message") or detail.get("title") or detail.get("code") or "request failed")
            detail.setdefault("http_status", exc.status_code)
            return reason, detail
        message = str(exc.detail)
        return message, {"code": "HTTP_ERROR", "message": message, "http_status": exc.status_code}
    message = str(exc)
    return message, {"code": "UNEXPECTED_ERROR", "message": message}


def _fail_task_for_exception(task_id: str, exc: Exception) -> None:
    reason, detail = _workflow_error_detail(exc)
    fail_task(task_id, reason, detail)


def _resolve_pdf_url(paper: Paper) -> str | None:
    if paper.pdf_url:
        return paper.pdf_url
    if paper.arxiv_id:
        return f"https://arxiv.org/pdf/{paper.arxiv_id}.pdf"
    if paper.source_url and "arxiv.org/abs/" in paper.source_url:
        return paper.source_url.replace("/abs/", "/pdf/") + ".pdf"
    return None


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
        suffix = normalized[len("/app/data/") :]
        candidates.append(backend_dir / "data" / suffix)

    marker = "/data/"
    if marker in normalized:
        suffix = normalized.split(marker, 1)[1]
        candidates.append(backend_dir / "data" / suffix)

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _paper_has_download_potential(paper: Paper) -> bool:
    if _resolve_local_pdf_path(paper.local_pdf_path):
        return True
    if _resolve_pdf_url(paper):
        return True
    if paper.doi or _extract_doi_from_text(paper.source_url):
        return True
    if paper.arxiv_id or _extract_arxiv_id(paper.source_url):
        return True
    return False












def _provider_diagnostics() -> dict[str, str]:
    checks = {
        "openalex": "https://api.openalex.org/works?per-page=1",
        "crossref": "https://api.crossref.org/works?rows=1",
        "arxiv": "https://export.arxiv.org/api/query?search_query=all:ai&start=0&max_results=1",
    }
    diagnostics: dict[str, str] = {}
    for name, url in checks.items():
        try:
            with create_httpx_client(timeout=8.0, headers=HTTP_HEADERS, follow_redirects=True) as client:
                response = client.get(url)
                diagnostics[name] = f"ok:{response.status_code}"
        except Exception as exc:  # noqa: BLE001
            diagnostics[name] = f"error:{str(exc)[:220]}"
    return diagnostics






def _is_fallback_source(paper: Paper) -> bool:
    return (paper.source or "").strip().lower() == "fallback"


def _iter_pdf_url_candidates(paper: Paper, primary_url: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _push(url: str | None) -> None:
        if not url:
            return
        cleaned = url.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        candidates.append(cleaned)

    _push(primary_url)
    parsed = urlparse(primary_url)
    if parsed.scheme == "https" and parsed.hostname in {"arxiv.org", "export.arxiv.org"}:
        _push(primary_url.replace("https://", "http://", 1))

    arxiv_id = paper.arxiv_id or _extract_arxiv_id(primary_url) or _extract_arxiv_id(paper.source_url)
    if arxiv_id:
        _push(f"https://arxiv.org/pdf/{arxiv_id}.pdf")
        _push(f"https://export.arxiv.org/pdf/{arxiv_id}.pdf")
        _push(f"http://arxiv.org/pdf/{arxiv_id}.pdf")
        _push(f"http://export.arxiv.org/pdf/{arxiv_id}.pdf")

    return candidates


def _download_pdf_bytes(url: str, verify_tls: bool = True) -> tuple[bytes, str]:
    safe, reason = _is_safe_url(url)
    if not safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "SSRF_BLOCKED", "message": reason, "url": url},
        )
    with create_httpx_client(
        timeout=25.0,
        headers=HTTP_HEADERS,
        follow_redirects=True,
        verify=verify_tls,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content, (response.headers.get("content-type") or "").lower()


def _download_pdf_for_paper(
    paper: Paper,
    task_id: str | None = None,
    resolved_pdf_url: str | None = None,
    resolution_trace: list[str] | None = None,
) -> str:
    pdf_url = resolved_pdf_url
    resolve_trace = list(resolution_trace or [])
    if not pdf_url:
        pdf_url, resolve_trace = _resolve_pdf_url_with_fallback(paper, task_id=task_id)
    if not pdf_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "PAPER_PDF_NOT_FOUND",
                "title": "Paper has no downloadable PDF",
                "message": f"Could not resolve a PDF URL for selected paper: {paper.title}",
                "resolution_trace": resolve_trace,
                "paper_id": paper.id,
                "paper_title": paper.title,
            },
        )

    if not _probe_pdf_url(pdf_url):
        if task_id:
            add_log(task_id, f"pdf probe uncertain, still trying full download: {paper.title}")

    attempt_logs: list[str] = []
    content: bytes | None = None
    content_type = ""
    downloaded_from = pdf_url

    for candidate_url in _iter_pdf_url_candidates(paper, pdf_url):
        try:
            content, content_type = _download_pdf_bytes(candidate_url, verify_tls=True)
            downloaded_from = candidate_url
            attempt_logs.append(f"{candidate_url}: ok")
            break
        except Exception as exc:  # noqa: BLE001
            attempt_logs.append(f"{candidate_url}: {exc}")
            parsed = urlparse(candidate_url)
            is_https = parsed.scheme == "https"
            if is_https and "ssl" in str(exc).lower() and _ALLOW_TLS_DOWNGRADE:
                try:
                    content, content_type = _download_pdf_bytes(candidate_url, verify_tls=False)
                    downloaded_from = candidate_url
                    attempt_logs.append(f"{candidate_url}: ok (verify_tls=false)")
                    break
                except Exception as retry_exc:  # noqa: BLE001
                    attempt_logs.append(f"{candidate_url}: retry verify_tls=false failed: {retry_exc}")
            continue

    if content is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DOWNLOAD_FAILED",
                "title": "Failed to download PDF",
                "message": f"Could not download a usable PDF for paper: {paper.title}",
                "paper_id": paper.id,
                "paper_title": paper.title,
                "resolution_trace": resolve_trace,
                "attempts": attempt_logs[:10],
            },
        )

    if not content:
        raise HTTPException(status_code=400, detail="Downloaded empty file")
    if not ("pdf" in content_type or content.startswith(b"%PDF")):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DOWNLOAD_NOT_PDF",
                "title": "Remote file is not a PDF",
                "message": f"Resolved link did not return PDF for paper: {paper.title}",
                "pdf_url": downloaded_from,
                "content_type": content_type or "unknown",
                "resolution_trace": resolve_trace,
                "paper_id": paper.id,
            },
        )

    saved = save_uploaded_pdf(
        base_dir=backend_dir / "data",
        project_id=paper.project_id,
        paper_id=paper.id,
        filename=Path(downloaded_from).name or "downloaded.pdf",
        content=content,
    )
    if paper.pdf_url != downloaded_from:
        paper.pdf_url = downloaded_from
    paper.local_pdf_path = str(saved)
    paper.parse_status = "pending"
    paper.updated_at = _now()
    return str(saved)


def _parse_paper_to_chunks(paper: Paper, db: Session, chunk_size: int) -> int:
    if not paper.local_pdf_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Paper has no local PDF to parse")

    pdf_path = _resolve_local_pdf_path(paper.local_pdf_path)
    if not pdf_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "LOCAL_PDF_NOT_FOUND",
                "title": "Local PDF file not found",
                "message": "Saved local PDF path is invalid on current runtime. Re-download or upload again.",
                "paper_id": paper.id,
                "paper_title": paper.title,
                "stored_local_pdf_path": paper.local_pdf_path,
            },
        )
    if paper.local_pdf_path != str(pdf_path):
        paper.local_pdf_path = str(pdf_path)

    raw_text = extract_pdf_text(pdf_path)
    tei_path = save_tei_placeholder(
        base_dir=backend_dir / "data",
        project_id=paper.project_id,
        paper_id=paper.id,
        text=raw_text,
    )
    chunks = chunk_text(raw_text, chunk_size=chunk_size)
    if not chunks:
        existing_chunk_count = int(
            db.scalar(select(func.count(PaperChunk.id)).where(PaperChunk.paper_id == paper.id)) or 0
        )
        paper.local_tei_path = str(tei_path)
        paper.parse_status = "failed"
        paper.updated_at = _now()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PDF_TEXT_EXTRACTION_EMPTY",
                "title": "No usable text was extracted from PDF",
                "message": (
                    "PDF parsed but no usable text was extracted. "
                    "Try another PDF source, upload a cleaner file, or run OCR before parsing."
                ),
                "paper_id": paper.id,
                "paper_title": paper.title,
                "local_pdf_path": str(pdf_path),
                "existing_chunk_count": existing_chunk_count,
            },
        )

    db.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper.id))
    for chunk in chunks:
        db.add(
            PaperChunk(
                id=chunk["id"],
                paper_id=paper.id,
                section=chunk["section"],
                subsection=chunk["subsection"],
                page_start=chunk["page_start"],
                page_end=chunk["page_end"],
                text=chunk["text"],
                token_count=chunk["token_count"],
                vector_id=chunk["vector_id"],
                metadata_json=chunk["metadata_json"],
                created_at=_now(),
            )
        )

    # Vectorize and upsert to Qdrant
    try:
        chunk_texts = [c["text"] for c in chunks if c.get("text")]
        if chunk_texts:
            embeddings = embedding_service.encode_texts(chunk_texts)
            payloads = [
                {
                    "project_id": paper.project_id,
                    "paper_id": paper.id,
                    "paper_title": paper.title,
                    "page_start": c.get("page_start"),
                    "page_end": c.get("page_end"),
                    "text_preview": (c.get("text") or "")[:500],
                }
                for c in chunks
            ]
            chunk_ids = [c["id"] for c in chunks]
            qdrant_service.upsert_chunks(chunk_ids=chunk_ids, embeddings=embeddings, payloads=payloads)
            logger.info("Vectorized %d chunks for paper %s", len(chunks), paper.id)
    except Exception as exc:
        logger.warning("Qdrant upsert failed for paper %s: %s (proceeding without vectors)", paper.id, exc)

    paper.local_tei_path = str(tei_path)
    paper.parse_status = "parsed"
    paper.updated_at = _now()
    return len(chunks)




def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@router.post("/projects/{project_id}/search-papers")
def search_project_papers(
    project_id: str, payload: SearchPapersRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    task = create_task("search-papers")
    try:
        set_progress(task.task_id, 10, "querying providers")
        candidates = search_papers(query=query, limit=payload.max_results)
        add_log(task.task_id, f"collected candidates: {len(candidates)}")
        inserted = _upsert_search_candidates(project_id=project_id, query=query, candidates=candidates, db=db)

        db.commit()
        set_progress(task.task_id, 80, "loading results")
        papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
        response = {
            "task_id": task.task_id,
            "query": query,
            "inserted": inserted,
            "total": len(papers),
            "papers": [_paper_to_dict(item) for item in papers],
        }
        complete_task(task.task_id, {"inserted": inserted, "total": len(papers)})
        return response
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


import os

_MAX_UPLOAD_SIZE_MB = int(os.getenv("PAPERFORGE_MAX_UPLOAD_SIZE_MB", "50"))
_UPLOAD_ALLOWED_EXTENSIONS = {".pdf"}
_UPLOAD_ALLOWED_MIME_TYPES = {"application/pdf", "application/octet-stream"}


def _validate_upload_file(file: UploadFile, content: bytes) -> None:
    max_bytes = _MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "UPLOAD_TOO_LARGE",
                "message": f"File size exceeds limit of {_MAX_UPLOAD_SIZE_MB}MB",
                "limit_mb": _MAX_UPLOAD_SIZE_MB,
                "received_bytes": len(content),
            },
        )

    filename = (file.filename or "").lower()
    if not any(filename.endswith(ext) for ext in _UPLOAD_ALLOWED_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UPLOAD_INVALID_EXTENSION",
                "message": f"Only {', '.join(_UPLOAD_ALLOWED_EXTENSIONS)} files are allowed",
            },
        )

    content_type = (file.content_type or "").lower()
    if content_type and content_type not in _UPLOAD_ALLOWED_MIME_TYPES:
        # Allow empty/unknown content_type, but reject explicitly wrong ones
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UPLOAD_INVALID_MIME",
                "message": f"MIME type '{content_type}' not allowed",
                "allowed": list(_UPLOAD_ALLOWED_MIME_TYPES),
            },
        )


@router.post("/projects/{project_id}/papers/upload")
async def upload_paper_pdf(
    project_id: str,
    file: UploadFile = File(...),
    paper_id: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict:
    project = _get_project_or_404(project_id, db)
    del project

    target_paper: Paper
    if paper_id:
        target_paper = _get_paper_or_404(paper_id, db)
        if target_paper.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found in project")
    else:
        now = _now()
        target_paper = Paper(
            id=str(uuid4()),
            project_id=project_id,
            title=(file.filename or "uploaded.pdf").rsplit(".", 1)[0],
            authors=[],
            year=None,
            doi=None,
            arxiv_id=None,
            venue=None,
            abstract=None,
            source="upload",
            source_url=None,
            pdf_url=None,
            oa_status="uploaded",
            license=None,
            local_pdf_path=None,
            local_tei_path=None,
            relevance_score=0.0,
            selected=True,
            parse_status="pending",
            metadata_json={},
            created_at=now,
            updated_at=now,
        )
        db.add(target_paper)
        db.flush()

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    _validate_upload_file(file, content)

    saved = save_uploaded_pdf(
        base_dir=backend_dir / "data",
        project_id=project_id,
        paper_id=target_paper.id,
        filename=file.filename or "upload.pdf",
        content=content,
    )
    target_paper.local_pdf_path = str(saved)
    target_paper.parse_status = "pending"
    target_paper.updated_at = _now()
    db.commit()
    db.refresh(target_paper)
    return {"paper": _paper_to_dict(target_paper)}


@router.post("/papers/{paper_id}/select")
def select_paper(
    paper_id: str, payload: SelectPaperRequest, db: Session = Depends(get_db)
) -> dict:
    paper = _get_paper_or_404(paper_id, db)
    paper.selected = payload.selected
    paper.updated_at = _now()
    db.commit()
    db.refresh(paper)
    return {"paper": _paper_to_dict(paper)}


@router.post("/papers/{paper_id}/download")
def download_paper(paper_id: str, db: Session = Depends(get_db)) -> dict:
    paper = _get_paper_or_404(paper_id, db)

    task = create_task("download-pdf")
    try:
        set_progress(task.task_id, 15, "downloading PDF")
        saved = _download_pdf_for_paper(paper, task_id=task.task_id)
        db.commit()
        complete_task(task.task_id, {"paper_id": paper.id, "local_pdf_path": saved})
        return {"task_id": task.task_id, "paper": _paper_to_dict(paper)}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/papers/{paper_id}/parse")
def parse_paper(
    paper_id: str, payload: ParsePaperRequest, db: Session = Depends(get_db)
) -> dict:
    paper = _get_paper_or_404(paper_id, db)

    task = create_task("parse-pdf")
    try:
        set_progress(task.task_id, 20, "extracting text")
        set_progress(task.task_id, 60, "chunking text")
        chunk_count = _parse_paper_to_chunks(paper, db, chunk_size=payload.chunk_size)
        db.commit()
        complete_task(task.task_id, {"paper_id": paper.id, "chunk_count": chunk_count})
        return {"task_id": task.task_id, "paper_id": paper.id, "chunk_count": chunk_count}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/download-selected-papers")
def download_selected_papers(
    project_id: str,
    auto_parse: bool = True,
    chunk_size: int = 900,
    db: Session = Depends(get_db),
) -> dict:
    _get_project_or_404(project_id, db)
    task = create_task("download-selected-papers")

    try:
        papers = list(
            db.scalars(
                select(Paper).where(Paper.project_id == project_id, Paper.selected == True)  # noqa: E712
            ).all()
        )
        if not papers:
            result = {
                "task_id": task.task_id,
                "selected_count": 0,
                "downloaded_count": 0,
                "parsed_count": 0,
                "skipped_no_pdf_count": 0,
                "failed_count": 0,
            }
            complete_task(task.task_id, result)
            return result

        downloaded_count = 0
        parsed_count = 0
        skipped_no_pdf_count = 0
        failed_count = 0

        for index, paper in enumerate(papers, start=1):
            set_progress(task.task_id, int(index * 100 / len(papers)), f"processing paper {index}/{len(papers)}")
            pdf_url, resolve_trace = _resolve_pdf_url_with_fallback(paper, task_id=task.task_id)
            if not pdf_url:
                skipped_no_pdf_count += 1
                add_log(task.task_id, f"skip(no pdf found after fallback): {paper.title}")
                continue
            try:
                _download_pdf_for_paper(
                    paper,
                    task_id=task.task_id,
                    resolved_pdf_url=pdf_url,
                    resolution_trace=resolve_trace,
                )
                downloaded_count += 1
                add_log(task.task_id, f"downloaded: {paper.title}")
                if auto_parse:
                    _parse_paper_to_chunks(paper, db, chunk_size=chunk_size)
                    parsed_count += 1
                    add_log(task.task_id, f"parsed: {paper.title}")
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                add_log(task.task_id, f"failed: {paper.title} -> {exc}")

        db.commit()
        result = {
            "selected_count": len(papers),
            "downloaded_count": downloaded_count,
            "parsed_count": parsed_count,
            "skipped_no_pdf_count": skipped_no_pdf_count,
            "failed_count": failed_count,
        }
        complete_task(task.task_id, result)
        return {"task_id": task.task_id, **result}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


def _execute_auto_workflow(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session, task_id: str
) -> dict:
    add_log(task_id, "enter _execute_auto_workflow")
    project = _get_project_or_404(project_id, db)
    add_log(task_id, f"project loaded: {project.title}")
    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    add_log(task_id, f"starting search_and_select: query={query[:80]}")
    selected_papers, inserted, reselection_triggered = run_search_and_select(
        project_id=project_id,
        query=query,
        auto_select_limit=payload.auto_select_limit,
        keep_manual_selection=payload.keep_manual_selection,
        max_results=payload.max_results,
        db=db,
        task_id=task_id,
    )
    add_log(task_id, f"search_and_select done: selected={len(selected_papers)}, inserted={inserted}")
    candidates: list[Any] = []  # populated below if needed for diagnostics
    if not selected_papers:
        provider_diag = _provider_diagnostics()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "SEARCH_NO_CANDIDATES",
                "title": "No credible papers found from current search",
                "message": (
                    "Current query returned no usable real candidates from external providers. "
                    "Auto workflow stopped to avoid writing from stale or synthetic papers."
                ),
                "summary": {
                    "query": query,
                    "provider_candidate_count": 0,
                    "inserted_count": inserted,
                },
                "provider_diagnostics": provider_diag,
                "next_actions": [
                    "Check backend network connectivity to OpenAlex/Crossref/arXiv.",
                    "If you use a local proxy, set PAPERFORGE_PROXY_URL=http://host.docker.internal:<port> in .env.",
                    "Or run backend outside Docker and retry search.",
                    "If you already selected trusted local papers, run with keep_manual_selection=true.",
                ],
            },
        )

    set_progress(task_id, 30, "downloading and parsing selected papers")
    downloaded_count = 0
    parsed_count = 0
    reused_local_pdf_count = 0
    resolved_via_fallback_count = 0
    skipped_no_pdf_count = 0
    failed_count = 0
    paper_diagnostics: list[dict[str, Any]] = []

    for index, paper in enumerate(selected_papers, start=1):
        set_progress(
            task_id,
            min(62, 30 + int(index * 32 / len(selected_papers))),
            f"processing selected paper {index}/{len(selected_papers)}",
        )
        paper_diag: dict[str, Any] = {"paper_id": paper.id, "title": paper.title, "status": "pending"}
        try:
            resolved_local_pdf = _resolve_local_pdf_path(paper.local_pdf_path)
            if (paper.source or "").lower() == "fallback":
                resolved_local_pdf = None
            if resolved_local_pdf:
                if paper.local_pdf_path != str(resolved_local_pdf):
                    paper.local_pdf_path = str(resolved_local_pdf)
                    paper.updated_at = _now()
                reused_local_pdf_count += 1
                paper_diag["status"] = "reused_local_pdf"
                add_log(task_id, f"reuse local pdf: {paper.title}")
            else:
                direct_pdf_url = _resolve_pdf_url(paper)
                if direct_pdf_url:
                    resolved_pdf_url = direct_pdf_url
                    resolve_trace = ["direct pdf_url/arxiv available"]
                else:
                    resolved_pdf_url, resolve_trace = _resolve_pdf_url_with_fallback(paper, task_id=task_id)
                if not resolved_pdf_url:
                    skipped_no_pdf_count += 1
                    paper_diag["status"] = "skipped_no_pdf"
                    paper_diag["resolution_trace"] = resolve_trace
                    add_log(task_id, f"skip(no downloadable or uploaded pdf): {paper.title}")
                    continue

                used_fallback = not bool(direct_pdf_url)
                if used_fallback:
                    resolved_via_fallback_count += 1
                    add_log(task_id, f"resolved via fallback: {paper.title}")
                _download_pdf_for_paper(
                    paper,
                    task_id=task_id,
                    resolved_pdf_url=resolved_pdf_url,
                    resolution_trace=resolve_trace,
                )
                downloaded_count += 1
                paper_diag["status"] = "downloaded"
                paper_diag["resolution_trace"] = resolve_trace
                add_log(task_id, f"downloaded: {paper.title}")

            chunk_count = _parse_paper_to_chunks(paper, db, chunk_size=payload.chunk_size)
            parsed_count += 1
            paper_diag["chunk_count"] = chunk_count
            if paper_diag["status"] == "pending":
                paper_diag["status"] = "parsed"
            add_log(task_id, f"parsed: {paper.title} ({chunk_count} chunks)")
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            paper_diag["status"] = "failed"
            paper_diag["error"] = str(exc)
            add_log(task_id, f"failed processing {paper.title}: {exc}")
        finally:
            paper_diagnostics.append(paper_diag)

    db.flush()
    add_log(task_id, "db flushed after paper processing")
    set_progress(task_id, 66, "building evidence cards")
    db.execute(delete(EvidenceCard).where(EvidenceCard.project_id == project_id))
    evidence_count = 0
    metadata_fallback_evidence_count = 0
    low_relevance_filtered_count = 0
    query_tokens = _query_tokens(query)
    for paper in selected_papers:
        chunks = list(
            db.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.created_at)).all()
        )
        if not chunks:
            continue
        chunk_payloads = [
            {
                "id": chunk.id,
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
            }
            for chunk in chunks
        ]
        scored_chunks = [(_text_query_score(item["text"], query), item) for item in chunk_payloads]
        max_chunk_score = max((score for score, _ in scored_chunks), default=0.0)
        if query_tokens:
            if max_chunk_score < 0.08:
                chunk_payloads = []
                low_relevance_filtered_count += 1
            else:
                threshold = max(0.12, max_chunk_score * 0.45)
                filtered = [item for score, item in scored_chunks if score >= threshold]
                if filtered:
                    chunk_payloads = filtered
                else:
                    chunk_payloads = []
                    low_relevance_filtered_count += 1
        elif max_chunk_score > 0:
            threshold = max(0.06, max_chunk_score * 0.4)
            filtered = [item for score, item in scored_chunks if score >= threshold]
            chunk_payloads = filtered or [item for _, item in sorted(scored_chunks, key=lambda row: row[0], reverse=True)[:8]]

        for item in build_evidence_from_chunks(paper.id, chunk_payloads, limit=payload.max_cards):
            if evidence_count >= payload.max_cards:
                break
            db.add(
                EvidenceCard(
                    id=str(uuid4()),
                    project_id=project_id,
                    paper_id=paper.id,
                    chunk_ids=item["chunk_ids"],
                    claim=item["claim"],
                    supporting_text=item["supporting_text"],
                    evidence_type=item["evidence_type"],
                    strength=item["strength"],
                    limitations=item["limitations"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    citation_key=item["citation_key"],
                    used_in_draft=False,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            evidence_count += 1
        if evidence_count >= payload.max_cards:
            break

    if evidence_count == 0:
        for paper in selected_papers:
            if evidence_count >= payload.max_cards:
                break
            if _is_fallback_source(paper):
                continue
            paper_score = _paper_query_score(paper, query)
            facet_coverage = _paper_facet_coverage(paper, query)
            if paper_score < 0.18 or facet_coverage < 0.5:
                low_relevance_filtered_count += 1
                continue
            abstract = (paper.abstract or "").strip()
            if len(abstract) < 40:
                continue
            pseudo_chunk = {
                "id": str(uuid4()),
                "text": f"{paper.title}\n{abstract}"[:2400],
                "page_start": None,
                "page_end": None,
            }
            for item in build_evidence_from_chunks(paper.id, [pseudo_chunk], limit=1):
                if evidence_count >= payload.max_cards:
                    break
                db.add(
                    EvidenceCard(
                        id=str(uuid4()),
                        project_id=project_id,
                        paper_id=paper.id,
                        chunk_ids=[],
                        claim=item["claim"],
                        supporting_text=item["supporting_text"],
                        evidence_type=item["evidence_type"],
                        strength="low",
                        limitations=(
                            "Metadata-only evidence (title/abstract). "
                            "Full PDF unavailable or parsing failed; manual verification required."
                        ),
                        page_start=None,
                        page_end=None,
                        citation_key=item["citation_key"],
                        used_in_draft=False,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
                evidence_count += 1
                metadata_fallback_evidence_count += 1
        if metadata_fallback_evidence_count > 0:
            add_log(
                task_id,
                f"metadata fallback evidence generated: {metadata_fallback_evidence_count}",
            )

    db.flush()
    if evidence_count == 0:
        skipped_titles = [item["title"] for item in paper_diagnostics if item.get("status") == "skipped_no_pdf"][:6]
        failed_items = [
            {"title": item.get("title"), "error": item.get("error")}
            for item in paper_diagnostics
            if item.get("status") == "failed"
        ][:6]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_EVIDENCE_CARDS",
                "title": "No evidence cards were generated",
                "message": (
                    "All selected papers were skipped or failed before parsing. "
                    "At least one selected paper must provide a downloadable or uploaded PDF."
                ),
                "summary": {
                    "selected_count": len(selected_papers),
                    "reused_local_pdf_count": reused_local_pdf_count,
                    "resolved_via_fallback_count": resolved_via_fallback_count,
                    "downloaded_count": downloaded_count,
                    "parsed_count": parsed_count,
                    "skipped_no_pdf_count": skipped_no_pdf_count,
                    "failed_count": failed_count,
                    "evidence_count": evidence_count,
                    "metadata_fallback_evidence_count": metadata_fallback_evidence_count,
                    "low_relevance_filtered_count": low_relevance_filtered_count,
                },
                "skipped_titles": skipped_titles,
                "failed_items": failed_items,
                "next_actions": [
                    "In Paper Library, keep at least one selected paper with a valid downloadable PDF.",
                    "If auto download fails, upload a local PDF manually and parse it once.",
                    "Refine the query with domain and audience constraints (for example: beginner, learning path, higher education).",
                    "If only metadata is available, manually verify generated claims before publication.",
                    "Re-run One-click Auto Workflow after at least one paper reaches parsed status.",
                ],
                "paper_diagnostics": paper_diagnostics[:20],
            },
        )

    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
    add_log(task_id, f"evidence cards built: {len(cards)}")
    set_progress(task_id, 78, "generating draft")
    draft = Draft(
        id=str(uuid4()),
        project_id=project_id,
        version=_next_draft_version(project_id, db),
        title=payload.draft_title or f"{project.title} Draft",
        content_md=build_draft_markdown(
            project_title=payload.draft_title or project.title,
            research_question=project.research_question,
            article_type=project.article_type,
            citation_style=project.citation_style,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
        ),
        status="draft",
        quality_score={"overall_score": 0.75},
        created_at=_now(),
    )
    db.add(draft)
    db.flush()

    db.flush()
    add_log(task_id, "draft generated and flushed")
    set_progress(task_id, 86, "reviewing draft")
    review_payloads, review_metrics = review_draft_with_metrics(
        draft.content_md,
        evidence_cards=[_evidence_to_dict(item) for item in cards],
        article_type=project.article_type,
    )
    db.execute(delete(ReviewIssue).where(ReviewIssue.project_id == project_id))
    created_issues: list[ReviewIssue] = []
    for payload_item in review_payloads:
        issue = ReviewIssue(
            id=str(uuid4()),
            project_id=project_id,
            draft_id=draft.id,
            severity=payload_item["severity"],
            issue_type=payload_item["issue_type"],
            location=payload_item["location"],
            claim=payload_item["claim"],
            description=payload_item["description"],
            suggestion=payload_item["suggestion"],
            evidence_ids=payload_item["evidence_ids"],
            resolved=False,
            created_at=_now(),
        )
        db.add(issue)
        created_issues.append(issue)
    critical_count = len([item for item in created_issues if item.severity == "high"])
    draft.status = "reviewed"
    draft.quality_score = score_quality(len(created_issues), critical_count, metrics=review_metrics)

    # Multi-round revision loop (max 3) with early stop conditions.
    # Stop when publication gate passes, or two consecutive rounds improve < 0.02.
    max_revision_rounds = 3
    min_improvement = 0.02
    current_content = draft.content_md
    current_issues = review_payloads
    current_metrics = review_metrics
    previous_overall = float(current_metrics.get("overall_score") or 0.0)
    stagnant_rounds = 0
    rounds_executed = 0

    best_content = current_content
    best_issues = current_issues
    best_metrics = current_metrics
    best_score = previous_overall

    # Quality gate snapshot history
    review_rounds: list[dict[str, Any]] = [
        {
            "round": 0,
            "stage": "initial_review",
            "metrics": dict(review_metrics),
        }
    ]

    for round_index in range(1, max_revision_rounds + 1):
        if bool(current_metrics.get("publication_prepared")):
            add_log(task_id, f"revision stop: publication gate passed before round {round_index}")
            break

        set_progress(task_id, min(96, 90 + round_index * 2), f"revising draft round {round_index}/{max_revision_rounds}")
        revised_candidate = revise_draft(
            current_content,
            issues=[
                {
                    "issue_type": str(item.get("issue_type") or ""),
                    "severity": str(item.get("severity") or ""),
                    "location": str(item.get("location") or ""),
                }
                for item in current_issues
            ],
        )
        revised_issues, revised_metrics = review_draft_with_metrics(
            revised_candidate,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
            article_type=project.article_type,
        )

        rounds_executed += 1
        overall = float(revised_metrics.get("overall_score") or 0.0)
        improvement = overall - previous_overall
        add_log(
            task_id,
            f"revision round {round_index}: overall={overall:.3f}, delta={improvement:.3f}, "
            f"critical={revised_metrics.get('critical_issues')}, "
            f"unsupported={revised_metrics.get('unsupported_claims')}",
        )

        review_rounds.append(
            {
                "round": round_index,
                "stage": "revision",
                "metrics": dict(revised_metrics),
                "improvement": round(improvement, 6),
            }
        )

        if overall > best_score:
            best_score = overall
            best_content = revised_candidate
            best_issues = revised_issues
            best_metrics = revised_metrics

        current_content = revised_candidate
        current_issues = revised_issues
        current_metrics = revised_metrics

        if improvement < min_improvement:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        previous_overall = overall

        if bool(revised_metrics.get("publication_prepared")):
            add_log(task_id, f"revision stop: publication gate passed at round {round_index}")
            break
        if stagnant_rounds >= 2:
            add_log(task_id, "revision stop: two consecutive rounds improved < 0.02")
            break

    revised_content = best_content
    revised_issue_payloads = best_issues
    revised_metrics = best_metrics
    revised_critical_count = len([item for item in revised_issue_payloads if item.get("severity") == "high"])
    revised_status = "publication_prepared" if revised_metrics.get("publication_prepared") else "revised_needs_human_review"
    revised_draft = Draft(
        id=str(uuid4()),
        project_id=project_id,
        version=_next_draft_version(project_id, db),
        title=(draft.title or "Draft") + " (Revised)",
        content_md=revised_content,
        status=revised_status,
        quality_score=score_quality(
            len(revised_issue_payloads),
            revised_critical_count,
            metrics=revised_metrics,
        ),
        created_at=_now(),
    )
    db.add(revised_draft)
    db.flush()

    export_files: dict[str, str] = {}
    add_log(task_id, f"revision done: rounds={rounds_executed}, best_score={best_score:.3f}")
    if payload.auto_export:
        set_progress(task_id, 97, "exporting package")
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        stamp = _timestamp()

        md_path = export_markdown(target_dir, f"draft_{revised_draft.version}_{stamp}.md", revised_draft.content_md)
        docx_path = export_docx(target_dir, f"draft_{revised_draft.version}_{stamp}.docx", revised_draft.content_md)
        pdf_path = export_pdf(target_dir, f"draft_{revised_draft.version}_{stamp}.pdf", revised_draft.content_md)

        bib_papers = selected_papers or papers
        bib_path = export_bibtex(
            target_dir,
            f"refs_{stamp}.bib",
            [
                {
                    "title": item.title,
                    "authors": item.authors,
                    "year": item.year,
                    "venue": item.venue,
                    "doi": item.doi,
                    "arxiv_id": item.arxiv_id,
                }
                for item in bib_papers
            ],
        )

        evidence_map_path = export_json(
            target_dir,
            f"evidence_map_{stamp}.json",
            [
                {
                    "id": item.id,
                    "paper_id": item.paper_id,
                    "chunk_ids": item.chunk_ids,
                    "claim": item.claim,
                    "supporting_text": item.supporting_text,
                    "evidence_type": item.evidence_type,
                    "strength": item.strength,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                }
                for item in cards
            ],
        )
        review_lines = ["# Review Report", ""]
        if not created_issues:
            review_lines.append("No review issues found.")
        for idx, item in enumerate(created_issues, start=1):
            review_lines.extend(
                [
                    f"## Issue {idx}",
                    f"- severity: {item.severity}",
                    f"- issue_type: {item.issue_type}",
                    f"- location: {item.location or 'n/a'}",
                    f"- description: {item.description}",
                    f"- suggestion: {item.suggestion or 'n/a'}",
                    "",
                ]
            )
        review_path = export_markdown(target_dir, f"review_report_{stamp}.md", "\n".join(review_lines))

        quality_path = export_quality_report(
            target_dir,
            f"quality_report_{stamp}.json",
            draft_version=revised_draft.version,
            review_rounds=review_rounds,
            final_metrics=dict(revised_metrics),
            publication_prepared=bool(revised_metrics.get("publication_prepared")),
        )

        export_files = {
            "markdown": str(md_path),
            "docx": str(docx_path),
            "pdf": str(pdf_path),
            "bibtex": str(bib_path),
            "evidence_map": str(evidence_map_path),
            "review_report": str(review_path),
            "quality_report": str(quality_path),
        }

    db.commit()
    result = {
        "query": query,
        "inserted_count": inserted,
        "total_papers": len(papers),
        "selected_count": len(selected_papers),
        "auto_selected_count": auto_selected_count,
        "reused_local_pdf_count": reused_local_pdf_count,
        "resolved_via_fallback_count": resolved_via_fallback_count,
        "downloaded_count": downloaded_count,
        "parsed_count": parsed_count,
        "skipped_no_pdf_count": skipped_no_pdf_count,
        "failed_count": failed_count,
        "evidence_count": evidence_count,
        "metadata_fallback_evidence_count": metadata_fallback_evidence_count,
        "low_relevance_filtered_count": low_relevance_filtered_count,
        "draft_id": draft.id,
        "revised_draft_id": revised_draft.id,
        "review_issue_count": len(created_issues),
        "critical_issue_count": revised_critical_count,
        "revision_rounds_executed": rounds_executed,
        "publication_prepared": bool(revised_metrics.get("publication_prepared")),
        "quality_gate": revised_metrics,
        "export_files": export_files,
    }
    return result


@router.post("/projects/{project_id}/run-auto-workflow")
def run_auto_workflow(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session = Depends(get_db)
) -> dict:
    task = create_task("run-auto-workflow")
    try:
        result = _execute_auto_workflow(project_id=project_id, payload=payload, db=db, task_id=task.task_id)
        complete_task(task.task_id, result)
        return {"task_id": task.task_id, **result}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/run-auto-workflow-async")
def run_auto_workflow_async(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session = Depends(get_db)
) -> dict:
    _get_project_or_404(project_id, db)
    db.close()  # close main thread session before starting worker to avoid shared-connection ROLLBACK
    task = create_task("run-auto-workflow")
    payload_data = payload.model_dump()

    def _runner() -> None:
        add_log(task.task_id, "worker thread started")
        worker_db = SessionLocal()
        add_log(task.task_id, "worker db session created")
        try:
            worker_payload = RunAutoWorkflowRequest.model_validate(payload_data)
            add_log(task.task_id, "payload validated")
            result = _execute_auto_workflow(
                project_id=project_id,
                payload=worker_payload,
                db=worker_db,
                task_id=task.task_id,
            )
            add_log(task.task_id, "workflow execution finished")
            complete_task(task.task_id, result)
        except Exception as exc:  # noqa: BLE001
            add_log(task.task_id, f"worker exception: {exc}")
            worker_db.rollback()
            _fail_task_for_exception(task.task_id, exc)
        finally:
            worker_db.close()

    Thread(target=_runner, daemon=True).start()
    return {"task_id": task.task_id, "status": "running"}


@router.post("/projects/{project_id}/retrieve-chunks")
def retrieve_chunks(
    project_id: str, payload: RetrieveChunksRequest, db: Session = Depends(get_db)
) -> dict:
    _get_project_or_404(project_id, db)
    stmt = (
        select(PaperChunk, Paper)
        .join(Paper, Paper.id == PaperChunk.paper_id)
        .where(Paper.project_id == project_id)
    )
    rows = db.execute(stmt).all()
    chunk_rows = [
        {
            "id": chunk.id,
            "paper_id": paper.id,
            "paper_title": paper.title,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "text": chunk.text,
            "section": chunk.section,
        }
        for chunk, paper in rows
    ]
    ranked = rank_chunks(payload.query, chunk_rows, top_k=payload.top_k)
    return {"query": payload.query, "count": len(ranked), "items": ranked}


@router.post("/projects/{project_id}/build-evidence")
def build_evidence(
    project_id: str, payload: BuildEvidenceRequest, db: Session = Depends(get_db)
) -> dict:
    _get_project_or_404(project_id, db)
    task = create_task("build-evidence")
    try:
        set_progress(task.task_id, 20, "loading chunks")
        papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
        if payload.only_selected and any(item.selected for item in papers):
            papers = [item for item in papers if item.selected]

        chunks_by_paper: dict[str, list[PaperChunk]] = {}
        for paper in papers:
            chunks = list(
                db.scalars(
                    select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.created_at)
                ).all()
            )
            if chunks:
                chunks_by_paper[paper.id] = chunks

        db.execute(delete(EvidenceCard).where(EvidenceCard.project_id == project_id))

        created = 0
        set_progress(task.task_id, 55, "generating cards")
        for paper in papers:
            chunks = chunks_by_paper.get(paper.id, [])
            chunk_payloads = [
                {
                    "id": chunk.id,
                    "text": chunk.text,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                }
                for chunk in chunks
            ]
            for item in build_evidence_from_chunks(paper.id, chunk_payloads, limit=payload.max_cards):
                if created >= payload.max_cards:
                    break
                card = EvidenceCard(
                    id=str(uuid4()),
                    project_id=project_id,
                    paper_id=paper.id,
                    chunk_ids=item["chunk_ids"],
                    claim=item["claim"],
                    supporting_text=item["supporting_text"],
                    evidence_type=item["evidence_type"],
                    strength=item["strength"],
                    limitations=item["limitations"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    citation_key=item["citation_key"],
                    used_in_draft=False,
                    created_at=_now(),
                    updated_at=_now(),
                )
                db.add(card)
                created += 1
            if created >= payload.max_cards:
                break

        db.commit()
        complete_task(task.task_id, {"evidence_count": created})
        return {"task_id": task.task_id, "evidence_count": created}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/generate-outline")
def generate_outline(
    project_id: str, payload: GenerateOutlineRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    del payload
    task = create_task("generate-outline")
    try:
        cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
        content = build_outline(
            project_title=project.title,
            research_question=project.research_question,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
        )
        draft = Draft(
            id=str(uuid4()),
            project_id=project_id,
            version=_next_draft_version(project_id, db),
            title=f"{project.title} Outline",
            content_md=content,
            status="outline",
            quality_score={"overall_score": 0.7},
            created_at=_now(),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        complete_task(task.task_id, {"draft_id": draft.id, "version": draft.version})
        return {"task_id": task.task_id, "draft_id": draft.id, "version": draft.version}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/generate-draft")
def generate_draft(
    project_id: str, payload: GenerateDraftRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    task = create_task("generate-draft")
    try:
        cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
        if not cards:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "NO_EVIDENCE_CARDS",
                    "title": "Cannot generate draft without evidence cards",
                    "message": "Build evidence cards first, then generate draft.",
                },
            )
        content = build_draft_markdown(
            project_title=payload.title or project.title,
            research_question=project.research_question,
            article_type=project.article_type,
            citation_style=project.citation_style,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
        )
        draft = Draft(
            id=str(uuid4()),
            project_id=project_id,
            version=_next_draft_version(project_id, db),
            title=payload.title or f"{project.title} Draft",
            content_md=content,
            status="draft",
            quality_score={"overall_score": 0.75},
            created_at=_now(),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        complete_task(task.task_id, {"draft_id": draft.id, "version": draft.version})
        return {"task_id": task.task_id, "draft_id": draft.id, "version": draft.version}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/review-draft")
def run_review(
    project_id: str, payload: ReviewDraftRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    draft = _get_draft_or_404(payload.draft_id, project_id, db)
    task = create_task("review-draft")
    try:
        cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
        issue_payloads, review_metrics = review_draft_with_metrics(
            draft.content_md,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
            article_type=project.article_type,
        )
        db.execute(delete(ReviewIssue).where(ReviewIssue.draft_id == draft.id))

        created_issues: list[ReviewIssue] = []
        for payload_item in issue_payloads:
            issue = ReviewIssue(
                id=str(uuid4()),
                project_id=project_id,
                draft_id=draft.id,
                severity=payload_item["severity"],
                issue_type=payload_item["issue_type"],
                location=payload_item["location"],
                claim=payload_item["claim"],
                description=payload_item["description"],
                suggestion=payload_item["suggestion"],
                evidence_ids=payload_item["evidence_ids"],
                resolved=False,
                created_at=_now(),
            )
            db.add(issue)
            created_issues.append(issue)

        critical_count = len([item for item in created_issues if item.severity == "high"])
        draft.status = "reviewed"
        draft.quality_score = score_quality(len(created_issues), critical_count, metrics=review_metrics)
        db.commit()
        complete_task(
            task.task_id,
            {
                "issue_count": len(created_issues),
                "critical_count": critical_count,
                "publication_prepared": bool(review_metrics.get("publication_prepared")),
                "quality_gate": review_metrics,
            },
        )
        return {
            "task_id": task.task_id,
            "draft_id": draft.id,
            "issue_count": len(created_issues),
            "critical_count": critical_count,
            "publication_prepared": bool(review_metrics.get("publication_prepared")),
            "quality_gate": review_metrics,
        }
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/revise-draft")
def run_revision(
    project_id: str, payload: ReviseDraftRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    draft = _get_draft_or_404(payload.draft_id, project_id, db)
    task = create_task("revise-draft")
    try:
        issues = list(
            db.scalars(
                select(ReviewIssue).where(ReviewIssue.draft_id == draft.id).order_by(ReviewIssue.created_at)
            ).all()
        )
        revised_content = revise_draft(
            draft.content_md,
            issues=[
                {
                    "issue_type": item.issue_type,
                    "severity": item.severity,
                    "location": item.location,
                }
                for item in issues
            ],
        )
        cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
        revised_issue_payloads, revised_metrics = review_draft_with_metrics(
            revised_content,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
            article_type=project.article_type,
        )
        revised_critical_count = len([item for item in revised_issue_payloads if item.get("severity") == "high"])
        revised_status = "publication_prepared" if revised_metrics.get("publication_prepared") else "revised_needs_human_review"
        new_draft = Draft(
            id=str(uuid4()),
            project_id=project_id,
            version=_next_draft_version(project_id, db),
            title=(draft.title or "Draft") + " (Revised)",
            content_md=revised_content,
            status=revised_status,
            quality_score=score_quality(
                len(revised_issue_payloads),
                revised_critical_count,
                metrics=revised_metrics,
            ),
            created_at=_now(),
        )
        db.add(new_draft)
        db.commit()
        complete_task(
            task.task_id,
            {
                "draft_id": new_draft.id,
                "version": new_draft.version,
                "publication_prepared": bool(revised_metrics.get("publication_prepared")),
                "quality_gate": revised_metrics,
            },
        )
        return {
            "task_id": task.task_id,
            "draft_id": new_draft.id,
            "version": new_draft.version,
            "publication_prepared": bool(revised_metrics.get("publication_prepared")),
            "quality_gate": revised_metrics,
        }
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise
