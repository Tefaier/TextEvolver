"""Database-backed flash messages for browser sessions."""

from fastapi import Request
from sqlalchemy import delete, select

from text_evolver.db.models import BrowserSessionFlash
from text_evolver.web.i18n import translate
from text_evolver.web.sessions import get_or_create_session, retrieve_current_session, state


def flash(request: Request, message: str, category: str = "error") -> None:
    db = request.scope["db"]
    item = get_or_create_session(request, db)
    if retrieve_current_session(db, item) is None:
        return
    db.add(
        BrowserSessionFlash(browser_session_token_hash=item.token_hash, category=category, message=translate(message))
    )
    db.flush()
    db.execute(
        delete(BrowserSessionFlash).where(
            BrowserSessionFlash.id.in_(
                select(BrowserSessionFlash.id)
                .where(BrowserSessionFlash.browser_session_token_hash == item.token_hash)
                .order_by(BrowserSessionFlash.id.desc())
                .offset(20)
            )
        )
    )


def pop_flashes(request: Request) -> list[tuple[str, str]]:
    db = request.scope["db"]
    if retrieve_current_session(db, state(request)) is None:
        return []
    rows = db.execute(
        delete(BrowserSessionFlash)
        .where(BrowserSessionFlash.browser_session_token_hash == state(request).token_hash)
        .returning(BrowserSessionFlash.id, BrowserSessionFlash.category, BrowserSessionFlash.message)
    ).all()
    return [(row.category, row.message) for row in sorted(rows, key=lambda row: row.id)]
