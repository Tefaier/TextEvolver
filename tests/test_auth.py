from pydantic import ValidationError

from text_evolver.config import AppSettings
from text_evolver.db.models import UserPassword
from text_evolver.web.auth import encrypt_password, verify_password


def _stored_password(password: str, user_id: int, settings: AppSettings) -> UserPassword:
    encrypted = encrypt_password(password, settings)
    return UserPassword(
        user_id=user_id,
        encoded_password=encrypted.encoded_password,
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )


def test_password_ciphertext_is_bound_to_user_and_key_version(app_settings):
    stored = _stored_password("secret password", user_id=7, settings=app_settings)

    assert verify_password("secret password", stored, app_settings)

    stored.user_id = 8
    assert not verify_password("secret password", stored, app_settings)
    stored.user_id = 7

    stored.key_version = app_settings.password_key_previous_version
    assert not verify_password("secret password", stored, app_settings)


def test_password_ciphertext_tampering_fails_authentication(app_settings):
    stored = _stored_password("secret password", user_id=7, settings=app_settings)
    stored.encoded_password = bytes([stored.encoded_password[0] ^ 1]) + stored.encoded_password[1:]

    assert not verify_password("secret password", stored, app_settings)


def test_password_with_unavailable_key_version_fails_authentication(app_settings):
    stored = _stored_password("secret password", user_id=7, settings=app_settings)
    stored.key_version = 999

    assert not verify_password("secret password", stored, app_settings)


def test_password_key_must_decode_to_32_bytes():
    try:
        AppSettings(
            database_url="sqlite+pysqlite:///test.db",
            secret_key="test-secret-key-with-at-least-thirty-two-characters",
            password_key_current="dG9vLXNob3J0",
            password_key_current_version=1,
            password_key_previous=None,
            password_key_previous_version=None,
        )
    except ValidationError as exc:
        assert "exactly 32 bytes" in str(exc)
    else:
        raise AssertionError("Short AES key was accepted")
