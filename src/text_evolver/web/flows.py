import datetime as dt
import json
import secrets
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from text_evolver.db.models import AuthFlow
from text_evolver.web.sessions import get_or_create_session, retrieve_current_session, state
from text_evolver.web.utils import digest, utcnow
from text_evolver.web.validation_utils import ValidationError, validate_flow_token


def create_flow(
    request: Request, 
    db: Session, 
    purpose: str, 
    data: dict, 
    *,
    user_id: UUID | None = None, 
    passkey_id: int | None = None,
) -> str:
    item = get_or_create_session(request, db)
    token = secrets.token_urlsafe(32)
    values = dict(
        browser_session_token_hash=item.token_hash,
        purpose=purpose,
        token_hash=digest(token),
        data=json.dumps(data),
        user_id=user_id,
        passkey_id=passkey_id,
        expires_at=utcnow() + dt.timedelta(minutes=5),
    )
    db.execute(
        insert(AuthFlow)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[AuthFlow.browser_session_token_hash, AuthFlow.purpose],
            set_={
                name: value for name, value in values.items() if name not in {"browser_session_token_hash", "purpose"}
            },
        )
    )
    return token


def consume_flow(request: Request, db: Session, purpose: str, token: str) -> AuthFlow:
    try:
        validate_flow_token(token)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from None
    if retrieve_current_session(db, state(request)) is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    row = db.scalar(
        delete(AuthFlow)
        .where(
            AuthFlow.browser_session_token_hash == state(request).token_hash,
            AuthFlow.purpose == purpose,
            AuthFlow.token_hash == digest(token),
            AuthFlow.expires_at > utcnow(),
        )
        .returning(AuthFlow)
        .execution_options(synchronize_session=False)
    )
    if row is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    return row


def consume_reset_grant(request: Request, db: Session, grant_id: int) -> None:
    if retrieve_current_session(db, state(request), lock=True) is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    consumed = db.scalar(
        delete(AuthFlow)
        .where(
            AuthFlow.id == grant_id,
            AuthFlow.browser_session_token_hash == state(request).token_hash,
            AuthFlow.purpose == "reset",
            AuthFlow.expires_at > utcnow(),
        )
        .returning(AuthFlow.id)
        .execution_options(synchronize_session=False)
    )
    if consumed is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
