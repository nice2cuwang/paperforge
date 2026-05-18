from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.api.routes.workflow as workflow_routes
from app.database import Base, get_db
from app.main import app


@pytest.fixture(scope="session")
def test_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_dir = tmp_path_factory.mktemp("paperforge-test-db")
    return db_dir / "test.sqlite3"


@pytest.fixture(scope="session")
def test_engine(test_db_path: Path):
    url = f"sqlite:///{test_db_path.as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    return engine


@pytest.fixture(scope="session")
def test_session_factory(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def reset_db(test_engine) -> None:
    # Important: reset only the isolated test DB, never the runtime DB.
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)


@pytest.fixture
def client(test_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    previous_session_local = workflow_routes.SessionLocal
    workflow_routes.SessionLocal = test_session_factory
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        workflow_routes.SessionLocal = previous_session_local
        app.dependency_overrides.clear()
