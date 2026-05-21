import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import backend_dir, get_db
from app.models import Paper, Project
from app.schemas import PaperCreate, PaperRead, PaperUpdate, ParsePaperRequest, SelectPaperRequest
from app.services.ingestion_service import save_uploaded_pdf
from app.services.task_registry import add_log, complete_task, create_task, set_progress, _fail_task_for_exception
from app.services.workflow.helpers import _now, _paper_to_dict
from app.services.workflow.ingest import _download_pdf_for_paper, _parse_paper_to_chunks, _resolve_pdf_url_with_fallback

router = APIRouter(prefix="/api", tags=["papers"])


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


@router.post("/projects/{project_id}/papers", response_model=PaperRead, status_code=status.HTTP_201_CREATED)
def create_paper(project_id: str, payload: PaperCreate, db: Session = Depends(get_db)) -> Paper:
    _get_project_or_404(project_id, db)
    now = datetime.now(timezone.utc)
    paper = Paper(
        id=str(uuid4()),
        project_id=project_id,
        title=payload.title.strip(),
        authors=payload.authors,
        year=payload.year,
        doi=payload.doi.strip() if payload.doi else None,
        arxiv_id=payload.arxiv_id.strip() if payload.arxiv_id else None,
        venue=payload.venue.strip() if payload.venue else None,
        abstract=payload.abstract.strip() if payload.abstract else None,
        source=payload.source.strip() if payload.source else None,
        source_url=payload.source_url.strip() if payload.source_url else None,
        pdf_url=payload.pdf_url.strip() if payload.pdf_url else None,
        oa_status=payload.oa_status.strip() if payload.oa_status else None,
        license=payload.license.strip() if payload.license else None,
        local_pdf_path=payload.local_pdf_path.strip() if payload.local_pdf_path else None,
        local_tei_path=payload.local_tei_path.strip() if payload.local_tei_path else None,
        relevance_score=payload.relevance_score,
        selected=payload.selected,
        parse_status=payload.parse_status.strip(),
        metadata_json=payload.metadata_json,
        created_at=now,
        updated_at=now,
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return paper


@router.get("/projects/{project_id}/papers", response_model=list[PaperRead])
def list_papers(project_id: str, db: Session = Depends(get_db)) -> list[Paper]:
    _get_project_or_404(project_id, db)
    stmt = (
        select(Paper)
        .where(Paper.project_id == project_id)
        .order_by(desc(Paper.selected), desc(Paper.relevance_score), Paper.created_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.get("/papers/{paper_id}", response_model=PaperRead)
def get_paper(paper_id: str, db: Session = Depends(get_db)) -> Paper:
    return _get_paper_or_404(paper_id, db)


@router.patch("/papers/{paper_id}", response_model=PaperRead)
def update_paper(paper_id: str, payload: PaperUpdate, db: Session = Depends(get_db)) -> Paper:
    paper = _get_paper_or_404(paper_id, db)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(paper, field, value)
    paper.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return paper


@router.delete("/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_paper(paper_id: str, db: Session = Depends(get_db)) -> Response:
    paper = _get_paper_or_404(paper_id, db)
    db.delete(paper)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
