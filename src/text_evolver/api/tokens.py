"""Personal API tokens; only digests are persisted."""

import secrets
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from text_evolver.db.models import ApiToken, UserAccount
from text_evolver.web.routines.accounts import lock_user
from text_evolver.web.utils import digest, utcnow

bearer = HTTPBearer(auto_error=False)
PERMISSIONS = {"read", "read_write"}


def issue_token(db: Session, user_id: UUID, permission: str, *, rotate: bool = False) -> str:
    if permission not in PERMISSIONS:
        raise HTTPException(422, "Invalid token permission")
    lock_user(db, user_id)
    row = db.get(ApiToken, (user_id, permission))
    if row is not None and not rotate:
        raise HTTPException(409, "A token already exists for this permission")
    if row is None and rotate:
        raise HTTPException(404, "Token not found")
    secret = "te_" + secrets.token_urlsafe(32)
    if row is None:
        row = ApiToken(user_id=user_id, permission=permission)
        db.add(row)
    row.token_hash = digest(secret)
    row.issued_at = utcnow()
    db.flush()
    return secret


def revoke_tokens(db: Session, user_id: UUID) -> None:
    db.execute(delete(ApiToken).where(ApiToken.user_id == user_id))


async def authenticate(request: Request) -> None:
    credentials = await bearer(request)
    if credentials is None or len(credentials.credentials) > 128:
        raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})
    db = request.scope["db"]
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest(credentials.credentials)))
    if token is None:
        raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})
    request.scope["api_user_id"] = token.user_id
    request.scope["api_permission"] = token.permission
    db.rollback()


def api_user(request: Request) -> UserAccount:
    user = request.scope["db"].get(UserAccount, request.scope["api_user_id"])
    if user is None:
        raise HTTPException(401, "Invalid or missing API token", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_write(request: Request) -> None:
    if request.scope["api_permission"] != "read_write":
        raise HTTPException(403, "A read+write token is required")
