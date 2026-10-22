import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from text_evolver.db.models import AuthFlow
from text_evolver.web import flows
from text_evolver.web.middlewares.sessions import SessionState
from text_evolver.web.sessions import get_or_create_session
from text_evolver.web.utils import digest, utc, utcnow


@pytest.mark.parametrize(
    "purpose", ["google", "google_signup", "register", "login", "reauthenticate", "recover", "reset"]
)
def test_replacing_flow_preserves_id_and_invalidates_previous_token(database, monkeypatch, purpose):
    now = utcnow()
    monkeypatch.setattr(flows, "utcnow", lambda: now)
    request = Request({"type": "http", "session_state": SessionState()})
    with Session(database) as db:
        first_token = flows.create_flow(request, db, purpose, {"challenge": "first"})
        first = db.execute(select(AuthFlow.id, AuthFlow.expires_at)).one()
        now += dt.timedelta(minutes=1)
        second_token = flows.create_flow(request, db, purpose, {"challenge": "second"})
        replacement = db.execute(select(AuthFlow.id, AuthFlow.token_hash, AuthFlow.data, AuthFlow.expires_at)).one()

        assert replacement.id == first.id
        assert second_token != first_token
        assert replacement.token_hash == digest(second_token)
        assert json.loads(replacement.data) == {"challenge": "second"}
        assert utc(replacement.expires_at) == now + dt.timedelta(minutes=5)
        assert replacement.expires_at > first.expires_at
        with pytest.raises(HTTPException) as error:
            flows.consume_flow(request, db, purpose, first_token)
        assert error.value.status_code == 400
        assert flows.consume_flow(request, db, purpose, second_token).id == first.id
        assert db.scalar(select(func.count()).select_from(AuthFlow)) == 0


def test_flow_upsert_keeps_other_purposes_and_sessions_separate(database):
    first_request = Request({"type": "http", "session_state": SessionState()})
    second_request = Request({"type": "http", "session_state": SessionState()})
    with Session(database) as db:
        flows.create_flow(first_request, db, "google", {"challenge": "old"})
        other_purpose = flows.create_flow(first_request, db, "recover", {"challenge": "recover"})
        other_session = flows.create_flow(second_request, db, "google", {"challenge": "other session"})
        replacement = flows.create_flow(first_request, db, "google", {"challenge": "replacement"})

        assert db.scalar(select(func.count()).select_from(AuthFlow)) == 3
        assert flows.consume_flow(first_request, db, "recover", other_purpose).token_hash == digest(other_purpose)
        assert flows.consume_flow(second_request, db, "google", other_session).token_hash == digest(other_session)
        assert flows.consume_flow(first_request, db, "google", replacement).token_hash == digest(replacement)


@pytest.mark.parametrize("purpose", ["google", "register", "reset"])
def test_concurrent_flow_creation_keeps_one_flow_per_session_and_purpose(database, purpose):
    initial_request = Request({"type": "http", "session_state": SessionState()})
    with Session(database) as db:
        browser = get_or_create_session(initial_request, db)
        db.commit()
    barrier = threading.Barrier(2)

    def create(challenge):
        request = Request({
            "type": "http",
            "session_state": SessionState(token=browser.token, token_hash=browser.token_hash),
        })
        with Session(database) as db:
            barrier.wait(timeout=10)
            token = flows.create_flow(request, db, purpose, {"challenge": challenge})
            db.commit()
            return token

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create, challenge) for challenge in ("first", "second")]
        tokens = [future.result(timeout=10) for future in futures]
    with Session(database) as db:
        row = db.scalars(select(AuthFlow)).one()
        assert row.browser_session_token_hash == browser.token_hash
        assert row.purpose == purpose
        assert row.token_hash in {digest(token) for token in tokens}
