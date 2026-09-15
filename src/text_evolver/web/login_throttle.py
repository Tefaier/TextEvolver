import datetime as dt

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from text_evolver.db.models import LoginFailure

LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW = dt.timedelta(minutes=10)


def client_ip(request_client_host: str | None) -> str:
    return request_client_host or "unknown"


def lock_login_failure(session: Session, username: str, ip: str) -> LoginFailure | None:
    return session.scalar(
        select(LoginFailure)
        .where(LoginFailure.username == username, LoginFailure.ip == ip)
        .with_for_update()
    )


def _as_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def login_is_blocked(failure: LoginFailure | None, now: dt.datetime) -> bool:
    if failure is None or failure.failure_count < LOGIN_FAILURE_LIMIT:
        return False
    return now - _as_utc(failure.window_started_at) < LOGIN_FAILURE_WINDOW


def record_login_failure(
    session: Session,
    failure: LoginFailure | None,
    username: str,
    ip: str,
    now: dt.datetime,
) -> None:
    if failure is None:
        values = {"username": username, "ip": ip, "failure_count": 1, "window_started_at": now}
        insert = (
            postgresql_insert(LoginFailure)
            if session.get_bind().dialect.name == "postgresql"
            else sqlite_insert(LoginFailure)
        )
        session.execute(
            insert.values(**values).on_conflict_do_update(
                index_elements=[LoginFailure.username, LoginFailure.ip],
                set_={
                    "failure_count": LoginFailure.failure_count + 1,
                    "window_started_at": case(
                        (
                            LoginFailure.window_started_at <= now - LOGIN_FAILURE_WINDOW,
                            now,
                        ),
                        else_=LoginFailure.window_started_at,
                    ),
                },
            )
        )
        return
    if now - _as_utc(failure.window_started_at) >= LOGIN_FAILURE_WINDOW:
        failure.window_started_at = now
    failure.failure_count += 1


def clear_login_failure(session: Session, username: str, ip: str) -> None:
    session.execute(delete(LoginFailure).where(LoginFailure.username == username, LoginFailure.ip == ip))
