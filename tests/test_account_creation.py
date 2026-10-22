import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from text_evolver.db.models import UserAccount
from text_evolver.web.routines.accounts import create_account
from text_evolver.web.validation_utils import ValidationError


def test_create_account_returns_persisted_account(database):
    with Session(database) as db:
        user = create_account(db, "new-account")
        assert user.username == "new-account"
        assert user.last_entry is not None
        assert user.setting_limit == 5
        assert db.get(UserAccount, user.id) is user
        user_id = user.id
        db.commit()
    with Session(database) as db:
        assert db.get(UserAccount, user_id).username == "new-account"


def test_duplicate_username_preserves_account_and_usable_transaction(database):
    with Session(database) as db:
        original = create_account(db, "duplicate")
        original.setting_limit = 7
        db.commit()
        with pytest.raises(ValidationError, match="Username is already registered"):
            create_account(db, "duplicate")
        assert db.scalar(select(UserAccount).where(UserAccount.username == "duplicate")) is original
        assert original.setting_limit == 7
        create_account(db, "another-account")
        db.commit()
        assert len(db.scalars(select(UserAccount)).all()) == 2


def test_create_account_participates_in_callers_transaction(database):
    with Session(database) as db:
        create_account(db, "rolled-back")
        db.rollback()
    with Session(database) as db:
        assert db.scalar(select(UserAccount)) is None


def test_concurrent_username_creation_has_one_winner(database):
    barrier = threading.Barrier(2)

    def register():
        with Session(database) as db:
            barrier.wait(timeout=10)
            try:
                create_account(db, "concurrent-account")
            except ValidationError:
                db.rollback()
                return "duplicate"
            db.commit()
            return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: register(), range(2)))
    assert sorted(results) == ["created", "duplicate"]
    with Session(database) as db:
        assert len(db.scalars(select(UserAccount)).all()) == 1
