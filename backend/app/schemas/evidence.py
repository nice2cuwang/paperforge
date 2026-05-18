from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EvidenceCardCreate(BaseModel):
    paper_id: str = Field(min_length=36, max_length=36)
    chunk_ids: list = Field(default_factory=list)
    claim: str = Field(min_length=1)
    supporting_text: str = Field(min_length=1)
    evidence_type: str | None = Field(default=None, max_length=64)
    strength: str | None = Field(default=None, max_length=32)
    limitations: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    citation_key: str | None = Field(default=None, max_length=255)
    used_in_draft: bool = False


class EvidenceCardUpdate(BaseModel):
    paper_id: str | None = Field(default=None, min_length=36, max_length=36)
    chunk_ids: list | None = None
    claim: str | None = Field(default=None, min_length=1)
    supporting_text: str | None = Field(default=None, min_length=1)
    evidence_type: str | None = Field(default=None, max_length=64)
    strength: str | None = Field(default=None, max_length=32)
    limitations: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    citation_key: str | None = Field(default=None, max_length=255)
    used_in_draft: bool | None = None


class EvidenceCardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    paper_id: str
    chunk_ids: list
    claim: str
    supporting_text: str
    evidence_type: str | None
    strength: str | None
    limitations: str | None
    page_start: int | None
    page_end: int | None
    citation_key: str | None
    used_in_draft: bool
    created_at: datetime
    updated_at: datetime
