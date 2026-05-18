from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import EvidenceCard, Paper, Project
from app.schemas import EvidenceCardCreate, EvidenceCardRead, EvidenceCardUpdate

router = APIRouter(prefix="/api", tags=["evidence"])


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_paper_for_project_or_404(project_id: str, paper_id: str, db: Session) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None or paper.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found in project")
    return paper


def _get_evidence_or_404(evidence_id: str, db: Session) -> EvidenceCard:
    evidence = db.get(EvidenceCard, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evidence card not found")
    return evidence


@router.post(
    "/projects/{project_id}/evidence",
    response_model=EvidenceCardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_evidence(
    project_id: str, payload: EvidenceCardCreate, db: Session = Depends(get_db)
) -> EvidenceCard:
    _get_project_or_404(project_id, db)
    _get_paper_for_project_or_404(project_id, payload.paper_id, db)
    now = datetime.now(timezone.utc)
    evidence = EvidenceCard(
        id=str(uuid4()),
        project_id=project_id,
        paper_id=payload.paper_id,
        chunk_ids=payload.chunk_ids,
        claim=payload.claim.strip(),
        supporting_text=payload.supporting_text.strip(),
        evidence_type=payload.evidence_type.strip() if payload.evidence_type else None,
        strength=payload.strength.strip() if payload.strength else None,
        limitations=payload.limitations.strip() if payload.limitations else None,
        page_start=payload.page_start,
        page_end=payload.page_end,
        citation_key=payload.citation_key.strip() if payload.citation_key else None,
        used_in_draft=payload.used_in_draft,
        created_at=now,
        updated_at=now,
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


@router.get("/projects/{project_id}/evidence", response_model=list[EvidenceCardRead])
def list_evidence(project_id: str, db: Session = Depends(get_db)) -> list[EvidenceCard]:
    _get_project_or_404(project_id, db)
    stmt = (
        select(EvidenceCard)
        .where(EvidenceCard.project_id == project_id)
        .order_by(EvidenceCard.created_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.get("/evidence/{evidence_id}", response_model=EvidenceCardRead)
def get_evidence(evidence_id: str, db: Session = Depends(get_db)) -> EvidenceCard:
    return _get_evidence_or_404(evidence_id, db)


@router.patch("/evidence/{evidence_id}", response_model=EvidenceCardRead)
def update_evidence(
    evidence_id: str, payload: EvidenceCardUpdate, db: Session = Depends(get_db)
) -> EvidenceCard:
    evidence = _get_evidence_or_404(evidence_id, db)
    changes = payload.model_dump(exclude_unset=True)
    if "paper_id" in changes and changes["paper_id"] is not None:
        _get_paper_for_project_or_404(evidence.project_id, changes["paper_id"], db)

    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(evidence, field, value)
    evidence.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(evidence)
    return evidence


@router.delete("/evidence/{evidence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evidence(evidence_id: str, db: Session = Depends(get_db)) -> Response:
    evidence = _get_evidence_or_404(evidence_id, db)
    db.delete(evidence)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
