from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Paper, PaperChunk
from app.schemas import PaperChunkCreate, PaperChunkRead, PaperChunkUpdate

router = APIRouter(prefix="/api", tags=["chunks"])


def _get_paper_or_404(paper_id: str, db: Session) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return paper


def _get_chunk_or_404(chunk_id: str, db: Session) -> PaperChunk:
    chunk = db.get(PaperChunk, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk not found")
    return chunk


@router.post("/papers/{paper_id}/chunks", response_model=PaperChunkRead, status_code=status.HTTP_201_CREATED)
def create_chunk(paper_id: str, payload: PaperChunkCreate, db: Session = Depends(get_db)) -> PaperChunk:
    _get_paper_or_404(paper_id, db)
    chunk = PaperChunk(
        id=str(uuid4()),
        paper_id=paper_id,
        section=payload.section.strip() if payload.section else None,
        subsection=payload.subsection.strip() if payload.subsection else None,
        page_start=payload.page_start,
        page_end=payload.page_end,
        text=payload.text.strip(),
        token_count=payload.token_count,
        vector_id=payload.vector_id.strip() if payload.vector_id else None,
        metadata_json=payload.metadata_json,
        created_at=datetime.now(timezone.utc),
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk


@router.get("/papers/{paper_id}/chunks", response_model=list[PaperChunkRead])
def list_chunks(paper_id: str, db: Session = Depends(get_db)) -> list[PaperChunk]:
    _get_paper_or_404(paper_id, db)
    stmt = select(PaperChunk).where(PaperChunk.paper_id == paper_id).order_by(PaperChunk.created_at.desc())
    return list(db.scalars(stmt).all())


@router.get("/chunks/{chunk_id}", response_model=PaperChunkRead)
def get_chunk(chunk_id: str, db: Session = Depends(get_db)) -> PaperChunk:
    return _get_chunk_or_404(chunk_id, db)


@router.patch("/chunks/{chunk_id}", response_model=PaperChunkRead)
def update_chunk(chunk_id: str, payload: PaperChunkUpdate, db: Session = Depends(get_db)) -> PaperChunk:
    chunk = _get_chunk_or_404(chunk_id, db)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(chunk, field, value)
    db.commit()
    db.refresh(chunk)
    return chunk


@router.delete("/chunks/{chunk_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chunk(chunk_id: str, db: Session = Depends(get_db)) -> Response:
    chunk = _get_chunk_or_404(chunk_id, db)
    db.delete(chunk)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
