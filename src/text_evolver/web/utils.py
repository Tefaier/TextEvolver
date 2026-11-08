"""Shared web time and token helpers."""

import datetime as dt
import hashlib


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
