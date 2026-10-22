import pytest

from text_evolver.web.i18n import locale
from text_evolver.web.validation_utils import (
    ValidationError,
    is_supported_document_filename,
    parse_bool,
    validate_account_action,
    validate_credentials,
    validate_flow_token,
    validate_google_purpose,
    validate_image_row_token,
    validate_image_upload_size,
    validate_language,
    validate_passkey_id,
    validate_passkey_name,
    validate_password,
    validate_phrase_regex,
    validate_repeated_fields,
    validate_setting_text,
    validate_upload_filename,
    validate_username,
    validate_webauthn_body,
    validate_webauthn_credential,
    validate_webauthn_operation,
)


@pytest.mark.parametrize("length,valid", [(3, False), (4, True), (62, True), (63, False)])
def test_username_boundaries(length, valid):
    if valid:
        validate_username("я" * length)
    else:
        with pytest.raises(ValidationError, match="Username"):
            validate_username("я" * length)


@pytest.mark.parametrize("length,valid", [(11, False), (12, True), (128, True), (129, False)])
def test_password_boundaries(length, valid):
    if valid:
        validate_password("x" * length, "x" * length)
    else:
        with pytest.raises(ValidationError, match="Password must"):
            validate_password("x" * length, "x" * length)


def test_optional_credential_checks_and_confirmation():
    validate_credentials()
    validate_credentials(username="reader")
    validate_credentials(password="correct horse")
    with pytest.raises(ValidationError, match="confirmation"):
        validate_credentials(password="correct horse", confirm="different password")
    with pytest.raises(ValidationError, match="Username"):
        validate_credentials(username="bad", password="short")


@pytest.mark.parametrize("value,expected", [(" key ", "key"), ("я" * 64, "я" * 64)])
def test_passkey_names_are_trimmed_and_accept_boundary(value, expected):
    assert validate_passkey_name(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "x" * 65])
def test_invalid_passkey_names(value):
    with pytest.raises(ValidationError, match="Passkey name"):
        validate_passkey_name(value)


def test_passkey_ids_are_parsed_without_ownership_or_existence_checks():
    assert validate_passkey_id(" 123 ") == 123
    assert validate_passkey_id("-1") == -1
    with pytest.raises(ValidationError, match="Invalid passkey"):
        validate_passkey_id("not-an-id")


@pytest.mark.parametrize("validator,allowed,rejected", [
    (validate_language, ["default", "en", "ru"], "de"),
    (validate_webauthn_operation, ["register", "login", "reauthenticate", "recover"], "reset"),
    (validate_google_purpose, ["login", "link", "reauthenticate"], "recover"),
    (validate_account_action, ["username", "password", "unlink_google", "rename_passkey", "remove_passkey"], None),
])
def test_fixed_choices(validator, allowed, rejected):
    for value in allowed:
        validator(value)
    with pytest.raises(ValidationError):
        validator(rejected)


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (" YES ", True), ("0", False)])
def test_explicit_boolean_parsing(value, expected):
    assert parse_bool(value, "active") is expected


@pytest.mark.parametrize("value", ["unknown", "True or False", "", None])
def test_invalid_booleans(value):
    with pytest.raises(ValidationError, match="active"):
        parse_bool(value, "active")


def test_setting_text_normalization_and_boundaries():
    assert validate_setting_text("  title  ", "Name", strip=True, required=True) == "title"
    assert validate_setting_text("x" * 64, "Name") == "x" * 64
    assert validate_setting_text("  ", "Name") == "  "
    with pytest.raises(ValidationError, match="must not be empty"):
        validate_setting_text("  ", "Name", strip=True, required=True)
    with pytest.raises(ValidationError, match="at most 64"):
        validate_setting_text("x" * 65, "Name")


def test_repeated_field_alignment():
    validate_repeated_fields([[], []], ["from", "to"])
    validate_repeated_fields([["one"], ["two"]], ["from", "to"])
    with pytest.raises(ValidationError, match="from, to"):
        validate_repeated_fields([["one"], []], ["from", "to"])


def test_image_row_tokens_validate_format_and_uniqueness_without_mutation():
    used = {"existing-1"}
    validate_image_row_token("new_2", used)
    validate_image_row_token("x" * 80, used)
    assert used == {"existing-1"}
    for value in ("existing-1", "", "../image", "x" * 81):
        with pytest.raises(ValidationError, match="row identifier"):
            validate_image_row_token(value, used)


def test_image_size_checks_use_metadata_only():
    assert validate_image_upload_size(1, "image.png") == 1
    for size in (None, 0, -1):
        with pytest.raises(ValidationError, match="image.png"):
            validate_image_upload_size(size, "image.png")


def test_phrase_regex_checks_pattern_and_replacement_references():
    validate_phrase_regex(r"(cat)", r"\1s")
    for source, replacement in [("[", ""), (r"(cat)", r"\2")]:
        with pytest.raises(ValidationError, match="Invalid phrase regular expression"):
            validate_phrase_regex(source, replacement)


def test_flow_token_shape_and_length():
    validate_flow_token("x" * 128)
    for token in (None, 12, "x" * 129):
        with pytest.raises(ValidationError):
            validate_flow_token(token)


def test_webauthn_body_and_response_shape():
    validate_webauthn_body(b"x" * 65536)
    with pytest.raises(ValidationError):
        validate_webauthn_body(b"x" * 65537)
    validate_webauthn_credential({"response": {}})
    for credential in (None, [], {}, {"response": "invalid"}):
        with pytest.raises(ValidationError):
            validate_webauthn_credential(credential)


def test_validation_messages_use_current_locale():
    token = locale.set("ru")
    try:
        with pytest.raises(ValidationError, match="Имя"):
            validate_username("x")
    finally:
        locale.reset(token)


@pytest.mark.parametrize("filename,allowed", [
    ("book.DOCX", True), ("book.epub", True), ("book.html", True), ("book.fb2", True),
    (None, False), ("", False), ("book.txt", False),
])
def test_document_extensions(filename, allowed):
    assert is_supported_document_filename(filename) is allowed


def test_upload_names_are_sanitized_and_duplicate_names_rejected():
    used = {"book.html"}
    assert validate_upload_filename("../other.html", used) == "other.html"
    assert used == {"book.html"}
    for value in (None, "", "../book.html", "/tmp/book.html"):
        with pytest.raises(ValidationError, match="filenames must be unique"):
            validate_upload_filename(value, used)
