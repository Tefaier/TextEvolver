import asyncio
from collections import deque

import pytest
from conftest import csrf_from
from fastapi import Request

from text_evolver.web.middlewares import body_size
from text_evolver.web.middlewares.body_size import MAX_BODY_BYTES, BodySizeLimitMiddleware


def invoke(chunks, *, headers=(), limit=8, disconnect=False, app=None):
    incoming = deque(
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1 or disconnect}
        for index, chunk in enumerate(chunks)
    )
    if disconnect:
        incoming.append({"type": "http.disconnect"})
    received = []
    sent = []
    called = []

    async def receive():
        message = incoming.popleft()
        received.append(message)
        return message

    async def send(message):
        sent.append(message)

    async def echo(scope, receive, send):
        called.append(True)
        parts = []
        while True:
            message = await receive()
            parts.append(message["body"])
            if not message["more_body"]:
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"".join(parts)})

    scope = {"type": "http", "method": "POST", "headers": list(headers)}
    asyncio.run(BodySizeLimitMiddleware(app or echo, limit)(scope, receive, send))
    return received, sent, called


def test_default_limit_is_512_mib():
    assert BodySizeLimitMiddleware(None).max_body_bytes == MAX_BODY_BYTES == 536_870_912


def test_oversized_content_length_is_rejected_without_reading_body():
    result = invoke([], headers=[(b"content-length", b"9")])
    received = result[0]
    sent = result[1]
    called = result[2]
    assert received == called == []
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("declared", [None, b"0", b"8", b"invalid"])
def test_actual_bytes_enforce_limit_without_trusting_content_length(declared):
    headers = [] if declared is None else [(b"content-length", declared)]
    result = invoke([b"1234", b"5678", b"9", b"unread"], headers=headers)
    received = result[0]
    sent = result[1]
    called = result[2]
    assert len(received) == 3
    assert called == []
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("chunks", [[b""], [b"1234567"], [b"1234", b"5678"], [b"12345678", b""]])
def test_body_at_or_below_limit_reaches_app_unchanged(chunks):
    result = invoke(chunks)
    sent = result[1]
    called = result[2]
    assert called == [True]
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"".join(chunks)


def test_disconnect_does_not_run_handler():
    result = invoke([b"1234"], disconnect=True)
    received = result[0]
    sent = result[1]
    called = result[2]
    assert received[-1]["type"] == "http.disconnect"
    assert sent == called == []


@pytest.mark.parametrize("outcome", ["accepted", "rejected", "disconnected", "exception"])
def test_spooled_file_is_closed_for_every_outcome(monkeypatch, outcome):
    files = []
    original = body_size.SpooledTemporaryFile

    def create_spool(*args, **kwargs):
        # Exercise disk IO without a large fixture.
        kwargs["max_size"] = 2
        file = original(*args, **kwargs)
        files.append(file)
        return file

    async def fail(scope, receive, send):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(body_size, "SpooledTemporaryFile", create_spool)
    chunks = [b"1234", b"56789"] if outcome == "rejected" else [b"1234"]
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="handler failed"):
            invoke(chunks, app=fail)
    else:
        invoke(chunks, disconnect=outcome == "disconnected")
    assert len(files) == 1
    assert files[0]._rolled
    assert files[0].closed


def test_websocket_and_lifespan_are_passed_through():
    scopes = []

    async def app(scope, receive, send):
        scopes.append(scope["type"])

    async def check():
        middleware = BodySizeLimitMiddleware(app)
        for scope_type in ("websocket", "lifespan"):
            await middleware({"type": scope_type}, None, None)

    asyncio.run(check())
    assert scopes == ["websocket", "lifespan"]


def test_application_rejects_large_body_before_csrf_and_localizes_error(client):
    response = client.post("/login", headers={
        "Content-Length": str(MAX_BODY_BYTES + 1), "Accept-Language": "ru",
    })
    assert response.status_code == 413
    assert response.json()["detail"] == "Размер тела запроса превышает ограничение в 512 МБ"


@pytest.mark.parametrize("header_token", [False, True])
def test_body_replay_preserves_multipart_and_csrf(client, header_token):
    async def endpoint(request: Request):
        async with request.form() as form:
            return {"text": form["text"], "file": (await form["file"].read()).decode()}

    client.app.state.web_app.add_api_route("/test-body", endpoint, methods=["POST"])
    token = csrf_from(client.get("/login"))
    headers = {"X-CSRF-Token": token} if header_token else {}
    response = client.post(
        "/test-body", data={"csrf_token": token, "text": "Привет"},
        files={"file": ("image.txt", b"contents")}, headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"text": "Привет", "file": "contents"}
