from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Paper, Project
from app.schemas import PaperCreate, PaperRead, PaperUpdate

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
