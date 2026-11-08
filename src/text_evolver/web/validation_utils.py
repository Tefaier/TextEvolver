"""Fixed validation rules for submitted values, independent of requests and databases."""

import math
import re
from pathlib import Path
from typing import Any

from text_evolver.web.i18n import LANGUAGES, translate

TRUTHY = {"true", "1", "yes", "on"}
FALSY = {"false", "0", "no", "off"}
SETTING_TEXT_MAX_LENGTH = 64
MATCHER_MAX_LENGTH = 128
SETTING_ELEMENT_LIMIT = 100
SETTING_IMAGES_MAX_BYTES = 512 * 1024 * 1024
PASSKEY_LIMIT = 3
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
IMAGE_ROW_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,80}")
WEBAUTHN_OPERATIONS = {"register", "login", "reauthenticate", "recover"}
GOOGLE_PURPOSES = {"link", "login", "reauthenticate"}
ACCOUNT_ACTIONS = {"username", "password", "unlink_google", "rename_passkey", "remove_passkey"}
ALLOWED_EXTENSIONS = {"docx", "epub", "html", "fb2"}


class ValidationError(ValueError):
    pass


class ConflictError(ValidationError):
    """A valid operation conflicts with the current resource state."""


def validate_username(username: str) -> None:
    if not 4 <= len(username) <= 62:
        raise ValidationError(translate("Username must contain between 4 and 62 characters"))


def validate_password(password: str | None = None, confirm: str | None = None) -> None:
    if password is not None and not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValidationError(
            translate(
                "Password must contain between %(value0)s and %(value1)s characters",
                value0=PASSWORD_MIN_LENGTH,
                value1=PASSWORD_MAX_LENGTH,
            )
        )
    if confirm is not None and confirm != password:
        raise ValidationError(translate("Password confirmation does not match"))


def validate_credentials(username: str | None = None, password: str | None = None, confirm: str | None = None) -> None:
    if username is not None:
        validate_username(username)
    validate_password(password, confirm)


def validate_passkey_name(value: Any) -> str:
    name = str(value).strip()
    if not 1 <= len(name) <= 64:
        raise ValidationError(translate("Passkey name must contain 1 to 64 characters."))
    return name


def validate_passkey_id(value: Any) -> int:
    try:
        return int(str(value))
    except ValueError:
        raise ValidationError(translate("Invalid passkey")) from None


def validate_language(choice: str) -> None:
    if choice not in LANGUAGES | {"default"}:
        raise ValidationError(translate("Invalid language"))


def validate_webauthn_operation(operation: str) -> None:
    if operation not in WEBAUTHN_OPERATIONS:
        raise ValidationError(translate("Invalid account action"))


def validate_google_purpose(purpose: str) -> None:
    if purpose not in GOOGLE_PURPOSES:
        raise ValidationError(translate("Invalid account action"))


def validate_account_action(action: Any) -> None:
    if not isinstance(action, str) or action not in ACCOUNT_ACTIONS:
        raise ValidationError(translate("Invalid account action"))


def validate_flow_token(token: Any) -> None:
    if not isinstance(token, str) or len(token) > 128:
        raise ValidationError(translate("Authentication failed. Please try again."))


def validate_webauthn_body(body: bytes) -> None:
    if len(body) > 65536:
        raise ValidationError(translate("Authentication failed. Please try again."))


def validate_webauthn_credential(credential: Any) -> None:
    if not isinstance(credential, dict) or not isinstance(credential.get("response"), dict):
        raise ValidationError(translate("Authentication failed. Please try again."))


def parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    raise ValidationError(translate("Invalid boolean value for %(value0)s", value0=field_name))


def validate_setting_text(value: Any, field_name: str, *, strip: bool = False, required: bool = False) -> str:
    text_value = str(value)
    if strip:
        text_value = text_value.strip()
    if required and not text_value:
        raise ValidationError(translate("%(value0)s must not be empty", value0=field_name))
    if len(text_value) > SETTING_TEXT_MAX_LENGTH:
        raise ValidationError(
            translate(
                "%(value0)s must contain at most %(value1)s characters",
                value0=field_name,
                value1=SETTING_TEXT_MAX_LENGTH,
            )
        )
    return text_value


def validate_repeated_fields(values: list[list[Any]], keys: list[str]) -> None:
    if len({len(value) for value in values}) > 1:
        raise ValidationError(translate("Mismatched repeated fields: %(value0)s", value0=", ".join(keys)))


def validate_image_row_token(token: str, used_tokens: set[str]) -> None:
    if IMAGE_ROW_TOKEN_PATTERN.fullmatch(token) is None or token in used_tokens:
        raise ValidationError(translate("Image conversion row identifier is invalid"))


def validate_image_upload_size(size: int | None, filename: str | None) -> int:
    if size is None:
        raise ValidationError(translate("Cannot determine the size of uploaded image '%(value0)s'", value0=filename))
    if size <= 0:
        raise ValidationError(translate("Uploaded image '%(value0)s' is empty", value0=filename))
    return size


def validate_phrase_regex(source: str, replacement: str) -> None:
    try:
        pattern = re.compile(source)
        pattern.sub(replacement, "")
    except re.error as error:
        raise ValidationError(translate("Invalid phrase regular expression: %(value0)s", value0=error)) from error


def is_supported_document_filename(filename: str | None) -> bool:
    return bool(filename and filename.rsplit(".", 1)[-1].lower() in ALLOWED_EXTENSIONS)


def validate_upload_filename(filename: str | None, used_names: set[str]) -> str:
    name = Path(filename or "").name
    if not name or name in used_names:
        raise ValidationError(translate("Uploaded filenames must be unique"))
    return name


def validate_matcher(value: Any, field_name: str, *, strip: bool = False) -> str:
    matcher = str(value)
    if strip:
        matcher = matcher.strip()
    if len(matcher) > MATCHER_MAX_LENGTH:
        raise ValidationError(translate(
            "%(value0)s must contain at most %(value1)s characters",
            value0=field_name, value1=MATCHER_MAX_LENGTH,
        ))
    return matcher


def validate_setting_element_count(count: int) -> None:
    if count > SETTING_ELEMENT_LIMIT:
        raise ValidationError(translate(
            "A setting can contain at most %(value0)s elements in total", value0=SETTING_ELEMENT_LIMIT,
        ))


def validate_setting_image_size(size: int) -> None:
    if size > SETTING_IMAGES_MAX_BYTES:
        raise ValidationError(translate(
            "Setting images must total at most %(value0)s MiB", value0=SETTING_IMAGES_MAX_BYTES // (1024 * 1024),
        ))


def parse_setting_separation(value: Any) -> int:
    try:
        return max(1, int(value))
    except (ValueError, TypeError, OverflowError):
        raise ValidationError(translate("Invalid image separation")) from None


def parse_setting_conversion(value: Any) -> float:
    try:
        conversion = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValidationError(translate("Invalid unit conversion multiplier")) from None
    if not math.isfinite(conversion):
        raise ValidationError(translate("Invalid unit conversion multiplier"))
    return conversion
