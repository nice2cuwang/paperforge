from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DraftCreate(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=1000)
    content_md: str = Field(min_length=1)
    status: str = Field(default="draft", max_length=32)
    quality_score: dict = Field(default_factory=dict)


class DraftUpdate(BaseModel):
    version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=1000)
    content_md: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, max_length=32)
    quality_score: dict | None = None


class DraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version: int
    title: str | None
    content_md: str
    status: str
    quality_score: dict
    created_at: datetime
