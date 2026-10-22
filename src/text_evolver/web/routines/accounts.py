from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from text_evolver.db.models import GoogleIdentity, Passkey, UserAccount, UserPassword
from text_evolver.web.validation_utils import ValidationError


def lock_user(db: Session, user_id: UUID) -> UserAccount:
    return db.scalar(
        select(UserAccount).where(UserAccount.id == user_id).with_for_update().execution_options(populate_existing=True)
    )


def create_account(db: Session, username: str) -> UserAccount:
    statement = (
        insert(UserAccount)
        .values(username=username)
        .on_conflict_do_nothing(index_elements=[UserAccount.username])
        .returning(UserAccount)
    )
    user = db.scalars(statement).one_or_none()
    if user is None:
        raise ValidationError("Username is already registered")
    return user


def has_alternative(db: Session, user_id: UUID, *, removing_google=False, removing_passkey=None) -> bool:
    if db.get(UserPassword, user_id):
        return True
    if not removing_google and db.get(GoogleIdentity, user_id):
        return True
    query = select(Passkey.id).where(Passkey.user_id == user_id)
    if removing_passkey is not None:
        query = query.where(Passkey.id != removing_passkey)
    return db.scalar(query.limit(1)) is not None
