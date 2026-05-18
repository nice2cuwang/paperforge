from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaperChunkCreate(BaseModel):
    section: str | None = Field(default=None, max_length=255)
    subsection: str | None = Field(default=None, max_length=255)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    token_count: int | None = Field(default=None, ge=0)
    vector_id: str | None = Field(default=None, max_length=255)
    metadata_json: dict = Field(default_factory=dict)


class PaperChunkUpdate(BaseModel):
    section: str | None = Field(default=None, max_length=255)
    subsection: str | None = Field(default=None, max_length=255)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    text: str | None = Field(default=None, min_length=1)
    token_count: int | None = Field(default=None, ge=0)
    vector_id: str | None = Field(default=None, max_length=255)
    metadata_json: dict | None = None


class PaperChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    paper_id: str
    section: str | None
    subsection: str | None
    page_start: int | None
    page_end: int | None
    text: str
    token_count: int | None
    vector_id: str | None
    metadata_json: dict
    created_at: datetime
