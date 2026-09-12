import os
import re
from collections.abc import Generator
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///var/test-bootstrap.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-thirty-two-characters")
os.environ.setdefault("PASSWORD_KEY_CURRENT", "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=")
os.environ.setdefault("PASSWORD_KEY_CURRENT_VERSION", "2")
os.environ.setdefault("PASSWORD_KEY_PREVIOUS", "ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA=")
os.environ.setdefault("PASSWORD_KEY_PREVIOUS_VERSION", "1")
os.environ.setdefault("S3_ENDPOINT_URL", "http://localhost:9000")
os.environ.setdefault("S3_BUCKET", "test-images")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("S3_ACCESS_KEY", "test-access-key")
os.environ.setdefault("S3_SECRET_KEY", "test-secret-key")

from text_evolver.config import AppSettings, get_application_settings  # noqa: E402
from text_evolver.db.session import get_db  # noqa: E402
from text_evolver.image_storage import get_image_storage  # noqa: E402
from text_evolver.main import create_app  # noqa: E402


@pytest.fixture
def database(tmp_path: Path):
    database_path = tmp_path / "test.db"
    connection = __import__("sqlite3").connect(database_path)
    migration = Path("migrations/sql/sqlite/V1__initial_schema.sql").read_text(encoding="utf-8")
    connection.executescript(migration)
    connection.close()
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

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
        websocket_update_seconds=0.01,
        websocket_max_connections=1,
    )
    settings.ensure_directories()
    return settings


class FakeImageStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.availability_checks = 0
        self._ids = count(1)

    def object_key(self, user_id: int, setting_id: int, filename: str | None = None) -> str:
        suffix = Path(filename or "").suffix
        return f"users/{user_id}/settings/{setting_id}/image-{next(self._ids)}{suffix}"

    def upload(self, source, object_key: str) -> None:
        source.seek(0)
        self.objects[object_key] = source.read()

    def download(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.objects[object_key])

    def copy(self, source_key: str, destination_key: str) -> None:
        self.objects[destination_key] = self.objects[source_key]

    def delete(self, object_keys) -> None:
        for object_key in dict.fromkeys(object_keys):
            self.deleted.append(object_key)
            self.objects.pop(object_key, None)

    def check_available(self) -> None:
        self.availability_checks += 1


@pytest.fixture
def image_storage() -> FakeImageStorage:
    return FakeImageStorage()


@pytest.fixture
def client(database, app_settings: AppSettings, image_storage: FakeImageStorage) -> Generator[TestClient, None, None]:
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
    application.dependency_overrides[get_image_storage] = lambda: image_storage
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
