import argparse
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import UserPassword
from text_evolver.db.session import get_engine

PASSWORD_NONCE_BYTES = 12
PASSWORD_NONCE_INSERT_ATTEMPTS = 5


def decrypt_password(stored: UserPassword, settings: AppSettings) -> str:
    key = settings.password_key_for_version(stored.key_version)
    if key is None or len(stored.nonce) != PASSWORD_NONCE_BYTES:
        raise RuntimeError(f"Password key version {stored.key_version} is unavailable")
    try:
        decrypted = AESGCM(key).decrypt(stored.nonce, stored.encoded_password, None)
        return decrypted.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"Password record {stored.id} could not be decrypted") from exc


def replace_encrypted_password(
    session: Session,
    stored: UserPassword,
    password: str,
    settings: AppSettings,
) -> None:
    for _ in range(PASSWORD_NONCE_INSERT_ATTEMPTS):
        nonce = secrets.token_bytes(PASSWORD_NONCE_BYTES)
        encoded_password = AESGCM(settings.current_password_key).encrypt(
            nonce,
            password.encode("utf-8"),
            None,
        )
        try:
            with session.begin_nested():
                stored.encoded_password = encoded_password
                stored.nonce = nonce
                stored.key_version = settings.password_key_current_version
                session.flush((stored,))
        except IntegrityError as exception:
            diagnostic = getattr(exception.orig, "diag", None)
            message = str(exception.orig).lower()
            if getattr(diagnostic, "constraint_name", None) == "uq_user_password_nonce" or (
                "unique" in message and "user_password.nonce" in message
            ):
                session.refresh(stored)
                continue
            raise
        else:
            return
    raise RuntimeError(f"Could not generate a unique nonce for password record {stored.id}")


def upgrade_cipher_version(engine: Engine, settings: AppSettings, batch_size: int) -> int:
    previous_version = settings.password_key_previous_version
    if previous_version is None or settings.password_key_previous is None:
        raise RuntimeError("A previous password key and version must be configured")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    upgraded = 0
    while True:
        with Session(engine) as session:
            passwords = list(
                session.scalars(
                    select(UserPassword)
                    .where(UserPassword.key_version == previous_version)
                    .order_by(UserPassword.id)
                    .limit(batch_size)
                    .with_for_update()
                )
            )
            if not passwords:
                return upgraded

            for stored in passwords:
                password = decrypt_password(stored, settings)
                replace_encrypted_password(session, stored, password, settings)
            session.commit()
            upgraded += len(passwords)


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-encrypt passwords from the configured previous key version to the current version."
    )
    parser.add_argument("--batch-size", type=positive_integer, default=100)
    arguments = parser.parse_args()

    settings = get_application_settings()
    upgraded = upgrade_cipher_version(get_engine(), settings, arguments.batch_size)
    print(
        f"Upgraded {upgraded} password records from key version "
        f"{settings.password_key_previous_version} to {settings.password_key_current_version}."
    )


if __name__ == "__main__":
    main()
