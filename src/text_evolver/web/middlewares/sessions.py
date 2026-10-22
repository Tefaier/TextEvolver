from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection
from starlette.responses import Response

from text_evolver.db.models import BrowserSession
from text_evolver.db.session import get_engine
from text_evolver.web import utils
from text_evolver.web.sessions import COOKIE, retrieve_current_session


@dataclass
class SessionState:
    token_hash: str | None = None
    token: str | None = None
    user_id: UUID | None = None
    expires_at: dt.datetime | None = None
    authenticated_at: dt.datetime | None = None
    method: str | None = None
    set_cookie: bool = False
    clear_cookie: bool = False

    def clear(self) -> None:
        self.token_hash = None
        self.token = None
        self.user_id = None
        self.expires_at = None
        self.authenticated_at = None
        self.method = None
        self.clear_cookie = True
        self.set_cookie = False


class DatabaseSessionMiddleware:
    def __init__(self, app, cookie_secure: bool = False):
        self.app = app
        self.cookie_secure = cookie_secure

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or scope.get("path", "").startswith("/static/"):
            return await self.app(scope, receive, send)
        factory = getattr(scope["app"].state, "session_factory", None)
        db = factory() if factory else Session(get_engine())
        item = SessionState()
        scope["db"] = db
        scope["session_state"] = item
        connection = HTTPConnection(scope)
        token = connection.cookies.get(COOKIE)
        try:
            if token and len(token) <= 128:
                row = db.scalar(
                    select(BrowserSession).where(
                        BrowserSession.token_hash == utils.digest(token),
                        BrowserSession.revoked_at.is_(None),
                        BrowserSession.expires_at > utils.utcnow(),
                    )
                )
                if row:
                    item.token_hash = row.token_hash
                    item.token = token
                    item.user_id = row.user_id
                    item.expires_at = row.expires_at
                    item.authenticated_at = row.authenticated_at
                    item.method = row.authentication_method
                else:
                    item.clear_cookie = True
            elif token:
                item.clear_cookie = True
            db.rollback()

            async def send_response(message):
                if message["type"] == "http.response.start":
                    message["headers"].extend([(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")])
                    if item.set_cookie and retrieve_current_session(db, item) is None:
                        item.set_cookie = False
                    db.commit()
                    cookie = Response()
                    if item.set_cookie:
                        remaining = max(0, math.ceil((utils.utc(item.expires_at) - utils.utcnow()).total_seconds()))
                        cookie.set_cookie(
                            COOKIE,
                            item.token,
                            max_age=remaining,
                            expires=utils.utc(item.expires_at),
                            httponly=True,
                            secure=self.cookie_secure,
                            samesite="lax",
                            path="/",
                        )
                    elif item.clear_cookie:
                        cookie.delete_cookie(COOKIE, httponly=True, secure=self.cookie_secure, samesite="lax")
                    message["headers"].extend((k, v) for k, v in cookie.raw_headers if k == b"set-cookie")
                await send(message)

            await self.app(scope, receive, send_response)
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
