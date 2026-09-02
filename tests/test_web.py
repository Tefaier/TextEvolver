from conftest import csrf_from
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from text_evolver.db.models import ProcessingJob, Setting, UserAccount, UserPassword
from text_evolver.web.auth import encrypt_password, verify_password


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


def test_login_rotates_password_encrypted_with_previous_key(client: TestClient, database, app_settings):
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
        old_nonce = stored.nonce

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
        assert stored.key_version == app_settings.password_key_current_version
        assert stored.nonce != old_nonce
        assert verify_password("old-key-password", stored, app_settings)


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
    token = csrf_from(page)
    response = registered_client.post(
        f"/setting/{setting_id}",
        data={"csrf_token": token, "run": "Run"},
        files={"Process_files": ("book.html", b"<html><body><p>old</p></body></html>", "text/html")},
    )
    assert response.status_code == 200
    assert "Waits" in response.text
    assert 'value="Terminate"' not in response.text
    with Session(database) as session:
        job = session.scalar(select(ProcessingJob))
        assert job is not None
        assert job.status == "queued"
        job.status = "running"
        session.commit()

    response = registered_client.get("/my_settings")
    assert 'value="Terminate"' in response.text


def test_private_setting_requires_owner(client: TestClient, database):
    # Unauthenticated visitors are redirected before any setting data is exposed.
    response = client.get("/setting/999", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/login")
