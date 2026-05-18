from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Draft, Project, ReviewIssue
from app.schemas import ReviewIssueCreate, ReviewIssueRead, ReviewIssueUpdate

router = APIRouter(prefix="/api", tags=["review-issues"])


def _get_project_or_404(project_id: str, db: Session) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _get_draft_for_project_or_404(project_id: str, draft_id: str, db: Session) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None or draft.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found in project")
    return draft


def _get_issue_or_404(issue_id: str, db: Session) -> ReviewIssue:
    issue = db.get(ReviewIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review issue not found")
    return issue


@router.post(
    "/projects/{project_id}/review-issues",
    response_model=ReviewIssueRead,
    status_code=status.HTTP_201_CREATED,
)
def create_review_issue(
    project_id: str, payload: ReviewIssueCreate, db: Session = Depends(get_db)
) -> ReviewIssue:
    _get_project_or_404(project_id, db)
    _get_draft_for_project_or_404(project_id, payload.draft_id, db)
    issue = ReviewIssue(
        id=str(uuid4()),
        project_id=project_id,
        draft_id=payload.draft_id,
        severity=payload.severity.strip(),
        issue_type=payload.issue_type.strip(),
        location=payload.location.strip() if payload.location else None,
        claim=payload.claim.strip() if payload.claim else None,
        description=payload.description.strip(),
        suggestion=payload.suggestion.strip() if payload.suggestion else None,
        evidence_ids=payload.evidence_ids,
        resolved=payload.resolved,
        created_at=datetime.now(timezone.utc),
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return issue


@router.get("/projects/{project_id}/review-issues", response_model=list[ReviewIssueRead])
def list_review_issues(project_id: str, db: Session = Depends(get_db)) -> list[ReviewIssue]:
    _get_project_or_404(project_id, db)
    stmt = (
        select(ReviewIssue)
        .where(ReviewIssue.project_id == project_id)
        .order_by(ReviewIssue.created_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.get("/review-issues/{issue_id}", response_model=ReviewIssueRead)
def get_review_issue(issue_id: str, db: Session = Depends(get_db)) -> ReviewIssue:
    return _get_issue_or_404(issue_id, db)


@router.patch("/review-issues/{issue_id}", response_model=ReviewIssueRead)
def update_review_issue(
    issue_id: str, payload: ReviewIssueUpdate, db: Session = Depends(get_db)
) -> ReviewIssue:
    issue = _get_issue_or_404(issue_id, db)
    changes = payload.model_dump(exclude_unset=True)
    if "draft_id" in changes and changes["draft_id"] is not None:
        _get_draft_for_project_or_404(issue.project_id, changes["draft_id"], db)
    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(issue, field, value)
    db.commit()
    db.refresh(issue)
    return issue


@router.delete("/review-issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_review_issue(issue_id: str, db: Session = Depends(get_db)) -> Response:
    issue = _get_issue_or_404(issue_id, db)
    db.delete(issue)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
