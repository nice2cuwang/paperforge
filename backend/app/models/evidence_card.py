from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.paper import Paper
    from app.models.project import Project


class EvidenceCard(Base):
    __tablename__ = "evidence_cards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, default=None, index=True,
        comment="academic | web | community | llm_knowledge"
    )
    strength: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    limitations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    citation_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    used_in_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="evidence_cards")
    paper: Mapped["Paper"] = relationship(back_populates="evidence_cards")
