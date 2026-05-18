from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    research_question: str = Field(min_length=1, max_length=4000)
    article_type: str = Field(min_length=1, max_length=64)
    target_audience: str | None = Field(default=None, max_length=1000)
    language: str = Field(default="zh", min_length=2, max_length=16)
    target_words: int = Field(default=5000, ge=500, le=50000)
    citation_style: str = Field(default="GB/T 7714", min_length=2, max_length=64)
    settings: dict = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    research_question: str | None = Field(default=None, min_length=1, max_length=4000)
    article_type: str | None = Field(default=None, min_length=1, max_length=64)
    target_audience: str | None = Field(default=None, max_length=1000)
    language: str | None = Field(default=None, min_length=2, max_length=16)
    target_words: int | None = Field(default=None, ge=500, le=50000)
    citation_style: str | None = Field(default=None, min_length=2, max_length=64)
    status: str | None = Field(default=None, min_length=1, max_length=32)
    settings: dict | None = Field(default=None)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    research_question: str
    article_type: str
    target_audience: str | None
    language: str
    target_words: int
    citation_style: str
    status: str
    settings: dict
    created_at: datetime
    updated_at: datetime
