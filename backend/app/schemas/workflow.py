from datetime import datetime

from pydantic import BaseModel, Field


class SearchPapersRequest(BaseModel):
    query: str | None = Field(default=None, max_length=1000)
    max_results: int = Field(default=30, ge=1, le=100)


class SelectPaperRequest(BaseModel):
    selected: bool = True


class ParsePaperRequest(BaseModel):
    chunk_size: int = Field(default=900, ge=200, le=4000)


class RetrieveChunksRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=20, ge=1, le=100)


class BuildEvidenceRequest(BaseModel):
    max_cards: int = Field(default=120, ge=1, le=300)
    only_selected: bool = True


class GenerateOutlineRequest(BaseModel):
    force: bool = False


class GenerateDraftRequest(BaseModel):
    title: str | None = Field(default=None, max_length=1000)


class ReviewDraftRequest(BaseModel):
    draft_id: str = Field(min_length=36, max_length=36)


class ReviseDraftRequest(BaseModel):
    draft_id: str = Field(min_length=36, max_length=36)


class RunAutoWorkflowRequest(BaseModel):
    query: str | None = Field(default=None, max_length=1000)
    max_results: int = Field(default=25, ge=1, le=100)
    auto_select_limit: int = Field(default=12, ge=1, le=50)
    keep_manual_selection: bool = False
    chunk_size: int = Field(default=900, ge=200, le=4000)
    max_cards: int = Field(default=120, ge=1, le=300)
    draft_title: str | None = Field(default=None, max_length=1000)
    auto_export: bool = True


class ExportResponse(BaseModel):
    task_id: str
    file_name: str
    file_path: str
    media_type: str


class TaskRead(BaseModel):
    task_id: str
    status: str
    progress: int
    current_step: str
    logs: list[str]
    result: dict
    updated_at: datetime | str
