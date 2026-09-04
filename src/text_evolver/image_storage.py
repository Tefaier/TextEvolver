from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import boto3
from boto3.exceptions import Boto3Error
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from text_evolver.config import AppSettings, get_application_settings


class ImageStorageError(RuntimeError):
    """A user-image object-store operation failed."""


class ImageStorage:
    def __init__(self, client: BaseClient, bucket: str):
        self._client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ImageStorage:
        addressing_style = "path" if settings.s3_force_path_style else "auto"
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=Config(
                s3={"addressing_style": addressing_style},
                retries={"mode": "standard", "max_attempts": 4},
            ),
        )
        return cls(client, settings.s3_bucket)

    @staticmethod
    def object_key(user_id: int, setting_id: int, filename: str | None = None) -> str:
        suffix = Path(filename or "").suffix.lower()
        if len(suffix) > 12 or not suffix[1:].isalnum():
            suffix = ""
        return f"users/{user_id}/settings/{setting_id}/{uuid4().hex}{suffix}"

    def check_available(self) -> None:
        self._call("check object storage", self._client.head_bucket, Bucket=self.bucket)

    def upload(self, source: BinaryIO, object_key: str) -> None:
        source.seek(0)
        self._call(
            "upload image",
            self._client.upload_fileobj,
            source,
            self.bucket,
            object_key,
        )

    def download(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._call(
            "download image",
            self._client.download_file,
            self.bucket,
            object_key,
            str(destination),
        )

    def copy(self, source_key: str, destination_key: str) -> None:
        self._call(
            "copy image",
            self._client.copy_object,
            Bucket=self.bucket,
            Key=destination_key,
            CopySource={"Bucket": self.bucket, "Key": source_key},
        )

    def delete(self, object_keys: Iterable[str]) -> None:
        keys = tuple(dict.fromkeys(object_keys))
        for offset in range(0, len(keys), 100):
            batch = keys[offset : offset + 100]
            response = self._call(
                "delete images",
                self._client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            errors = response.get("Errors", [])
            if errors:
                raise ImageStorageError(f"Object storage could not delete {len(errors)} image(s)")

    @staticmethod
    def _call(operation: str, function: Callable[..., Any], *args: object, **kwargs: object) -> Any:
        try:
            return function(*args, **kwargs)
        except (Boto3Error, BotoCoreError, ClientError, OSError) as exc:
            raise ImageStorageError(f"Unable to {operation}") from exc


class ImageStorageChanges:
    """Tracks non-transactional object changes around a database transaction."""

    def __init__(self, storage: ImageStorage):
        self.storage = storage
        self.created: list[str] = []
        self.obsolete: list[str] = []

    def created_object(self, object_key: str) -> None:
        self.created.append(object_key)

    def obsolete_object(self, object_key: str) -> None:
        self.obsolete.append(object_key)

    def database_committed(self) -> None:
        self.storage.delete(self.obsolete)
        self.created.clear()
        self.obsolete.clear()

    def database_rolled_back(self) -> None:
        self.storage.delete(self.created)
        self.created.clear()
        self.obsolete.clear()


@lru_cache
def get_image_storage() -> ImageStorage:
    return ImageStorage.from_settings(get_application_settings())
