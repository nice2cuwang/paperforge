from __future__ import annotations

from threading import Thread
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Paper, Project
from app.schemas import RunAutoWorkflowRequest, SearchPapersRequest
from app.services.search_service import search_papers
from app.services.task_registry import add_log, complete_task, create_task, set_progress, _fail_task_for_exception
from app.services.workflow.helpers import _get_paper_or_404, _get_project_or_404, _paper_to_dict
from app.services.workflow.runner import _execute_auto_workflow
from app.services.workflow.search_select import _upsert_search_candidates

router = APIRouter(prefix="/api", tags=["workflow"])


@router.post("/projects/{project_id}/search-papers")
def search_project_papers(
    project_id: str, payload: SearchPapersRequest, db: Session = Depends(get_db)
) -> dict:
    project = _get_project_or_404(project_id, db)
    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    task = create_task("search-papers")
    try:
        set_progress(task.task_id, 10, "querying providers")
        candidates = search_papers(query=query, limit=payload.max_results)
        add_log(task.task_id, f"collected candidates: {len(candidates)}")
        inserted = _upsert_search_candidates(project_id=project_id, query=query, candidates=candidates, db=db)

        db.commit()
        set_progress(task.task_id, 80, "loading results")
        papers = list(db.scalars(select(Paper).where(Paper.project_id == project_id)).all())
        response = {
            "task_id": task.task_id,
            "query": query,
            "inserted": inserted,
            "total": len(papers),
            "papers": [_paper_to_dict(item) for item in papers],
        }
        complete_task(task.task_id, {"inserted": inserted, "total": len(papers)})
        return response
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/run-auto-workflow")
def run_auto_workflow(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session = Depends(get_db)
) -> dict:
    task = create_task("run-auto-workflow")
    try:
        result = _execute_auto_workflow(project_id=project_id, payload=payload, db=db, task_id=task.task_id)
        complete_task(task.task_id, result)
        return {"task_id": task.task_id, **result}
    except Exception as exc:
        db.rollback()
        _fail_task_for_exception(task.task_id, exc)
        raise


@router.post("/projects/{project_id}/run-auto-workflow-async")
def run_auto_workflow_async(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session = Depends(get_db)
) -> dict:
    _get_project_or_404(project_id, db)
    db.close()  # close main thread session before starting worker to avoid shared-connection ROLLBACK
    task = create_task("run-auto-workflow")
    payload_data = payload.model_dump()

    def _runner() -> None:
        add_log(task.task_id, "worker thread started")
        worker_db = SessionLocal()
        add_log(task.task_id, "worker db session created")
        try:
            worker_payload = RunAutoWorkflowRequest.model_validate(payload_data)
            add_log(task.task_id, "payload validated")
            result = _execute_auto_workflow(
                project_id=project_id,
                payload=worker_payload,
                db=worker_db,
                task_id=task.task_id,
            )
            add_log(task.task_id, "workflow execution finished")
            complete_task(task.task_id, result)
        except Exception as exc:  # noqa: BLE001
            add_log(task.task_id, f"worker exception: {exc}")
            worker_db.rollback()
            _fail_task_for_exception(task.task_id, exc)
        finally:
            worker_db.close()

    Thread(target=_runner, daemon=True).start()
    return {"task_id": task.task_id, "status": "running"}
