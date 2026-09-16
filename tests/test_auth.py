from argon2 import PasswordHasher, Type

from text_evolver.db.models import UserPassword
from text_evolver.web.auth import hash_password, rehash_password_if_needed, verify_password


def _stored_password(password: str, user_id: int = 7) -> UserPassword:
    return UserPassword(user_id=user_id, password_hash=hash_password(password))


def test_password_is_stored_as_argon2id_hash():
    stored = _stored_password("secret password")

    assert stored.password_hash.startswith("$argon2id$")
    assert "secret password" not in stored.password_hash
    assert verify_password("secret password", stored)
    assert not verify_password("wrong password", stored)


def test_invalid_password_hash_fails_authentication():
    stored = UserPassword(user_id=7, password_hash="not-an-argon2-hash")

    assert not verify_password("secret password", stored)


def test_missing_user_runs_dummy_verification_and_fails():
    assert not verify_password("secret password", None)


def test_password_hash_is_upgraded_after_successful_verification():
    old_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID)
    stored = UserPassword(user_id=7, password_hash=old_hasher.hash("secret password"))
    old_hash = stored.password_hash

    assert verify_password("secret password", stored)
    rehash_password_if_needed(stored, "secret password")

    assert stored.password_hash != old_hash
    assert verify_password("secret password", stored)
