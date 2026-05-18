import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api import (
    chunks_router,
    drafts_router,
    evidence_router,
    exports_router,
    llm_router,
    papers_router,
    projects_router,
    reviews_router,
    tasks_router,
    workflow_router,
)
from app.database import Base, engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Only auto-create tables in dev; production must use Alembic migrations.
    if os.getenv("ENV", "dev").lower() in ("dev", "development"):
        Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="PaperForge API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(papers_router)
app.include_router(chunks_router)
app.include_router(evidence_router)
app.include_router(drafts_router)
app.include_router(reviews_router)
app.include_router(workflow_router)
app.include_router(exports_router)
app.include_router(tasks_router)
app.include_router(llm_router)


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"
