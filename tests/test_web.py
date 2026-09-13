import secrets
import time

import pytest
from conftest import csrf_from
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from text_evolver.db.models import (
    ImageConversion,
    ImageConversionFile,
    ProcessingJob,
    Setting,
    UserAccount,
    UserPassword,
)
from text_evolver.services import job_paths
from text_evolver.web.auth import encrypt_password, verify_password


def create_processing_job(database, app_settings, *, status: str = "queued"):
    with Session(database) as session:
        owner = session.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        assert owner is not None
        setting = Setting(owner_id=owner.id, name="WebSocket processing")
        session.add(setting)
        session.flush()
        job = ProcessingJob(user_id=owner.id, setting_id=setting.id, status=status)
        session.add(job)
        session.commit()
        job_id = job.id
    _, origin, output = job_paths(app_settings, job_id)
    origin.mkdir(parents=True)
    output.mkdir(parents=True)
    (origin / "first.html").write_text("first", encoding="utf-8")
    (origin / "second.html").write_text("second", encoding="utf-8")
    return job_id, output


def test_readiness_checks_database_and_object_storage(client, image_storage):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert image_storage.availability_checks == 1


def test_registration_encrypts_password(client: TestClient, database, app_settings):
    token = csrf_from(client.get("/register"))
    response = client.post(
        "/register",
        data={"csrf_token": token, "username": "alice", "password": "not-plaintext", "confirm": "not-plaintext"},
    )
    assert response.status_code == 200
    assert "My settings" in response.text
    with Session(database) as session:
        user = session.scalar(select(UserAccount).where(UserAccount.username == "alice"))
        assert user is not None
        stored = session.scalar(select(UserPassword).where(UserPassword.user_id == user.id))
        assert stored is not None
        assert stored.encoded_password != b"not-plaintext"
        assert len(stored.nonce) == 12
        assert stored.key_version == app_settings.password_key_current_version
        assert verify_password("not-plaintext", stored, app_settings)
        assert not verify_password("wrong-password", stored, app_settings)


def test_form_validation_error_is_returned_for_popup_without_redirect(client: TestClient):
    token = csrf_from(client.get("/login"))

    response = client.post(
        "/login",
        data={"csrf_token": token, "username": "missing", "password": "wrong-password"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert response.json() == {
        "message": "User with these username and password does not exist",
        "category": "error",
    }


def test_json_form_success_preserves_flash_for_redirect_destination(client: TestClient):
    token = csrf_from(client.get("/register"))

    response = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "popup-user",
            "password": "popup-password",
            "confirm": "popup-password",
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.json()["redirect"].endswith("/my_settings")
    destination = client.get(response.json()["redirect"])
    assert 'id="notification-container"' in destination.text
    assert 'class="notification notification-success"' in destination.text
    assert "You successfully registered account popup-user" in destination.text


def test_login_accepts_password_encrypted_with_previous_key(client: TestClient, database, app_settings):
    previous_settings = app_settings.model_copy(
        update={
            "password_key_current": app_settings.password_key_previous,
            "password_key_current_version": app_settings.password_key_previous_version,
            "password_key_previous": None,
            "password_key_previous_version": None,
        }
    )
    with Session(database) as session:
        user = UserAccount(username="rotating-user")
        session.add(user)
        session.flush()
        encrypted = encrypt_password("old-key-password", previous_settings)
        stored = UserPassword(
            user_id=user.id,
            encoded_password=encrypted.encoded_password,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        session.add(stored)
        session.commit()
        user_id = user.id

    token = csrf_from(client.get("/login"))
    response = client.post(
        "/login",
        data={"csrf_token": token, "username": "rotating-user", "password": "old-key-password"},
    )

    assert response.status_code == 200
    assert "My settings" in response.text
    with Session(database) as session:
        stored = session.scalar(select(UserPassword).where(UserPassword.user_id == user_id))
        assert stored is not None
        assert stored.key_version == app_settings.password_key_previous_version
        assert verify_password("old-key-password", stored, app_settings)


def test_registration_retries_when_password_nonce_collides(
    client: TestClient,
    database,
    app_settings,
    monkeypatch,
):
    colliding_nonce = b"\x00" * 12
    replacement_nonce = b"\x01" * 12
    with Session(database) as session:
        existing_user = UserAccount(username="existing-user")
        session.add(existing_user)
        session.flush()
        session.add(
            UserPassword(
                user_id=existing_user.id,
                encoded_password=b"x" * 16,
                nonce=colliding_nonce,
                key_version=app_settings.password_key_current_version,
            )
        )
        session.commit()

    token = csrf_from(client.get("/register"))
    generated_nonces = iter((colliding_nonce, replacement_nonce))
    original_token_bytes = secrets.token_bytes
    monkeypatch.setattr(
        "text_evolver.web.auth.secrets.token_bytes",
        lambda size: next(generated_nonces) if size == 12 else original_token_bytes(size),
    )
    response = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "nonce-retry-user",
            "password": "encrypted password",
            "confirm": "encrypted password",
        },
    )

    assert response.status_code == 200
    with Session(database) as session:
        user = session.scalar(select(UserAccount).where(UserAccount.username == "nonce-retry-user"))
        assert user is not None
        stored = session.scalar(select(UserPassword).where(UserPassword.user_id == user.id))
        assert stored is not None
        assert stored.nonce == replacement_nonce
        assert verify_password("encrypted password", stored, app_settings)


def test_csrf_is_required_for_mutations(registered_client: TestClient):
    response = registered_client.post("/add_set", data={})
    assert response.status_code == 403


def test_create_setting_and_enqueue_upload(registered_client: TestClient, database):
    page = registered_client.get("/my_settings")
    token = csrf_from(page)
    response = registered_client.post("/add_set", data={"csrf_token": token})
    assert response.status_code == 200
    with Session(database) as session:
        setting = session.scalar(select(Setting))
        assert setting is not None
        setting_id = setting.id

    page = registered_client.get(f"/setting/{setting_id}")
    assert 'name="phrase_regex"' in page.text
    assert 'onchange="Sync_phrase_regex(this)"' in page.text
    assert "data-phrase-regex-fallback" in page.text
    assert 'class="explanation-trigger"' in page.text
    assert 'aria-label="Open explanations"' in page.text
    assert 'aria-expanded="false">?</button>' in page.text
    assert 'class="explanation-backdrop"' in page.text
    assert 'class="explanation-popup text_usual"' in page.text
    assert 'aria-label="Close explanations"' in page.text
    assert 'onclick="Close_exp(event)"' in page.text
    assert (
        "type=\"button\" onclick=\"window.location.href='http://testserver/my_settings'\">Cancel</button>"
        in page.text
    )
    assert (
        f"type=\"button\" onclick=\"window.location.href='http://testserver/setting/{setting_id}'\">Reset</button>"
        in page.text
    )
    token = csrf_from(page)
    response = registered_client.post(
        f"/setting/{setting_id}",
        data={"csrf_token": token, "run": "Run"},
        files={"Process_files": ("book.html", b"<html><body><p>old</p></body></html>", "text/html")},
    )
    assert response.status_code == 200
    assert "Waits" in response.text
    assert 'id="processing-files"' in response.text
    assert "/ws/processing" in response.text
    assert 'id="terminate-control" hidden' in response.text
    with Session(database) as session:
        job = session.scalar(select(ProcessingJob))
        assert job is not None
        assert job.status == "queued"
        job.status = "running"
        session.commit()

    response = registered_client.get("/my_settings")
    assert 'id="terminate-control" >' in response.text


def test_processing_websocket_closes_when_there_is_no_job(registered_client: TestClient):
    with registered_client.websocket_connect("/ws/processing") as websocket:
        assert websocket.receive_json() == {
            "job_id": None,
            "status": "idle",
            "completed_files": 0,
            "total_files": 0,
            "can_download": False,
            "can_terminate": False,
        }
        with pytest.raises(WebSocketDisconnect) as disconnect:
            websocket.receive_json()
    assert disconnect.value.code == 1000


def test_processing_websocket_reports_progress_and_closes_on_completion(
    registered_client: TestClient,
    database,
    app_settings,
):
    job_id, output = create_processing_job(database, app_settings)

    with registered_client.websocket_connect("/ws/processing") as websocket:
        assert websocket.receive_json() == {
            "job_id": job_id,
            "status": "queued",
            "completed_files": 0,
            "total_files": 2,
            "can_download": False,
            "can_terminate": False,
        }

        (output / "first.html").write_text("processed", encoding="utf-8")
        with Session(database) as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            job.status = "running"
            session.commit()
        running = websocket.receive_json()
        assert running["status"] == "running"
        assert running["completed_files"] == 1
        assert running["total_files"] == 2
        assert running["can_terminate"]

        (output / "second.html").write_text("processed", encoding="utf-8")
        with Session(database) as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            job.status = "completed"
            session.commit()
        completed = websocket.receive_json()
        assert completed["status"] == "completed"
        assert completed["completed_files"] == 2
        assert completed["total_files"] == 2
        assert completed["can_download"]
        assert not completed["can_terminate"]
        with pytest.raises(WebSocketDisconnect) as disconnect:
            websocket.receive_json()
    assert disconnect.value.code == 1000


def test_processing_websocket_limits_connections_and_releases_closed_ones(
    registered_client: TestClient,
    database,
    app_settings,
):
    create_processing_job(database, app_settings)
    limiter = registered_client.app.state.websocket_limiter
    limiter.maximum = 1

    with registered_client.websocket_connect("/ws/processing") as first:
        first.receive_json()
        assert limiter.active_count == 1
        with registered_client.websocket_connect("/ws/processing") as second:
            with pytest.raises(WebSocketDisconnect) as disconnect:
                second.receive_json()
        assert disconnect.value.code == 1013

    deadline = time.monotonic() + 1
    while limiter.active_count and time.monotonic() < deadline:
        time.sleep(0.01)
    assert limiter.active_count == 0

    with registered_client.websocket_connect("/ws/processing") as replacement:
        assert replacement.receive_json()["status"] == "queued"


def test_private_setting_requires_owner(client: TestClient, database):
    # Unauthenticated visitors are redirected before any setting data is exposed.
    response = client.get("/setting/999", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")


def test_deleting_setting_deletes_its_stored_images(registered_client, database, image_storage):
    with Session(database) as session:
        owner = session.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        assert owner is not None
        setting = Setting(owner_id=owner.id, name="Delete images")
        session.add(setting)
        session.flush()
        conversion = ImageConversion(
            setting_id=setting.id,
            phrase="trigger",
            separation=1,
            explanation="",
            mutations=False,
        )
        session.add(conversion)
        session.flush()
        object_key = f"users/{owner.id}/settings/{setting.id}/image.png"
        session.add(
            ImageConversionFile(
                image_conversion_id=conversion.id,
                object_key=object_key,
                size_bytes=5,
                position=0,
            )
        )
        session.commit()
        setting_id = setting.id
    image_storage.objects[object_key] = b"image"
    token = csrf_from(registered_client.get("/my_settings"))

    response = registered_client.post(
        f"/delete_set/{setting_id}",
        data={"csrf_token": token},
    )

    assert response.status_code == 204
    assert object_key not in image_storage.objects
    assert object_key in image_storage.deleted
    with Session(database) as session:
        assert session.get(Setting, setting_id) is None


def test_deleting_setting_deletes_all_non_running_jobs(
    registered_client,
    database,
    app_settings,
):
    with Session(database) as session:
        owner = session.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        assert owner is not None
        setting = Setting(owner_id=owner.id, name="Delete jobs")
        session.add(setting)
        session.flush()
        jobs = [
            ProcessingJob(user_id=owner.id, setting_id=setting.id, status=job_status)
            for job_status in ("queued", "completed", "failed", "cancelled")
        ]
        session.add_all(jobs)
        session.commit()
        setting_id = setting.id
        job_ids = [job.id for job in jobs]
    for job_id in job_ids:
        root, _, _ = job_paths(app_settings, job_id)
        root.mkdir(parents=True)
        (root / "file.txt").write_text("job data", encoding="utf-8")
    token = csrf_from(registered_client.get("/my_settings"))

    response = registered_client.post(
        f"/delete_set/{setting_id}",
        data={"csrf_token": token},
    )

    assert response.status_code == 204
    with Session(database) as session:
        assert session.get(Setting, setting_id) is None
        assert list(session.scalars(select(ProcessingJob).where(ProcessingJob.id.in_(job_ids)))) == []
    for job_id in job_ids:
        root, _, _ = job_paths(app_settings, job_id)
        assert not root.exists()


def test_deleting_setting_rejects_running_job(registered_client, database):
    with Session(database) as session:
        owner = session.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        assert owner is not None
        setting = Setting(owner_id=owner.id, name="Running job")
        session.add(setting)
        session.flush()
        job = ProcessingJob(user_id=owner.id, setting_id=setting.id, status="running")
        session.add(job)
        session.commit()
        setting_id = setting.id
        job_id = job.id
    token = csrf_from(registered_client.get("/my_settings"))

    response = registered_client.post(
        f"/delete_set/{setting_id}",
        data={"csrf_token": token},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Setting is used by a running job"}
    with Session(database) as session:
        assert session.get(Setting, setting_id) is not None
        assert session.get(ProcessingJob, job_id) is not None
