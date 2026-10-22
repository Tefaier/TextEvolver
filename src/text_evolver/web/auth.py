import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from text_evolver.db.models import BrowserSession, UserAccount
from text_evolver.web.login_throttle import clear_login_failure, client_ip
from text_evolver.web.routines.accounts import lock_user
from text_evolver.web.sessions import FRESHNESS, reset_session, retrieve_current_session, state
from text_evolver.web.utils import utc, utcnow


def login(request: Request, user: UserAccount, method: str = "password", *, recreate: bool = True) -> None:
    clear_login_failure(request.scope["db"], user.username, client_ip(request.client.host if request.client else None))
    reset_session(request, request.scope["db"], user.id, method, recreate=recreate)
    request.session.clear()
    get_or_create_csrf(request)
    user.last_entry = utcnow()


def logout(request: Request) -> None:
    item = state(request)
    if item.token_hash is not None:
        request.scope["db"].execute(
            update(BrowserSession).where(BrowserSession.token_hash == item.token_hash).values(revoked_at=utcnow())
        )
    item.clear()
    request.session.clear()


def current_user(request: Request, session: Session) -> UserAccount | None:
    row = retrieve_current_session(session, state(request))
    return session.get(UserAccount, row.user_id) if row and row.user_id else None


def require_user(request: Request, session: Session) -> UserAccount:
    user = current_user(request, session)
    if user is None:
        if "application/json" in request.headers.get("accept", ""):
            raise HTTPException(401, "Your session has expired. Please sign in again.")
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": str(request.url_for("login_page"))},
        )
    return user


def require_recent(request: Request, db: Session) -> UserAccount:
    user = require_user(request, db)
    user = lock_user(db, user.id)
    row = retrieve_current_session(db, state(request), lock=True)
    if row is None:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    if row.authenticated_at is None or utcnow() - utc(row.authenticated_at) >= FRESHNESS:
        raise HTTPException(428, "Please authenticate again to continue.")
    return user


def get_or_create_csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token
