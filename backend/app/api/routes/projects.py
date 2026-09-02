from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import backend_dir, get_db
from app.models import Project
from app.schemas import ProjectCreate, ProjectRead, ProjectTokenUsage, ProjectUpdate
from app.services.usage_service import get_project_token_usage

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    now = datetime.now(timezone.utc)
    project = Project(
        id=str(uuid4()),
        title=payload.title.strip(),
        research_question=payload.research_question.strip(),
        article_type=payload.article_type.strip(),
        target_audience=payload.target_audience.strip() if payload.target_audience else None,
        language=payload.language.strip(),
        target_words=payload.target_words,
        citation_style=payload.citation_style.strip(),
        status="created",
        settings=payload.settings,
        created_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc())
    return list(db.scalars(stmt).all())


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(project, field, value)
    project.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, db: Session = Depends(get_db)) -> Response:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/token-usage", response_model=ProjectTokenUsage)
def get_token_usage(project_id: str, db: Session = Depends(get_db)) -> dict:
    """按项目聚合 LLM token 消耗（总量 / 按模型 / 按运行批次）。"""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return get_project_token_usage(project_id, db)


@router.get("/{project_id}/llm-calls")
def list_llm_calls(
    project_id: str,
    task_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict]:
    """项目发给模型的每次调用记录（对话式工作区透明化）。

    返回精简摘要（用途/模型/耗时/消耗/prompt 预览），完整 prompt 原文
    通过 ``/llm-calls/{call_id}`` 单条获取，避免列表响应过大。
    """
    from app.models.audit_log import AuditLog

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    stmt = select(AuditLog).where(AuditLog.project_id == project_id)
    if task_id:
        stmt = stmt.where(AuditLog.task_id == task_id)
    stmt = stmt.order_by(AuditLog.created_at.desc()).limit(min(max(limit, 1), 500))

    out: list[dict] = []
    for row in db.scalars(stmt).all():
        usage = row.usage or {}
        out.append({
            "id": row.id,
            "task_id": row.task_id,
            "purpose": row.purpose,
            "model": row.model,
            "provider": row.provider,
            "latency_ms": row.latency_ms,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "error": row.error,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "system_prompt_preview": (row.system_prompt_text or "")[:160],
            "user_prompt_preview": (row.user_prompt_text or "")[:200],
            # 响应预览：对话卡片直接展示"模型回了什么"，全文仍走详情端点
            "response_preview": (row.response_text or "")[:200],
        })
    return out


@router.get("/{project_id}/llm-calls/{call_id}")
def get_llm_call(project_id: str, call_id: str, db: Session = Depends(get_db)) -> dict:
    """单次 LLM 调用的完整 payload（system/user prompt 原文 + 响应）。"""
    from app.models.audit_log import AuditLog

    row = db.get(AuditLog, call_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="LLM call not found")
    return {
        "id": row.id,
        "task_id": row.task_id,
        "purpose": row.purpose,
        "provider": row.provider,
        "model": row.model,
        "strategy_mode": row.strategy_mode,
        "system_prompt": row.system_prompt_text,
        "user_prompt": row.user_prompt_text,
        "response": row.response_text,
        "latency_ms": row.latency_ms,
        "usage": row.usage,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/{project_id}/images/{filepath:path}")
def get_project_image(project_id: str, filepath: str) -> FileResponse:
    """Serve generated images for a project (supports subdirectories)."""
    # Prevent path traversal — block ".." components
    if ".." in filepath.split("/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filepath")

    images_dir = backend_dir / "data" / "storage" / project_id / "images"
    resolved = images_dir / filepath

    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")

    # Ensure the resolved path stays within images_dir (prevent traversal via symlinks)
    try:
        resolved.resolve().relative_to(images_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    suffix = resolved.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    return FileResponse(resolved, media_type=media_type)
