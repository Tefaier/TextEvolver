import base64
import binascii
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(min_length=1, validation_alias="DATABASE_URL")
    secret_key: str = Field(min_length=32, validation_alias="SECRET_KEY")
    password_key_current: SecretStr = Field(validation_alias="PASSWORD_KEY_CURRENT")
    password_key_current_version: int = Field(ge=1, validation_alias="PASSWORD_KEY_CURRENT_VERSION")
    password_key_previous: SecretStr | None = Field(default=None, validation_alias="PASSWORD_KEY_PREVIOUS")
    password_key_previous_version: int | None = Field(
        default=None, ge=1, validation_alias="PASSWORD_KEY_PREVIOUS_VERSION"
    )
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
    websocket_update_seconds: float = Field(default=5.0, ge=0.1, validation_alias="WEBSOCKET_UPDATE_SECONDS")
    websocket_max_connections: int = Field(default=100, ge=1, le=10_000, validation_alias="WEBSOCKET_MAX_CONNECTIONS")

    @field_validator("work_root", "temp_root", "seleniumbase_driver_root", mode="after")
    @classmethod
    def resolve_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("password_key_previous", "password_key_previous_version", mode="before")
    @classmethod
    def empty_previous_key_values_are_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("s3_endpoint_url", mode="after")
    @classmethod
    def validate_s3_endpoint_url(cls, value: str) -> str:
        endpoint = value.strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("S3_ENDPOINT_URL must be an HTTP URL without query parameters or fragments")
        return endpoint

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

    @model_validator(mode="after")
    def validate_password_keys(self) -> "AppSettings":
        previous_values = (self.password_key_previous, self.password_key_previous_version)
        if (previous_values[0] is None) != (previous_values[1] is None):
            raise ValueError("PASSWORD_KEY_PREVIOUS and PASSWORD_KEY_PREVIOUS_VERSION must be set together")
        if self.password_key_previous_version == self.password_key_current_version:
            raise ValueError("Current and previous password key versions must differ")
        self._decode_password_key(self.password_key_current, "PASSWORD_KEY_CURRENT")
        if self.password_key_previous is not None:
            self._decode_password_key(self.password_key_previous, "PASSWORD_KEY_PREVIOUS")
        return self

    @staticmethod
    def _decode_password_key(value: SecretStr, name: str) -> bytes:
        try:
            decoded = base64.b64decode(value.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{name} must be valid Base64") from exc
        if len(decoded) != 32:
            raise ValueError(f"{name} must decode to exactly 32 bytes for AES-256-GCM")
        return decoded

    @property
    def current_password_key(self) -> bytes:
        return self._decode_password_key(self.password_key_current, "PASSWORD_KEY_CURRENT")

    def password_key_for_version(self, version: int) -> bytes | None:
        if version == self.password_key_current_version:
            return self.current_password_key
        if version == self.password_key_previous_version and self.password_key_previous is not None:
            return self._decode_password_key(self.password_key_previous, "PASSWORD_KEY_PREVIOUS")
        return None

    def ensure_directories(self) -> None:
        for directory in (self.work_root, self.temp_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_application_settings() -> AppSettings:
    return AppSettings()
