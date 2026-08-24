import hmac
import secrets

from fastapi import HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from text_evolver.db.models import UserAccount

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def login(request: Request, user: UserAccount) -> None:
    request.session.clear()
    request.session["user_id"] = user.id
    csrf_token(request)


def logout(request: Request) -> None:
    request.session.clear()


def current_user(request: Request, session: Session) -> UserAccount | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return session.get(UserAccount, user_id)


def require_user(request: Request, session: Session) -> UserAccount:
    user = current_user(request, session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": str(request.url_for("login_page"))},
        )
    return user


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


async def validate_csrf(request: Request) -> None:
    expected = request.session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token")
    if supplied is None:
        form = await request.form()
        value = form.get("csrf_token")
        supplied = value if isinstance(value, str) else None
    if not isinstance(expected, str) or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def flash(request: Request, message: str, category: str = "error") -> None:
    messages = request.session.setdefault("flash_messages", [])
    messages.append([category, message])
    request.session["flash_messages"] = messages[-20:]


def pop_flashes(request: Request) -> list[tuple[str, str]]:
    return [tuple(item) for item in request.session.pop("flash_messages", [])]

