from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Draft, EvidenceCard, Project
from app.schemas import DraftCreate, DraftRead, DraftUpdate, GenerateDraftRequest, GenerateOutlineRequest
from app.services.task_registry import complete_task, create_task, _fail_task_for_exception
from app.services.workflow.helpers import _evidence_to_dict, _next_draft_version, _now
from app.services.writing_service import build_draft_markdown, build_outline

router = APIRouter(prefix="/api", tags=["drafts"])


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_draft_or_404(draft_id: str, db: Session) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    return draft


def _ensure_unique_version(project_id: str, version: int, db: Session, skip_draft_id: str | None = None) -> None:
    stmt = select(Draft).where(Draft.project_id == project_id, Draft.version == version)
    existing = db.scalars(stmt).first()
    if existing is not None and existing.id != skip_draft_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Draft version {version} already exists in this project",
        )


@router.post("/projects/{project_id}/drafts", response_model=DraftRead, status_code=status.HTTP_201_CREATED)
def create_draft(project_id: str, payload: DraftCreate, db: Session = Depends(get_db)) -> Draft:
    _get_project_or_404(project_id, db)
    draft = Draft(
        id=str(uuid4()),
        project_id=project_id,
        version=payload.version,
        title=payload.title.strip() if payload.title else None,
        content_md=payload.content_md.strip(),
        status=payload.status.strip(),
        quality_score=payload.quality_score,
        created_at=datetime.now(timezone.utc),
    )
    db.add(draft)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Draft version {payload.version} already exists in this project",
        ) from exc
    db.refresh(draft)
    return draft


@router.get("/projects/{project_id}/drafts", response_model=list[DraftRead])
def list_drafts(project_id: str, db: Session = Depends(get_db)) -> list[Draft]:
    _get_project_or_404(project_id, db)
    stmt = select(Draft).where(Draft.project_id == project_id).order_by(Draft.version.desc())
    return list(db.scalars(stmt).all())


@router.get("/drafts/{draft_id}", response_model=DraftRead)
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> Draft:
    return _get_draft_or_404(draft_id, db)


@router.patch("/drafts/{draft_id}", response_model=DraftRead)
def update_draft(draft_id: str, payload: DraftUpdate, db: Session = Depends(get_db)) -> Draft:
    draft = _get_draft_or_404(draft_id, db)
    changes = payload.model_dump(exclude_unset=True)
    new_version = changes.get("version")

    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(draft, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Draft version {new_version} already exists in this project",
        ) from exc
    db.refresh(draft)
    return draft


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(draft_id: str, db: Session = Depends(get_db)) -> Response:
    draft = _get_draft_or_404(draft_id, db)
    db.delete(draft)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
