from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import backend_dir
from app.models import Paper, PaperChunk
from app.services.http_client import create_httpx_client
from app.services.ingestion_service import chunk_text, extract_pdf_text, save_tei_placeholder, save_uploaded_pdf
from app.services import embedding_service, qdrant_service
from app.services.workflow.helpers import (
    _now,
    _extract_arxiv_id,
    _extract_doi_from_text,
    _extract_pdf_from_openalex_work,
    _normalize_doi,
)
from app.services.task_registry import add_log

logger = logging.getLogger(__name__)

HTTP_HEADERS = {"User-Agent": "PaperForge/0.3 (+https://paperforge.local)"}
UNPAYWALL_EMAIL = "paperforge@local.dev"

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
