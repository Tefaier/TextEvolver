from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(min_length=1, validation_alias="DATABASE_URL")
    secret_key: str = Field(min_length=32, validation_alias="SECRET_KEY")
    work_root: Path = Field(default=Path("var/work"), validation_alias="WORK_ROOT")
    temp_root: Path = Field(default=Path("var/tmp"), validation_alias="TEMP_ROOT")
    seleniumbase_driver_root: Path = Field(
        default=Path("var/seleniumbase"), validation_alias="SELENIUMBASE_DRIVER_ROOT"
    )
    s3_endpoint_url: str = Field(min_length=1, validation_alias="S3_ENDPOINT_URL")
    s3_bucket: str = Field(min_length=3, validation_alias="S3_BUCKET")
    s3_region: str = Field(default="us-east-1", min_length=1, validation_alias="S3_REGION")
    s3_access_key: SecretStr = Field(min_length=3, validation_alias="S3_ACCESS_KEY")
    s3_secret_key: SecretStr = Field(min_length=8, validation_alias="S3_SECRET_KEY")
    s3_force_path_style: bool = Field(default=True, validation_alias="S3_FORCE_PATH_STYLE")
    chrome_binary: str | None = Field(default=None, validation_alias="CHROME_BINARY")
    cookie_secure: bool = Field(default=False, validation_alias="COOKIE_SECURE")
    app_base_url: str = Field(default="http://localhost:8000", validation_alias="APP_BASE_URL")
    webauthn_rp_id: str = Field(default="localhost", validation_alias="WEBAUTHN_RP_ID")
    webauthn_origin: str = Field(default="http://localhost:8000", validation_alias="WEBAUTHN_ORIGIN")
    google_client_id: str = Field(default="", validation_alias="GOOGLE_CLIENT_ID")
    google_client_secret: SecretStr = Field(default=SecretStr(""), validation_alias="GOOGLE_CLIENT_SECRET")
    upload_limit_bytes: int = Field(default=26_214_400, ge=1, validation_alias="UPLOAD_LIMIT_BYTES")
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, validation_alias="WORKER_POLL_SECONDS")
    worker_concurrency: int = Field(default=1, ge=1, le=64, validation_alias="WORKER_CONCURRENCY")
    worker_min_free_memory_bytes: int = Field(
        default=805_306_368, ge=0, validation_alias="WORKER_MIN_FREE_MEMORY_BYTES"
    )
    worker_cancel_poll_seconds: float = Field(default=0.5, ge=0.1, validation_alias="WORKER_CANCEL_POLL_SECONDS")
    job_stale_seconds: int = Field(default=300, ge=30, validation_alias="JOB_STALE_SECONDS")
    results_per_page: int = Field(default=50, ge=1, le=200, validation_alias="RESULTS_PER_PAGE")
    websocket_update_seconds: float = Field(default=5.0, ge=0.1, validation_alias="WEBSOCKET_UPDATE_SECONDS")
    websocket_max_connections: int = Field(default=100, ge=1, le=10_000, validation_alias="WEBSOCKET_MAX_CONNECTIONS")

    @field_validator("work_root", "temp_root", "seleniumbase_driver_root", mode="after")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("s3_endpoint_url", mode="after")
    @classmethod
    def validate_s3_endpoint_url(cls, value: str) -> str:
        endpoint = value.strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("S3_ENDPOINT_URL must be an HTTP URL without query parameters or fragments")
        return endpoint

    @field_validator("app_base_url", "webauthn_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            raise ValueError("Expected an HTTP origin without a path or credentials")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("HTTP is supported only on localhost; use HTTPS for deployment")
        return value.rstrip("/")

    @field_validator("webauthn_rp_id")
    @classmethod
    def validate_rp_id(cls, value: str) -> str:
        if not value or any(c in value for c in "/:@ "):
            raise ValueError("WEBAUTHN_RP_ID must be a hostname without a scheme or port")
        return value

    @field_validator("s3_bucket", mode="after")
    @classmethod
    def validate_s3_bucket(cls, value: str) -> str:
        if (
            len(value) > 63
            or value.lower() != value
            or not value[0].isalnum()
            or not value[-1].isalnum()
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in value)
            or ".." in value
            or ".-" in value
            or "-." in value
        ):
            raise ValueError("S3_BUCKET must be a valid lowercase bucket name")
        return value

    def ensure_directories(self) -> None:
        for directory in (self.work_root, self.temp_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_application_settings() -> AppSettings:
    return AppSettings()
