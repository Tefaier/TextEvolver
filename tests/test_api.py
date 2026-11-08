import datetime as dt
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from zipfile import ZipFile

import pytest
from conftest import csrf_from
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from text_evolver.api.tokens import issue_token
from text_evolver.db.models import (
    ApiToken,
    BrowserSession,
    ProcessingJob,
    Setting,
    SettingImageDeletion,
    UserAccount,
)
from text_evolver.image_storage import ImageStorageError
from text_evolver.services import job_paths
from text_evolver.web import sessions, utils

API = "/api/v1"


def token_form(client, permission="read_write", action="create"):
    csrf = csrf_from(client.get("/account/settings"))
    return client.post(f"/account/tokens/{permission}/{action}", data={"csrf_token": csrf},
                       follow_redirects=False)


def create_token(client, permission="read_write"):
    response = token_form(client, permission)
    assert response.status_code == 200, response.text
    found = re.search(r'id="new-api-token">([^<]+)', response.text)
    assert found
    return found.group(1)


def authorization(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_headers(registered_client):
    return authorization(create_token(registered_client))


def create_setting(client, headers, **fields):
    response = client.post(API + "/settings", headers=headers, json={"name": "Book", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def completed_task(database, settings, *, user_id=None, setting_id=None):
    with Session(database) as db:
        if user_id is None:
            user_id = db.scalar(select(UserAccount.id).where(UserAccount.username == "reader"))
        job = ProcessingJob(user_id=user_id, setting_id=setting_id, status="completed")
        db.add(job)
        db.commit()
        job_id = job.id
    output = job_paths(settings, job_id)[2]
    output.mkdir(parents=True)
    (output / "book.html").write_bytes(b"processed")
    return job_id


def test_token_creation_rotation_revocation_and_one_time_display(registered_client, database):
    client = registered_client
    read = create_token(client, "read")
    write = create_token(client)
    assert read != write
    assert len(read) == 46
    assert token_form(client, "read").status_code == 409
    page = client.get("/account/settings")
    assert read not in page.text and write not in page.text
    assert all(read not in value and write not in value for value in client.cookies.values())
    with Session(database) as db:
        rows = list(db.scalars(select(ApiToken)))
        assert len(rows) == 2
        assert {row.token_hash for row in rows} == {utils.digest(read), utils.digest(write)}
    response = token_form(client, "read", "rotate")
    rotated = re.search(r'id="new-api-token">([^<]+)', response.text).group(1)
    assert client.get(API + "/settings", headers=authorization(read)).status_code == 401
    assert client.get(API + "/settings", headers=authorization(rotated)).status_code == 200
    assert token_form(client, "read", "delete").status_code == 303
    assert client.get(API + "/settings", headers=authorization(rotated)).status_code == 401
    assert client.get(API + "/settings", headers=authorization(write)).status_code == 200


def test_token_forms_require_csrf_and_fresh_authentication(registered_client, database):
    client = registered_client
    assert client.post("/account/tokens/read/create").status_code == 403
    with Session(database) as db:
        browser = db.get(BrowserSession, utils.digest(client.cookies.get(sessions.COOKIE)))
        browser.authenticated_at = utils.utcnow() - dt.timedelta(minutes=5)
        db.commit()
    response = token_form(client, "read")
    assert response.status_code == 303
    assert response.headers["location"] == "/account/reauthenticate"
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(ApiToken)) == 0


def test_password_changes_revoke_both_tokens_but_logout_does_not(registered_client):
    client = registered_client
    read = create_token(client, "read")
    write = create_token(client)
    csrf = csrf_from(client.get("/account/settings"))
    response = client.post("/account/settings", data={"csrf_token": csrf, "action": "password",
                           "current_password": "correct horse", "password": "replacement password",
                           "confirm": "replacement password"})
    assert response.status_code == 200
    assert client.get(API + "/settings", headers=authorization(read)).status_code == 401
    assert client.get(API + "/settings", headers=authorization(write)).status_code == 401
    read = create_token(client, "read")
    client.post("/logout", data={"csrf_token": csrf_from(client.get("/account/settings"))})
    assert client.get(API + "/settings", headers=authorization(read)).status_code == 200


def test_token_creation_serializes_and_database_enforces_scope_limit(registered_client, database):
    with Session(database) as db:
        user_id = db.scalar(select(UserAccount.id))
    barrier = threading.Barrier(2)

    def create():
        with Session(database) as db:
            barrier.wait(timeout=10)
            try:
                issue_token(db, user_id, "read")
                db.commit()
                return 201
            except HTTPException as error:
                db.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [201, 409]
    with Session(database) as db:
        db.add(ApiToken(user_id=user_id, permission="read", token_hash="duplicate", issued_at=utils.utcnow()))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.execute(delete(UserAccount).where(UserAccount.id == user_id))
        db.commit()
        assert db.scalar(select(func.count()).select_from(ApiToken)) == 0


def test_api_is_separate_from_browser_middleware_and_documents_bearer(registered_client, api_headers, monkeypatch):
    from text_evolver.web.middlewares import csrf, locale
    from text_evolver.web.middlewares import sessions as middleware_sessions

    def unexpected(*args, **kwargs):
        raise AssertionError("API traversed browser middleware")

    monkeypatch.setattr(csrf.CSRFMiddleware, "__call__", unexpected)
    monkeypatch.setattr(locale.LocaleMiddleware, "__call__", unexpected)
    monkeypatch.setattr(middleware_sessions.DatabaseSessionMiddleware, "__call__", unexpected)
    response = registered_client.post(API + "/settings", headers=api_headers, json={"name": "Created"})
    assert response.status_code == 201
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert registered_client.get("/health/live").status_code == 200
    assert registered_client.get("/health/ready").status_code == 200
    assert registered_client.get("/static/notifications.js").status_code == 200
    assert registered_client.get(API + "/docs").status_code == 200
    specification = registered_client.get(API + "/openapi.json").json()
    assert specification["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert specification["paths"]["/tasks"]["post"]["security"] == [{"HTTPBearer": []}]


@pytest.mark.parametrize("header", [None, "Basic password", "Bearer unknown", "Bearer " + "a" * 129])
def test_api_rejects_cookies_and_invalid_tokens(registered_client, header):
    headers = {} if header is None else {"Authorization": header}
    response = registered_client.get(API + "/settings", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "set-cookie" not in response.headers
    assert "redirect" not in response.json()


def test_read_scope_rejects_every_write_route(registered_client):
    headers = authorization(create_token(registered_client, "read"))
    assert registered_client.get(API + "/settings", headers=headers).status_code == 200
    operations = [
        ("POST", "/settings", {}), ("PATCH", "/settings/1", {}), ("DELETE", "/settings/1", None),
        ("POST", "/settings/1/copy", None), ("DELETE", "/settings/1/images/1", None),
        ("POST", "/tasks/1/cancel", None),
    ]
    for operation in operations:
        response = registered_client.request(operation[0], API + operation[1], headers=headers, json=operation[2])
        assert response.status_code == 403, response.text
    for method in ["POST", "PUT"]:
        path = "/settings/1/images" + ("/1" if method == "PUT" else "")
        response = registered_client.request(method, API + path, headers=headers,
                    data={"metadata": '{"phrase":"picture"}'}, files={"files": ("a.png", b"image")})
        assert response.status_code == 403
    response = registered_client.post(API + "/tasks", headers=headers, data={"setting_id": 1},
                                       files={"files": ("a.html", b"document")})
    assert response.status_code == 403


def test_setting_roundtrip_search_ownership_limits_and_deletion(registered_client, api_headers, database):
    client = registered_client
    setting = create_setting(client, api_headers, public=True,
        units=[{"phrase_from": "inch", "phrase_to": "cm", "conversion": 2.54}],
        phrases=[{"phrase_from": "old", "phrase_to": "new", "direct": True}],
        fandoms=[{"name": "Pokemons", "active": True, "separation": 2}])
    path = API + f"/settings/{setting['id']}"
    response = client.patch(path, headers=api_headers, json={"name": "Renamed", "units": []})
    assert response.status_code == 200
    assert response.json()["units"] == []
    assert response.json()["phrases"] == setting["phrases"]
    assert response.json()["fandoms"] == setting["fandoms"]
    assert client.get(API + "/settings?q=Renamed&page_size=1", headers=api_headers).json()["total"] == 1
    assert client.get(API + "/settings?page=0", headers=api_headers).status_code == 422
    assert client.get(API + "/settings?page_size=201", headers=api_headers).status_code == 422
    assert client.patch(path, headers=api_headers, json={"name": None}).status_code == 422
    assert client.patch(path, headers=api_headers, json={"name": " "}).status_code == 422
    assert client.patch(path, headers=api_headers, json={"name": "a" * 65}).status_code == 422
    assert client.patch(path, headers=api_headers, json={"units": [
        {"phrase_from": "x" * 129, "phrase_to": "x", "conversion": 1}]}).status_code == 422
    assert client.patch(path, headers=api_headers, json={"phrases": [
        {"phrase_from": "[", "phrase_to": "x", "direct": True, "regex": True}]}).status_code == 422
    assert client.patch(path, headers=api_headers, json={"phrases": [
        {"phrase_from": "x", "phrase_to": "y"}] * 101}).status_code == 422
    with Session(database) as db:
        other = UserAccount(id=uuid4(), username="other")
        db.add(other)
        db.flush()
        public = Setting(owner_id=other.id, name="Visible", public=True)
        private = Setting(owner_id=other.id, name="Hidden")
        db.add(public)
        db.add(private)
        db.flush()
        public_id = public.id
        private_id = private.id
        user = db.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        user.setting_limit = 1
        db.commit()
    assert client.get(API + f"/settings/{private_id}", headers=api_headers).status_code == 404
    assert client.get(API + f"/settings/{public_id}", headers=api_headers).status_code == 200
    assert client.patch(API + f"/settings/{public_id}", headers=api_headers, json={}).status_code == 403
    assert client.get(API + "/settings", headers=api_headers).json()["total"] == 1
    assert client.get(API + "/settings?scope=accessible", headers=api_headers).json()["total"] == 2
    assert client.post(API + "/settings", headers=api_headers, json={}).status_code == 409
    assert client.post(API + f"/settings/{public_id}/copy", headers=api_headers).status_code == 409
    assert client.delete(path, headers=api_headers).status_code == 202
    assert client.patch(path, headers=api_headers, json={}).status_code == 409
    assert client.post(API + "/tasks", headers=api_headers, data={"setting_id": setting["id"]},
                       files={"files": ("book.html", b"document")}).status_code == 409


def test_image_rules_retain_replace_copy_delete_and_hide_keys(registered_client, api_headers, database, image_storage):
    client = registered_client
    setting = create_setting(client, api_headers)
    path = API + f"/settings/{setting['id']}/images"
    response = client.post(path, headers=api_headers, data={"metadata": '{"phrase":"cat"}'},
                           files=[("files", ("a.png", b"first")), ("files", ("b.png", b"second"))])
    assert response.status_code == 201, response.text
    image = response.json()
    assert [file["position"] for file in image["files"]] == [0, 1]
    assert [file["size_bytes"] for file in image["files"]] == [5, 6]
    assert "object_key" not in response.text and "users/" not in response.text
    file_path = path + f"/{image['id']}/files/{image['files'][0]['id']}"
    assert client.get(file_path, headers=api_headers).content == b"first"
    response = client.put(path + f"/{image['id']}", headers=api_headers,
                           data={"metadata": '{"phrase":"dog"}'})
    assert response.status_code == 200
    assert response.json()["files"] == image["files"]
    response = client.put(path + f"/{image['id']}", headers=api_headers,
                           data={"metadata": '{"phrase":"dog"}'}, files={"files": ("c.png", b"third")})
    assert response.status_code == 200
    assert len(response.json()["files"]) == 1
    assert len(image_storage.objects) == 1
    with Session(database) as db:
        source = db.get(Setting, setting["id"])
        other = UserAccount(id=uuid4(), username="source_owner")
        db.add(other)
        db.flush()
        source.owner_id = other.id
        source.public = True
        db.commit()
    response = client.post(API + f"/settings/{setting['id']}/copy", headers=api_headers)
    assert response.status_code == 201, response.text
    assert len(image_storage.objects) == 2
    copied = response.json()
    copied_path = API + f"/settings/{copied['id']}/images/{copied['images'][0]['id']}"
    assert client.delete(copied_path, headers=api_headers).status_code == 204
    assert len(image_storage.objects) == 1
    assert client.get(file_path, headers=api_headers).status_code == 404
    assert client.get(path + f"/{image['id']}/files/{image['files'][1]['id']}", headers=api_headers).status_code == 404


def test_image_storage_failure_rolls_back_and_cleanup_is_durable(registered_client, api_headers,
                                                                 database, image_storage, monkeypatch):
    client = registered_client
    setting = create_setting(client, api_headers)
    path = API + f"/settings/{setting['id']}/images"
    first = client.post(path, headers=api_headers, data={"metadata": '{"phrase":"cat"}'},
                         files={"files": ("a.png", b"first")}).json()
    original_upload = image_storage.upload
    calls = []

    def fail_second(source, key):
        calls.append(key)
        if len(calls) == 2:
            raise ImageStorageError("Unavailable")
        original_upload(source, key)

    monkeypatch.setattr(image_storage, "upload", fail_second)
    response = client.post(path, headers=api_headers, data={"metadata": '{"phrase":"broken"}'},
                             files=[("files", ("a.png", b"one")), ("files", ("b.png", b"two"))])
    assert response.status_code == 422
    assert len(image_storage.objects) == 1
    assert len(client.get(API + f"/settings/{setting['id']}", headers=api_headers).json()["images"]) == 1
    monkeypatch.setattr(image_storage, "upload", original_upload)

    def unavailable(keys):
        raise ImageStorageError("Unavailable")

    monkeypatch.setattr(image_storage, "delete", unavailable)
    response = client.delete(path + f"/{first['id']}", headers=api_headers)
    assert response.status_code == 204
    with Session(database) as db:
        assert len(list(db.scalars(select(SettingImageDeletion)))) == 1


def test_tasks_submit_poll_cancel_and_edit_conflicts(registered_client, api_headers, database, app_settings):
    client = registered_client
    setting = create_setting(client, api_headers)
    path = API + "/tasks"
    response = client.post(path, headers=api_headers, data={"setting_id": setting["id"]},
                           files={"files": ("../book.html", b"document")})
    assert response.status_code == 202, response.text
    task = response.json()
    assert response.headers["location"].endswith(f"/api/v1/tasks/{task['id']}")
    assert task["status"] == "queued" and task["total_files"] == 1
    assert (job_paths(app_settings, task["id"])[1] / "book.html").exists()
    task_path = path + f"/{task['id']}"
    assert client.get(task_path, headers=api_headers).json() == task
    assert client.get(path, headers=api_headers).json()["items"] == [task]
    assert client.patch(API + f"/settings/{setting['id']}", headers=api_headers, json={}).status_code == 409
    assert client.post(task_path + "/download", headers=api_headers).status_code == 409
    assert client.post(path, headers=api_headers, data={"setting_id": setting["id"]},
                       files={"files": ("book.html", b"document")}).status_code == 409
    assert client.post(task_path + "/cancel", headers=api_headers).json()["status"] == "cancelled"
    assert client.post(task_path + "/cancel", headers=api_headers).status_code == 409
    response = client.post(path, headers=api_headers, data={"setting_id": setting["id"]},
                           files={"files": ("book.html", b"document")})
    assert response.status_code == 202
    task_id = response.json()["id"]
    with Session(database) as db:
        job = db.get(ProcessingJob, task_id)
        job.status = "running"
        db.commit()
    assert client.post(path + f"/{task_id}/cancel", headers=api_headers).json()["cancellation_requested"]


def test_read_token_download_consumes_and_allows_next_task(registered_client, api_headers, database, app_settings):
    client = registered_client
    setting = create_setting(client, api_headers)
    job_id = completed_task(database, app_settings, setting_id=setting["id"])
    read = authorization(create_token(client, "read"))
    path = API + f"/tasks/{job_id}/download"
    response = client.post(API + "/tasks", headers=api_headers, data={"setting_id": setting["id"]},
                           files={"files": ("book.html", b"document")})
    assert response.status_code == 409
    response = client.post(path, headers=read)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("Your files/book.html") == b"processed"
    assert not job_paths(app_settings, job_id)[0].exists()
    assert client.post(path, headers=read).status_code == 404
    assert client.get(API + f"/tasks/{job_id}", headers=read).status_code == 404
    assert client.post(API + "/tasks", headers=api_headers, data={"setting_id": setting["id"]},
                        files={"files": ("book.html", b"document")}).status_code == 202


def test_download_archive_failure_preserves_result(registered_client, api_headers, database, app_settings, monkeypatch):
    from text_evolver import task_service

    job_id = completed_task(database, app_settings)

    def fail(*args, **kwargs):
        raise OSError("Cannot create archive")

    monkeypatch.setattr(task_service.ZipFile, "write", fail)
    with pytest.raises(OSError, match="Cannot create archive"):
        registered_client.post(API + f"/tasks/{job_id}/download", headers=api_headers)
    assert (job_paths(app_settings, job_id)[2] / "book.html").exists()
    with Session(database) as db:
        assert db.get(ProcessingJob, job_id).status == "completed"


def test_concurrent_browser_and_api_download_consume_once(registered_client, api_headers, database, app_settings):
    job_id = completed_task(database, app_settings)
    csrf = csrf_from(registered_client.get("/my_settings"))
    browser_cookie = registered_client.cookies.get(sessions.COOKIE)
    csrf_cookie = registered_client.cookies.get(sessions.CSRF_COOKIE)
    barrier = threading.Barrier(2)

    def browser_download():
        with TestClient(registered_client.app) as client:
            client.cookies.set(sessions.COOKIE, browser_cookie)
            client.cookies.set(sessions.CSRF_COOKIE, csrf_cookie)
            barrier.wait(timeout=10)
            response = client.post("/download_files", data={"csrf_token": csrf}, follow_redirects=False)
            return response.headers.get("content-type") == "application/zip"

    def api_download():
        with TestClient(registered_client.app) as client:
            barrier.wait(timeout=10)
            response = client.post(API + f"/tasks/{job_id}/download", headers=api_headers)
            return response.headers.get("content-type") == "application/zip"

    with ThreadPoolExecutor(max_workers=2) as pool:
        browser = pool.submit(browser_download)
        api = pool.submit(api_download)
        assert sum([browser.result(timeout=15), api.result(timeout=15)]) == 1


def test_other_accounts_tasks_are_hidden(registered_client, api_headers, database, app_settings):
    with Session(database) as db:
        other = UserAccount(id=uuid4(), username="other")
        db.add(other)
        db.commit()
        user_id = other.id
    job_id = completed_task(database, app_settings, user_id=user_id)
    path = API + f"/tasks/{job_id}"
    assert registered_client.get(path, headers=api_headers).status_code == 404
    assert registered_client.post(path + "/cancel", headers=api_headers).status_code == 404
    assert registered_client.post(path + "/download", headers=api_headers).status_code == 404
    assert registered_client.get(API + "/tasks", headers=api_headers).json()["total"] == 0


def test_upload_and_body_limits_leave_no_staged_jobs(registered_client, api_headers, app_settings, database):
    client = registered_client
    setting = create_setting(client, api_headers)
    replace_settings = app_settings.model_copy(update={"upload_limit_bytes": 3})
    client.app.state.settings = replace_settings
    # Pydantic settings remain shared across both mounted apps.
    assert replace_settings.upload_limit_bytes == 3
    response = client.post(API + "/tasks", headers=api_headers, data={"setting_id": setting["id"]},
                           files={"files": ("a.html", b"too large")})
    assert response.status_code == 422
    assert list(app_settings.work_root.iterdir()) == []
    response = client.post(API + "/settings", headers={**api_headers, "Content-Length": str(536_870_913),
                                                       "Accept-Language": "ru"}, content=b"")
    assert response.status_code == 413
    assert response.json()["detail"].startswith("Request body")
    assert "set-cookie" not in response.headers
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0


def test_failed_task_commit_removes_staged_documents(
    registered_client, api_headers, database, app_settings, monkeypatch,
):
    setting = create_setting(registered_client, api_headers)
    original_commit = Session.commit

    def fail_commit(db):
        if db.info.get("staged_job_roots"):
            raise RuntimeError("Simulated commit failure")
        return original_commit(db)

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Simulated commit failure"):
        registered_client.post(API + "/tasks", headers=api_headers, data={"setting_id": setting["id"]},
                                files={"files": ("book.html", b"document")})
    assert list(app_settings.work_root.iterdir()) == []
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0


def test_images_size_budget_counts_retained_and_replaced_files(registered_client, api_headers, monkeypatch):
    from text_evolver.web import validation_utils

    monkeypatch.setattr(validation_utils, "SETTING_IMAGES_MAX_BYTES", 8)
    client = registered_client
    setting = create_setting(client, api_headers)
    path = API + f"/settings/{setting['id']}/images"
    response = client.post(path, headers=api_headers, data={"metadata": '{"phrase":"cat"}'},
                           files={"files": ("a.png", b"12345")})
    assert response.status_code == 201
    image_id = response.json()["id"]
    assert client.post(path, headers=api_headers, data={"metadata": '{"phrase":"dog"}'},
                       files={"files": ("b.png", b"6789")}).status_code == 422
    response = client.put(path + f"/{image_id}", headers=api_headers, data={"metadata": '{"phrase":"cat"}'},
                           files={"files": ("b.png", b"12345678")})
    assert response.status_code == 200
    assert response.json()["files"][0]["size_bytes"] == 8


def test_api_tokens_have_no_expiry_and_errors_do_not_leak_paths(registered_client, api_headers,
                                                               database, app_settings, monkeypatch):
    with Session(database) as db:
        token = db.scalar(select(ApiToken))
        token.issued_at = utils.utcnow() - dt.timedelta(days=3650)
        db.commit()
    assert registered_client.get(API + "/settings", headers=api_headers).status_code == 200
    job_id = completed_task(database, app_settings)
    # Missing result storage must produce a JSON server error without consuming
    # the row or disclosing its internal directory to API callers.
    (job_paths(app_settings, job_id)[2] / "book.html").unlink()
    job_paths(app_settings, job_id)[2].rmdir()
    with TestClient(registered_client.app, raise_server_exceptions=False) as client:
        response = client.post(API + f"/tasks/{job_id}/download", headers=api_headers)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert str(app_settings.work_root) not in response.text
    with Session(database) as db:
        assert db.get(ProcessingJob, job_id).status == "completed"


def test_rolled_back_image_upload_is_queued_if_storage_cleanup_also_fails(
    registered_client, api_headers, database, image_storage, monkeypatch,
):
    setting = create_setting(registered_client, api_headers)
    original_upload = image_storage.upload

    def failed_upload(source, key):
        original_upload(source, key)
        raise ImageStorageError("Upload did not acknowledge completion")

    def failed_delete(keys):
        raise ImageStorageError("Storage unavailable")

    monkeypatch.setattr(image_storage, "upload", failed_upload)
    monkeypatch.setattr(image_storage, "delete", failed_delete)
    response = registered_client.post(API + f"/settings/{setting['id']}/images", headers=api_headers,
        data={"metadata": '{"phrase":"cat"}'}, files={"files": ("a.png", b"image")})
    assert response.status_code == 422
    with Session(database) as db:
        queued = list(db.scalars(select(SettingImageDeletion.object_key)))
        assert set(queued) == set(image_storage.objects)
        assert len(queued) == 1
    assert registered_client.get(API + f"/settings/{setting['id']}", headers=api_headers).json()["images"] == []
