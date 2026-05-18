from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReviewIssueCreate(BaseModel):
    draft_id: str = Field(min_length=36, max_length=36)
    severity: str = Field(min_length=1, max_length=16)
    issue_type: str = Field(min_length=1, max_length=64)
    location: str | None = None
    claim: str | None = None
    description: str = Field(min_length=1)
    suggestion: str | None = None
    evidence_ids: list = Field(default_factory=list)
    resolved: bool = False


class ReviewIssueUpdate(BaseModel):
    draft_id: str | None = Field(default=None, min_length=36, max_length=36)
    severity: str | None = Field(default=None, min_length=1, max_length=16)
    issue_type: str | None = Field(default=None, min_length=1, max_length=64)
    location: str | None = None
    claim: str | None = None
    description: str | None = Field(default=None, min_length=1)
    suggestion: str | None = None
    evidence_ids: list | None = None
    resolved: bool | None = None


class ReviewIssueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    draft_id: str
    severity: str
    issue_type: str
    location: str | None
    claim: str | None
    description: str
    suggestion: str | None
    evidence_ids: list
    resolved: bool
    created_at: datetime
