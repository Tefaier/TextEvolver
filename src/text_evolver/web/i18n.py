"""Request-scoped gettext translations shared by server templates and browser messages."""

from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

from babel.support import Translations

LANGUAGES = {"en", "ru"}
LOCALES = Path(__file__).resolve().parents[1] / "locales"
locale = ContextVar("locale", default="en")


@lru_cache(maxsize=len(LANGUAGES))
def catalog(language: str) -> Translations:
    return Translations.load(LOCALES, [language])


def translate(message: str, **values) -> str:
    translated = catalog(locale.get()).gettext(message)
    return translated % values if values else translated


def plural(singular: str, multiple: str, number: int) -> str:
    return catalog(locale.get()).ngettext(singular, multiple, number) % {"count": number}


def choose_language(connection, user=None) -> str:
    if user is not None and user.language in LANGUAGES:
        return user.language
    candidates = []
    for entry in connection.headers.get("accept-language", "").split(","):
        language_parts = entry.strip().partition(";")
        language = language_parts[0]
        weight = language_parts[2]
        try:
            quality = float(weight.strip().removeprefix("q=")) if weight else 1.0
        except ValueError:
            continue
        if 0 < quality <= 1:
            candidates.append((quality, language.lower().split("-")[0]))
    for _, language in sorted(candidates, key=lambda item: item[0], reverse=True):
        if language in LANGUAGES:
            return language
    return "en"


BROWSER_MESSAGES = [
    "The form could not be submitted. Please try again.",
    "The form could not be submitted. Check your connection and try again.",
    "Setting could not be deleted.",
    "Setting could not be deleted. Check your connection and try again.",
    "Waits ",
    "No files selected",
    "WebAuthn is not supported by this browser.",
    "Passkey name must contain 1 to 64 characters.",
    "Authentication failed or was cancelled. Please try again.",
]
