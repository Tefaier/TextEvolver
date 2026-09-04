from io import BytesIO
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from text_evolver.config import AppSettings
from text_evolver.image_storage import ImageStorage, ImageStorageChanges, ImageStorageError


class RecordingS3Client:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, object]] = []

    def head_bucket(self, **kwargs):
        self.calls.append(("head", kwargs))

    def upload_fileobj(self, source, bucket, object_key):
        self.calls.append(("upload", (bucket, object_key)))
        self.objects[object_key] = source.read()

    def download_file(self, bucket, object_key, destination):
        self.calls.append(("download", (bucket, object_key)))
        Path(destination).write_bytes(self.objects[object_key])

    def copy_object(self, **kwargs):
        self.calls.append(("copy", kwargs))
        self.objects[kwargs["Key"]] = self.objects[kwargs["CopySource"]["Key"]]

    def delete_objects(self, **kwargs):
        self.calls.append(("delete", kwargs))
        for value in kwargs["Delete"]["Objects"]:
            self.objects.pop(value["Key"], None)
        return {}


def test_storage_upload_download_copy_delete_and_health(tmp_path: Path):
    client = RecordingS3Client()
    storage = ImageStorage(client, "images")
    source = BytesIO(b"image-data")
    source.read()

    storage.check_available()
    storage.upload(source, "source.png")
    storage.copy("source.png", "copy.png")
    destination = tmp_path / "nested" / "download.png"
    storage.download("copy.png", destination)
    storage.delete(["source.png", "copy.png", "copy.png"])

    assert destination.read_bytes() == b"image-data"
    assert client.objects == {}
    assert [name for name, _ in client.calls] == ["head", "upload", "copy", "download", "delete"]


def test_storage_wraps_client_errors_without_credentials():
    class FailingClient(RecordingS3Client):
        def head_bucket(self, **kwargs):
            raise ClientError({"Error": {"Code": "403", "Message": "secret-value"}}, "HeadBucket")

    storage = ImageStorage(FailingClient(), "images")

    with pytest.raises(ImageStorageError, match="Unable to check object storage") as error:
        storage.check_available()

    assert "secret-value" not in str(error.value)


def test_storage_changes_delete_only_the_correct_side_of_transaction():
    client = RecordingS3Client()
    client.objects = {"created": b"new", "obsolete": b"old"}
    storage = ImageStorage(client, "images")
    rolled_back = ImageStorageChanges(storage)
    rolled_back.created_object("created")
    rolled_back.obsolete_object("obsolete")

    rolled_back.database_rolled_back()

    assert client.objects == {"obsolete": b"old"}

    client.objects["created"] = b"new"
    committed = ImageStorageChanges(storage)
    committed.created_object("created")
    committed.obsolete_object("obsolete")
    committed.database_committed()

    assert client.objects == {"created": b"new"}


def test_object_keys_are_scoped_unique_and_keep_safe_suffixes():
    first = ImageStorage.object_key(7, 11, "photo.PNG")
    second = ImageStorage.object_key(7, 11, "photo.PNG")

    assert first.startswith("users/7/settings/11/")
    assert first.endswith(".png")
    assert first != second
    assert ImageStorage.object_key(7, 11, "unsafe.very-long-extension").count(".") == 0


@pytest.mark.parametrize("endpoint", ["minio:9000", "ftp://minio", "http://", "http:///missing-host"])
def test_s3_endpoint_requires_an_http_url(app_settings: AppSettings, endpoint: str):
    values = app_settings.model_dump()
    values["s3_endpoint_url"] = endpoint

    with pytest.raises(PydanticValidationError):
        AppSettings(**values)


@pytest.mark.parametrize("bucket", ["UPPERCASE", "two..dots", "-leading", "bad_underscore"])
def test_s3_bucket_requires_a_minio_compatible_name(app_settings: AppSettings, bucket: str):
    values = app_settings.model_dump()
    values["s3_bucket"] = bucket

    with pytest.raises(PydanticValidationError):
        AppSettings(**values)
