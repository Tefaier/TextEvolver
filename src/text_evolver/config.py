from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(min_length=1, validation_alias="DATABASE_URL")
    secret_key: str = Field(min_length=32, validation_alias="SECRET_KEY")
    work_root: Path = Field(default=Path("var/work"), validation_alias="WORK_ROOT")
    temp_root: Path = Field(default=Path("var/tmp"), validation_alias="TEMP_ROOT")
    chrome_binary: str | None = Field(default=None, validation_alias="CHROME_BINARY")
    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    upload_limit_bytes: int = Field(default=26_214_400, ge=1, validation_alias="UPLOAD_LIMIT_BYTES")
    setting_limit_bytes: int = Field(default=536_870_912, ge=1, validation_alias="SETTING_LIMIT_BYTES")
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, validation_alias="WORKER_POLL_SECONDS")
    worker_concurrency: int = Field(default=1, ge=1, le=64, validation_alias="WORKER_CONCURRENCY")
    worker_min_free_memory_bytes: int = Field(
        default=805_306_368, ge=0, validation_alias="WORKER_MIN_FREE_MEMORY_BYTES"
    )
    worker_cancel_poll_seconds: float = Field(default=0.5, ge=0.1, validation_alias="WORKER_CANCEL_POLL_SECONDS")
    job_stale_seconds: int = Field(default=300, ge=30, validation_alias="JOB_STALE_SECONDS")
    results_per_page: int = Field(default=50, ge=1, le=200, validation_alias="RESULTS_PER_PAGE")

    @field_validator("work_root", "temp_root", mode="after")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def ensure_directories(self) -> None:
        for directory in (self.work_root, self.temp_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_application_settings() -> AppSettings:
    return AppSettings()
