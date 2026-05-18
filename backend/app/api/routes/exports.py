from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import backend_dir, get_db
from app.models import Draft, EvidenceCard, Paper, Project, ReviewIssue
from app.services.export_service import (
    ensure_export_dir,
    export_bibtex,
    export_docx,
    export_json,
    export_markdown,
    export_pdf,
)
from app.services.task_registry import complete_task, create_task, fail_task

router = APIRouter(prefix="/api", tags=["exports"])


def _project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _latest_draft_or_404(project_id: str, db: Session) -> Draft:
    draft = db.scalars(select(Draft).where(Draft.project_id == project_id).order_by(Draft.version.desc())).first()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No draft found for this project")
    return draft


def _response(path: Path, media_type: str) -> FileResponse:
    return FileResponse(path=str(path), filename=path.name, media_type=media_type)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


@router.post("/projects/{project_id}/export/markdown")
def export_project_markdown(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    draft = _latest_draft_or_404(project_id, db)
    task = create_task("export-markdown")
    try:
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_markdown(target_dir, f"draft_{draft.version}_{_timestamp()}.md", draft.content_md)
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(path, "text/markdown")
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise


@router.post("/projects/{project_id}/export/docx")
def export_project_docx(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    draft = _latest_draft_or_404(project_id, db)
    task = create_task("export-docx")
    try:
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_docx(target_dir, f"draft_{draft.version}_{_timestamp()}.docx", draft.content_md)
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(
            path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise


@router.post("/projects/{project_id}/export/pdf")
def export_project_pdf(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    draft = _latest_draft_or_404(project_id, db)
    task = create_task("export-pdf")
    try:
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_pdf(target_dir, f"draft_{draft.version}_{_timestamp()}.pdf", draft.content_md)
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(path, "application/pdf")
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise


@router.post("/projects/{project_id}/export/bibtex")
def export_project_bibtex(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    task = create_task("export-bibtex")
    try:
        papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id, Paper.selected == True)).all())  # noqa: E712
        if not papers:
            papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
        payload = [
            {
                "title": item.title,
                "authors": item.authors,
                "year": item.year,
                "venue": item.venue,
                "doi": item.doi,
                "arxiv_id": item.arxiv_id,
            }
            for item in papers
        ]
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_bibtex(target_dir, f"refs_{_timestamp()}.bib", payload)
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(path, "application/x-bibtex")
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise


@router.post("/projects/{project_id}/export/evidence-map")
def export_project_evidence_map(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    task = create_task("export-evidence-map")
    try:
        evidence = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
        payload = [
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
            for item in evidence
        ]
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_json(target_dir, f"evidence_map_{_timestamp()}.json", payload)
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(path, "application/json")
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise


@router.post("/projects/{project_id}/export/review-report")
def export_project_review_report(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    _project_or_404(project_id, db)
    task = create_task("export-review-report")
    try:
        issues = list(
            db.scalars(
                select(ReviewIssue).where(ReviewIssue.project_id == project_id).order_by(ReviewIssue.created_at)
            ).all()
        )
        lines = ["# Review Report", ""]
        if not issues:
            lines.append("No review issues found.")
        for idx, issue in enumerate(issues, start=1):
            lines.extend(
                [
                    f"## Issue {idx}",
                    f"- severity: {issue.severity}",
                    f"- issue_type: {issue.issue_type}",
                    f"- location: {issue.location or 'n/a'}",
                    f"- description: {issue.description}",
                    f"- suggestion: {issue.suggestion or 'n/a'}",
                    "",
                ]
            )
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        path = export_markdown(target_dir, f"review_report_{_timestamp()}.md", "\n".join(lines))
        complete_task(task.task_id, {"file_path": str(path)})
        return _response(path, "text/markdown")
    except Exception as exc:
        fail_task(task.task_id, str(exc))
        raise
