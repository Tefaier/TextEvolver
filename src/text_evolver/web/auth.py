import hmac
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings
from text_evolver.db.models import UserAccount, UserPassword

PASSWORD_NONCE_BYTES = 12
PASSWORD_NONCE_INSERT_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class EncryptedPassword:
    encoded_password: bytes
    nonce: bytes
    key_version: int


def encrypt_password(password: str, settings: AppSettings) -> EncryptedPassword:
    key_version = settings.password_key_current_version
    nonce = secrets.token_bytes(PASSWORD_NONCE_BYTES)
    encoded_password = AESGCM(settings.current_password_key).encrypt(
        nonce,
        password.encode("utf-8"),
        None
    )
    return EncryptedPassword(encoded_password=encoded_password, nonce=nonce, key_version=key_version)


def add_encrypted_password(
    session: Session,
    user_id: int,
    password: str,
    settings: AppSettings,
) -> UserPassword:
    for _ in range(PASSWORD_NONCE_INSERT_ATTEMPTS):
        encrypted = encrypt_password(password, settings)
        stored = UserPassword(
            user_id=user_id,
            encoded_password=encrypted.encoded_password,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        try:
            with session.begin_nested():
                session.add(stored)
                session.flush()
        except IntegrityError as exception:
            diagnostic = getattr(exception.orig, "diag", None)
            message = str(exception.orig).lower()
            if getattr(diagnostic, "constraint_name", None) == "uq_user_password_nonce" or (
                "unique" in message and "user_password.nonce" in message
            ):
                continue
            raise
        else:
            return stored
    raise RuntimeError("Could not generate a unique password nonce")


def verify_password(password: str, stored: UserPassword, settings: AppSettings) -> bool:
    key = settings.password_key_for_version(stored.key_version)
    if key is None or len(stored.nonce) != PASSWORD_NONCE_BYTES:
        return False
    try:
        decrypted = AESGCM(key).decrypt(
            stored.nonce,
            stored.encoded_password,
            None,
        )
    except (InvalidTag, ValueError):
        return False
    return hmac.compare_digest(decrypted, password.encode("utf-8"))


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
