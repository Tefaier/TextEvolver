import datetime as dt

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from text_evolver.db.models import LoginFailure, LoginFailureByAccount
from text_evolver.web.utils import utc

LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = dt.timedelta(minutes=10)
ACCOUNT_DELAY_THRESHOLD = 5
ACCOUNT_MAX_DELAY_SECONDS = 60


def client_ip(request_client_host: str | None) -> str:
    return request_client_host or "unknown"


def lock_login_failure(session: Session, username: str, ip: str) -> LoginFailure | None:
    return session.scalar(
        select(LoginFailure)
        .where(LoginFailure.username == username, LoginFailure.ip == ip)
        .with_for_update()
    )


def lock_account_login_failure(session: Session, username: str) -> LoginFailureByAccount | None:
    return session.scalar(
        select(LoginFailureByAccount)
        .where(LoginFailureByAccount.username == username)
        .with_for_update()
    )


def _pair_login_is_blocked(failure: LoginFailure | None, now: dt.datetime) -> bool:
    if failure is None or failure.failure_count < LOGIN_FAILURE_LIMIT:
        return False
    return now - utc(failure.window_started_at) < LOGIN_FAILURE_WINDOW


def account_retry_delay(failure_score: int) -> dt.timedelta:
    if failure_score < ACCOUNT_DELAY_THRESHOLD:
        return dt.timedelta(0)
    seconds = min(2 ** (failure_score - ACCOUNT_DELAY_THRESHOLD), ACCOUNT_MAX_DELAY_SECONDS)
    return dt.timedelta(seconds=seconds)


def login_is_blocked(
    pair_failure: LoginFailure | None,
    account_failure: LoginFailureByAccount | None,
    now: dt.datetime,
) -> bool:
    if (pair_failure is not None and 
        pair_failure.failure_count >= LOGIN_FAILURE_LIMIT and 
        now - utc(pair_failure.window_started_at) < LOGIN_FAILURE_WINDOW):
        return True
    if (account_failure is not None and 
        now - utc(account_failure.last_failure_at) < account_retry_delay(account_failure.failure_score)):
        return True
    return False


def record_login_failure(
    session: Session,
    failure: LoginFailure | None,
    username: str,
    ip: str,
    now: dt.datetime,
) -> None:
    if failure is None:
        values = {"username": username, "ip": ip, "failure_count": 1, "window_started_at": now}
        session.execute(
            insert(LoginFailure).values(**values).on_conflict_do_update(
                index_elements=[LoginFailure.username, LoginFailure.ip],
                set_={
                    "failure_count": LoginFailure.failure_count + 1,
                    "window_started_at": case(
                        (LoginFailure.window_started_at <= now - LOGIN_FAILURE_WINDOW, now),
                        else_=LoginFailure.window_started_at,
                    ),
                },
            )
        )
        return
    if now - utc(failure.window_started_at) >= LOGIN_FAILURE_WINDOW:
        failure.window_started_at = now
    failure.failure_count += 1


def record_account_login_failure(
    session: Session,
    failure: LoginFailureByAccount | None,
    username: str,
    now: dt.datetime,
) -> None:
    if failure is None:
        values = {"username": username, "failure_score": 1, "last_failure_at": now}
        session.execute(
            insert(LoginFailureByAccount).values(**values).on_conflict_do_update(
                index_elements=[LoginFailureByAccount.username],
                set_={
                    "failure_score": LoginFailureByAccount.failure_score + 1,
                    "last_failure_at": now,
                },
            )
        )
        return
    failure.failure_score += 1
    failure.last_failure_at = now


def clear_login_failure(session: Session, username: str, ip: str) -> None:
    session.execute(delete(LoginFailure).where(LoginFailure.username == username, LoginFailure.ip == ip))
    session.execute(delete(LoginFailureByAccount).where(LoginFailureByAccount.username == username))
