from fastapi import APIRouter, HTTPException, status

from app.services.task_registry import get_task

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks/{task_id}")
def get_task_status(task_id: str) -> dict:
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task

