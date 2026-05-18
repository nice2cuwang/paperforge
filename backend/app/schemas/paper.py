from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PaperCreate(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    authors: list = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1800, le=2100)
    doi: str | None = Field(default=None, max_length=255)
    arxiv_id: str | None = Field(default=None, max_length=64)
    venue: str | None = Field(default=None, max_length=1000)
    abstract: str | None = None
    source: str | None = Field(default=None, max_length=64)
    source_url: str | None = Field(default=None, max_length=2000)
    pdf_url: str | None = Field(default=None, max_length=2000)
    oa_status: str | None = Field(default=None, max_length=32)
    license: str | None = Field(default=None, max_length=128)
    local_pdf_path: str | None = Field(default=None, max_length=2000)
    local_tei_path: str | None = Field(default=None, max_length=2000)
    relevance_score: float = 0.0
    selected: bool = False
    parse_status: str = Field(default="pending", max_length=32)
    metadata_json: dict = Field(default_factory=dict)


class PaperUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    authors: list | None = None
    year: int | None = Field(default=None, ge=1800, le=2100)
    doi: str | None = Field(default=None, max_length=255)
    arxiv_id: str | None = Field(default=None, max_length=64)
    venue: str | None = Field(default=None, max_length=1000)
    abstract: str | None = None
    source: str | None = Field(default=None, max_length=64)
    source_url: str | None = Field(default=None, max_length=2000)
    pdf_url: str | None = Field(default=None, max_length=2000)
    oa_status: str | None = Field(default=None, max_length=32)
    license: str | None = Field(default=None, max_length=128)
    local_pdf_path: str | None = Field(default=None, max_length=2000)
    local_tei_path: str | None = Field(default=None, max_length=2000)
    relevance_score: float | None = None
    selected: bool | None = None
    parse_status: str | None = Field(default=None, max_length=32)
    metadata_json: dict | None = None


class PaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    authors: list
    year: int | None
    doi: str | None
    arxiv_id: str | None
    venue: str | None
    abstract: str | None
    source: str | None
    source_url: str | None
    pdf_url: str | None
    oa_status: str | None
    license: str | None
    local_pdf_path: str | None
    local_tei_path: str | None
    relevance_score: float
    selected: bool
    parse_status: str
    metadata_json: dict
    created_at: datetime
    updated_at: datetime
