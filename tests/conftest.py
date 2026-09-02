import os
import re
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///var/test-bootstrap.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-thirty-two-characters")
os.environ.setdefault("PASSWORD_KEY_CURRENT", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
os.environ.setdefault("PASSWORD_KEY_CURRENT_VERSION", "2")
os.environ.setdefault("PASSWORD_KEY_PREVIOUS", "ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA=")
os.environ.setdefault("PASSWORD_KEY_PREVIOUS_VERSION", "1")

from text_evolver.config import AppSettings, get_application_settings  # noqa: E402
from text_evolver.db.session import get_db  # noqa: E402
from text_evolver.main import create_app  # noqa: E402


@pytest.fixture
def database(tmp_path: Path):
    database_path = tmp_path / "test.db"
    connection = __import__("sqlite3").connect(database_path)
    migration = Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8")
    connection.executescript(migration)
    connection.close()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", connect_args={"check_same_thread": False})
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def app_settings(tmp_path: Path, database) -> AppSettings:
    settings = AppSettings(
        database_url=str(database.url),
        secret_key="test-secret-key-with-at-least-thirty-two-characters",
        password_key_current="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
        password_key_current_version=2,
        password_key_previous="ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA=",
        password_key_previous_version=1,
        work_root=tmp_path / "work",
        temp_root=tmp_path / "tmp",
        worker_min_free_memory_bytes=0,
    )
    settings.ensure_directories()
    return settings


@pytest.fixture
def client(database, app_settings: AppSettings) -> Generator[TestClient, None, None]:
    application = create_app()

    def database_override():
        with Session(database) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    application.dependency_overrides[get_db] = database_override
    application.dependency_overrides[get_application_settings] = lambda: app_settings
    with TestClient(application) as test_client:
        yield test_client


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


@pytest.fixture
def registered_client(client: TestClient) -> TestClient:
    token = csrf_from(client.get("/register"))
    response = client.post(
        "/register",
        data={"csrf_token": token, "username": "reader", "password": "correct horse", "confirm": "correct horse"},
    )
    assert response.status_code == 200
    return client
