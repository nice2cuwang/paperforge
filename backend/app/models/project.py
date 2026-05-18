from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.draft import Draft
    from app.models.evidence_card import EvidenceCard
    from app.models.paper import Paper
    from app.models.review_issue import ReviewIssue


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    article_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_audience: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="zh")
    target_words: Mapped[int] = mapped_column(Integer, default=5000)
    citation_style: Mapped[str] = mapped_column(String(64), default="GB/T 7714")
    status: Mapped[str] = mapped_column(String(32), default="created")
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    papers: Mapped[list["Paper"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    evidence_cards: Mapped[list["EvidenceCard"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    review_issues: Mapped[list["ReviewIssue"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
