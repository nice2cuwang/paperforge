from app.api.routes.chunks import router as chunks_router
from app.api.routes.drafts import router as drafts_router
from app.api.routes.evidence import router as evidence_router
from app.api.routes.exports import router as exports_router
from app.api.routes.llm_config import router as llm_router
from app.api.routes.papers import router as papers_router
from app.api.routes.projects import router as projects_router
from app.api.routes.reviews import router as reviews_router
from app.api.routes.tasks import router as tasks_router
from app.api.routes.workflow import router as workflow_router

__all__ = [
    "projects_router",
    "papers_router",
    "chunks_router",
    "evidence_router",
    "drafts_router",
    "reviews_router",
    "workflow_router",
    "exports_router",
    "tasks_router",
    "llm_router",
]
