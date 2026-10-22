"""Argon2id password hashing, storage, and verification."""

import secrets
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy.orm import Session

from text_evolver.db.models import UserPassword

PASSWORD_HASHER = PasswordHasher(type=Type.ID)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def add_password(session: Session, user_id: UUID, password: str) -> UserPassword:
    stored = UserPassword(user_id=user_id, password_hash=hash_password(password))
    session.add(stored)
    session.flush()
    return stored


def verify_password(password: str, stored: UserPassword | None) -> bool:
    password_hash = stored.password_hash if stored is not None else DUMMY_PASSWORD_HASH
    try:
        valid = PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False
    return stored is not None and valid


def rehash_password_if_needed(stored: UserPassword, password: str) -> None:
    if PASSWORD_HASHER.check_needs_rehash(stored.password_hash):
        stored.password_hash = hash_password(password)
