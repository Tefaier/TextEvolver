import asyncio
import datetime as dt
import secrets
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from text_evolver.db.models import BrowserSession, UserAccount
from text_evolver.web.i18n import locale
from text_evolver.web.middlewares.locale import LocaleMiddleware
from text_evolver.web.middlewares.sessions import SessionState
from text_evolver.web.sessions import COOKIE, LIFETIME
from text_evolver.web.utils import digest, utcnow


def scope_for(language, scope_type="http"):
    return {
        "type": scope_type,
        "path": "/",
        "headers": [(b"accept-language", language.encode())],
        "db": SimpleNamespace(rollback=lambda: None),
        "session_state": SessionState(),
    }


@pytest.mark.parametrize("scope_type", ["http", "websocket"])
@pytest.mark.parametrize("fails", [False, True])
def test_locale_context_is_restored_after_request(scope_type, fails):
    async def app(scope, receive, send):
        assert scope["locale"] == locale.get() == "ru"
        if fails:
            raise RuntimeError("downstream failure")

    async def check():
        token = locale.set("en")
        try:
            request = LocaleMiddleware(app)(scope_for("ru", scope_type), None, None)
            if fails:
                with pytest.raises(RuntimeError, match="downstream failure"):
                    await request
            else:
                await request
            assert locale.get() == "en"
        finally:
            locale.reset(token)

    asyncio.run(check())


def test_concurrent_requests_keep_separate_locales():
    async def app(scope, receive, send):
        await asyncio.sleep(0)
        assert locale.get() == scope["locale"]

    async def check():
        middleware = LocaleMiddleware(app)
        await asyncio.gather(*(middleware(scope_for(language), None, None) for language in ("ru", "en")))

    asyncio.run(check())


def set_account_session(client, database, language, *, expired=False):
    now = utcnow()
    token = secrets.token_urlsafe(32)
    user_id = uuid4()
    created_at = now - LIFETIME - dt.timedelta(seconds=1) if expired else now
    with Session(database) as db:
        db.add(UserAccount(id=user_id, username=f"locale-{user_id.hex}", language=language))
        db.flush()
        db.add(BrowserSession(
            token_hash=digest(token), user_id=user_id, created_at=created_at,
            expires_at=created_at + LIFETIME, authenticated_at=now, authentication_method="password",
        ))
        db.commit()
    client.cookies.set(COOKIE, token, domain="testserver.local", path="/")


@pytest.mark.parametrize("preference,header,expected", [("ru", "en", "ru"), ("en", "ru", "en"), (None, "ru", "ru")])
def test_locale_middleware_reads_account_after_session_middleware(client, database, preference, header, expected):
    set_account_session(client, database, preference)
    response = client.get("/my_settings", headers={"Accept-Language": header})
    assert response.status_code == 200
    assert f'<html lang="{expected}">' in response.text


def test_expired_session_uses_browser_language(client, database):
    set_account_session(client, database, "ru", expired=True)
    response = client.get("/login", headers={"Accept-Language": "en"})
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert client.cookies.get(COOKIE) is None
