from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Draft, EvidenceCard, Project, ReviewIssue
from app.schemas import ReviewDraftRequest, ReviewIssueCreate, ReviewIssueRead, ReviewIssueUpdate, ReviseDraftRequest
from app.services.review_service import review_draft_with_metrics, revise_draft, score_quality
from app.services.task_registry import complete_task, create_task, _fail_task_for_exception
from app.services.workflow.helpers import _evidence_to_dict, _next_draft_version, _now

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


@router.post("/projects/{project_id}/review-draft")
def run_review(
    project_id: str, payload: ReviewDraftRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    draft = _get_draft_for_project_or_404(project_id, payload.draft_id, db)
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
    draft = _get_draft_for_project_or_404(project_id, payload.draft_id, db)
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
