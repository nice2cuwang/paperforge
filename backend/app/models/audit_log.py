from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    """Immutable audit trail for every LLM invocation.

    Prompts are stored as SHA-256 hashes so the log can be shared or
    inspected without leaking sensitive content.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # 项目归属：由 llm_service 的任务上下文自动填充（见 set_task_context），
    # 用于按项目/按次运行聚合 token 消耗。历史行为 NULL。
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    strategy_mode: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    system_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 对话式工作区透明化：完整 prompt/响应原文（本地单用户工具，用户明确
    # 希望在 Chat 界面看到系统发给模型的数据）。截断存储防止极端长文撑爆库。
    system_prompt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_prompt_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 调用用途标签（如 writing/review/thesis/figures），便于前端归类展示
    purpose: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    temperature: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_format: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    usage: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
