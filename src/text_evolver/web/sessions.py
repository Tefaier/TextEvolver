from __future__ import annotations

import datetime as dt
import secrets
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from text_evolver.db.models import AuthFlow, BrowserSession, BrowserSessionFlash
from text_evolver.web import utils

if TYPE_CHECKING:
    from text_evolver.web.middlewares.sessions import SessionState


COOKIE = "text_evolver_session"
CSRF_COOKIE = "text_evolver_csrf"
LIFETIME = dt.timedelta(days=7)
FRESHNESS = dt.timedelta(minutes=5)


def seconds_remaining(item: SessionState) -> float:
    return max(0.001, (utils.utc(item.expires_at) - utils.utcnow()).total_seconds())


def state(connection: HTTPConnection) -> SessionState:
    return connection.scope["session_state"]


def retrieve_current_session(db: Session, item: SessionState, *, lock: bool = False) -> BrowserSession | None:
    if item.token_hash is None or item.token is None or utils.digest(item.token) != item.token_hash:
        return None
    query = (
        select(BrowserSession)
        .where(
            BrowserSession.token_hash == item.token_hash,
            BrowserSession.revoked_at.is_(None),
            BrowserSession.expires_at > utils.utcnow(),
        )
        .execution_options(populate_existing=True)
    )
    return db.scalar(query.with_for_update() if lock else query)


def get_or_create_session(connection: HTTPConnection, db: Session) -> SessionState:
    item = state(connection)
    if item.token_hash is None:
        token = secrets.token_urlsafe(32)
        now = utils.utcnow()
        row = BrowserSession(token_hash=utils.digest(token), created_at=now, expires_at=now + LIFETIME)
        db.add(row)
        db.flush()
        item.token_hash = row.token_hash
        item.token = token
        item.expires_at = row.expires_at
        item.set_cookie = True
    return item


def revoke_user_sessions(db: Session, user_id: UUID, *, except_token_hash: str | None = None) -> None:
    query = update(BrowserSession).where(BrowserSession.user_id == user_id, BrowserSession.revoked_at.is_(None))
    if except_token_hash is not None:
        query = query.where(BrowserSession.token_hash != except_token_hash)
    db.execute(query.values(revoked_at=utils.utcnow()))


def reset_session(
    connection: HTTPConnection, db: Session, user_id: UUID, method: str, *, recreate: bool = True,
) -> None:
    """Create a new seven-day session, or rotate an active session preserving its deadline."""
    item = state(connection)
    now = utils.utcnow()
    if recreate:
        if item.token_hash is not None:
            db.execute(
                update(BrowserSession).where(BrowserSession.token_hash == item.token_hash).values(revoked_at=now)
            )
        item.token_hash = None
        item.token = None
        get_or_create_session(connection, db)
        row = db.get(BrowserSession, item.token_hash)
        row.user_id = user_id
        row.authenticated_at = now
        row.authentication_method = method
    else:
        row = retrieve_current_session(db, item, lock=True)
        if row is None or row.user_id != user_id:
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        db.execute(delete(BrowserSessionFlash).where(BrowserSessionFlash.browser_session_token_hash == item.token_hash))
        db.execute(
            delete(AuthFlow).where(AuthFlow.browser_session_token_hash == item.token_hash, AuthFlow.purpose == "reset")
        )
        token = secrets.token_urlsafe(32)
        row.token_hash = utils.digest(token)
        row.authenticated_at = now
        row.authentication_method = method
        item.token_hash = row.token_hash
        item.token = token
    item.user_id = user_id
    item.authenticated_at = now
    item.method = method
    item.set_cookie = True
    item.clear_cookie = False
    db.flush()
