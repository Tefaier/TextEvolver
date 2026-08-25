from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from text_evolver.config import get_application_settings


@lru_cache
def get_engine() -> Engine:
    url = get_application_settings().database_url
    connect_args: dict[str, object] = {}
    if url.startswith("postgresql+psycopg"):
        connect_args["prepare_threshold"] = None
    return create_engine(
        url,
        connect_args=connect_args,
        poolclass=NullPool,
        pool_pre_ping=True,
    )


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_db() -> Generator[Session, None, None]:
    with session_scope() as session:
        yield session
