from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, JSON, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="Default")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="openai")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="gpt-4o-mini")
    api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_base: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, default=4096, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    proxy_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    use_system_proxy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extra_headers: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    extra_body: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    strategy_mode: Mapped[str] = mapped_column(String(16), default="balanced", nullable=False)
    enable_reasoning: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preferred_max_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
