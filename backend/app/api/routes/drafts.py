from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Draft, Project
from app.schemas import DraftCreate, DraftRead, DraftUpdate

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
