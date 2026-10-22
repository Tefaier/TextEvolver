import hashlib

import pytest
from conftest import csrf_from
from fastapi import Request, WebSocket


@pytest.mark.parametrize("source", ["missing", "header", "form", "invalid-header-valid-form"])
def test_all_post_routes_require_csrf(client, source):
    called = []

    async def endpoint():
        called.append(True)
        return {"ok": True}

    client.app.add_api_route("/test-csrf", endpoint, methods=["POST"])
    token = csrf_from(client.get("/login"))
    headers = {"X-CSRF-Token": "wrong"} if "header" in source else {}
    data = {"csrf_token": token if source == "invalid-header-valid-form" else "wrong"} if "form" in source else {}
    assert client.post("/test-csrf", data=data, headers=headers).status_code == 403
    assert called == []


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_csrf_validation_only_applies_to_post(client, method):
    client.app.add_api_route("/test-csrf", lambda: {"ok": True}, methods=[method])
    assert client.request(method, "/test-csrf").status_code == 200


def test_header_token_preserves_json_body(client):
    async def endpoint(request: Request):
        return await request.json()

    client.app.add_api_route("/test-csrf", endpoint, methods=["POST"])
    token = csrf_from(client.get("/login"))
    payload = {"text": "Привет", "values": [1, 2, 3]}
    response = client.post("/test-csrf", json=payload, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.json() == payload


@pytest.mark.parametrize("multipart", [False, True])
def test_form_token_preserves_fields_and_uploads(client, multipart):
    async def endpoint(request: Request):
        async with request.form() as form:
            payload = {"text": form["text"], "tags": form.getlist("tags")}
            if "upload" in form:
                payload["file_hash"] = hashlib.sha256(await form["upload"].read()).hexdigest()
                payload["file_name"] = form["upload"].filename
            return payload

    client.app.add_api_route("/test-csrf", endpoint, methods=["POST"])
    token = csrf_from(client.get("/login"))
    contents = bytes(range(256)) * 8192
    files = {"upload": ("document.bin", contents, "application/octet-stream")} if multipart else None
    response = client.post(
        "/test-csrf", data={"csrf_token": token, "text": "Привет", "tags": ["one", "two"]}, files=files,
    )
    assert response.status_code == 200
    payload = {"text": "Привет", "tags": ["one", "two"]}
    if multipart:
        payload.update(file_hash=hashlib.sha256(contents).hexdigest(), file_name="document.bin")
    assert response.json() == payload


def test_malformed_multipart_returns_bad_request(client):
    csrf_from(client.get("/login"))
    response = client.post("/login", content=b"malformed", headers={"Content-Type": "multipart/form-data"})
    assert response.status_code == 400


def test_csrf_error_uses_request_locale(client):
    csrf_from(client.get("/login"))
    response = client.post("/login", headers={"Accept-Language": "ru", "X-CSRF-Token": "wrong"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Недопустимый CSRF-токен"


def test_websocket_does_not_require_csrf(client):
    async def endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json(await websocket.receive_json())
        await websocket.close()

    client.app.add_api_websocket_route("/test-csrf", endpoint)
    with client.websocket_connect("/test-csrf") as websocket:
        websocket.send_json({"ok": True})
        assert websocket.receive_json() == {"ok": True}
