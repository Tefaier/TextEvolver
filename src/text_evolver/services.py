from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from text_evolver.config import AppSettings
from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    ImageConversionFile,
    PhraseConversion,
    ProcessingJob,
    Setting,
    UnitConversion,
    UserAccount,
)
from text_evolver.fandoms import FandomName
from text_evolver.image_storage import ImageStorage, ImageStorageChanges, ImageStorageError
from text_evolver.web.i18n import translate
from text_evolver.web.validation_utils import (
    ValidationError,
    is_supported_document_filename,
    parse_bool,
    validate_image_row_token,
    validate_image_upload_size,
    validate_phrase_regex,
    validate_repeated_fields,
    validate_setting_text,
    validate_upload_filename,
)

ACTIVE_JOB_STATUSES = {"queued", "running"}
IMAGE_UPLOAD_FIELD_PREFIX = "image_files_"


@dataclass
class SettingData:
    """Representation of settings for frontend"""

    id: int
    owner_id: UUID
    name: str
    public: bool
    clean_empty: bool
    convert_to_utf: bool
    use_comma_separator: bool
    expect_feet: bool
    owner_name: str = ""
    fandoms: list[Fandom] = field(default_factory=list)
    unit_convs: list[UnitConversion] = field(default_factory=list)
    image_convs: list[ImageConversion] = field(default_factory=list)
    phrase_convs: list[PhraseConversion] = field(default_factory=list)


@dataclass
class UserView:
    """Representation of user for frontend"""

    id: UUID
    name: str
    set_limit: int
    settings: list[SettingData]
    waits: bool


def setting_data(session: Session, setting: Setting) -> SettingData:
    owner_name = session.scalar(select(UserAccount.username).where(UserAccount.id == setting.owner_id)) or ""
    return SettingData(
        id=setting.id,
        owner_id=setting.owner_id,
        name=setting.name,
        public=setting.public,
        clean_empty=setting.clean_empty,
        convert_to_utf=setting.convert_to_utf,
        use_comma_separator=setting.use_comma_separator,
        expect_feet=setting.expect_feet,
        owner_name=owner_name,
        fandoms=list(session.scalars(select(Fandom).where(Fandom.setting_id == setting.id).order_by(Fandom.id))),
        unit_convs=list(
            session.scalars(
                select(UnitConversion).where(UnitConversion.setting_id == setting.id).order_by(UnitConversion.id)
            )
        ),
        image_convs=list(
            session.scalars(
                select(ImageConversion).where(ImageConversion.setting_id == setting.id).order_by(ImageConversion.id)
            )
        ),
        phrase_convs=list(
            session.scalars(
                select(PhraseConversion).where(PhraseConversion.setting_id == setting.id).order_by(PhraseConversion.id)
            )
        ),
    )


def get_setting(session: Session, setting_id: int) -> SettingData | None:
    setting = session.get(Setting, setting_id)
    return setting_data(session, setting) if setting is not None else None


def list_user_settings(session: Session, user_id: UUID) -> list[SettingData]:
    settings = session.scalars(select(Setting).where(Setting.owner_id == user_id).order_by(Setting.id))
    return [setting_data(session, value) for value in settings]


def latest_job(session: Session, user_id: UUID) -> ProcessingJob | None:
    return session.scalar(
        select(ProcessingJob).where(ProcessingJob.user_id == user_id).order_by(ProcessingJob.created_at.desc()).limit(1)
    )


def lock_setting_jobs_for_deletion(session: Session, setting_id: int) -> list[ProcessingJob]:
    return list(session.scalars(select(ProcessingJob).where(ProcessingJob.setting_id == setting_id).with_for_update()))


def user_view(session: Session, user: UserAccount) -> UserView:
    job = latest_job(session, user.id)
    waits = job is not None and job.status == "queued"
    return UserView(user.id, user.username, user.setting_limit, list_user_settings(session, user.id), waits)


def create_default_setting(session: Session, user_id: UUID) -> Setting:
    setting = Setting(owner_id=user_id, name="New_setting")
    session.add(setting)
    session.flush()
    session.add(Fandom(setting_id=setting.id, name=FandomName.POKEMONS))
    session.flush()
    return setting


def copy_setting(
    session: Session,
    source_id: int,
    user_id: UUID,
    storage: ImageStorage,
    storage_changes: ImageStorageChanges,
) -> Setting:
    session.scalar(select(Setting.id).where(Setting.id == source_id).with_for_update(read=True))
    source = get_setting(session, source_id)
    if source is None:
        raise ValidationError(translate("Setting not found"))
    copied = Setting(
        owner_id=user_id,
        name=source.name,
        public=False,
        clean_empty=source.clean_empty,
        convert_to_utf=source.convert_to_utf,
        use_comma_separator=source.use_comma_separator,
        expect_feet=source.expect_feet,
    )
    session.add(copied)
    session.flush()
    session.add_all(
        [
            Fandom(
                setting_id=copied.id,
                name=value.name,
                active=value.active,
                separation=value.separation,
                support_value_1=value.support_value_1,
                support_value_2=value.support_value_2,
            )
            for value in source.fandoms
        ]
    )
    session.add_all(
        [
            UnitConversion(
                setting_id=copied.id,
                phrase_from=value.phrase_from,
                phrase_to=value.phrase_to,
                conversion=value.conversion,
                can_be_word=value.can_be_word,
            )
            for value in source.unit_convs
        ]
    )
    session.add_all(
        [
            PhraseConversion(
                setting_id=copied.id,
                phrase_from=value.phrase_from,
                phrase_to=value.phrase_to,
                direct=value.direct,
                mutations=value.mutations,
                regex=value.direct and value.regex,
            )
            for value in source.phrase_convs
        ]
    )
    source_files = _image_files_by_conversion(session, source_id)
    for value in source.image_convs:
        copied_conversion = ImageConversion(
            setting_id=copied.id,
            phrase=value.phrase,
            separation=value.separation,
            explanation=value.explanation,
            mutations=value.mutations,
        )
        session.add(copied_conversion)
        session.flush()
        for image_file in source_files[value.id]:
            object_key = storage.object_key(user_id, copied.id, image_file.object_key)
            try:
                storage.copy(image_file.object_key, object_key)
            except ImageStorageError as exc:
                raise ValidationError(translate("Unable to copy setting images")) from exc
            storage_changes.created_object(object_key)
            session.add(
                ImageConversionFile(
                    image_conversion_id=copied_conversion.id,
                    object_key=object_key,
                    size_bytes=image_file.size_bytes,
                    position=image_file.position,
                )
            )
    return copied


def search_settings(session: Session, user_id: UUID, phrase: str, page: int, page_size: int) -> dict[str, Any]:
    predicate = (Setting.public.is_(True) | (Setting.owner_id == user_id)) & Setting.name.ilike(f"%{phrase}%")
    found = session.scalar(select(func.count()).select_from(Setting).where(predicate)) or 0
    pages = max(1, (found + page_size - 1) // page_size)
    page = min(max(page, 1), pages)
    offset = (page - 1) * page_size
    rows = session.scalars(
        select(Setting).where(predicate).order_by(Setting.name, Setting.id).offset(offset).limit(page_size)
    )
    values = [setting_data(session, row) for row in rows]
    return {
        "phrase": phrase,
        "founded": found,
        "settings": values,
        "page": page,
        "offset": offset,
        "left_border": offset + 1 if found else 0,
        "right_border": min(offset + len(values), found),
        "left_available": page > 1,
        "right_available": page < pages,
    }


def _values(form: Any, key: str) -> list[Any]:
    return list(form.getlist(key))


def _aligned(form: Any, keys: list[str]) -> list[list[Any]]:
    """Extract repeated values and require the same number of values for every key."""
    values = [_values(form, key) for key in keys]
    validate_repeated_fields(values, keys)
    return values


@dataclass(frozen=True)
class RowChanges:
    preserved: tuple[Any, ...]
    removed: tuple[Any, ...]
    added: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ImageSubmission:
    phrase: str
    separation: int
    explanation: str
    mutations: bool
    existing_conversion_id: int | None
    uploads: tuple[UploadFile, ...]


def _calculate_row_changes(existing: list[Any], submitted: list[dict[str, Any]], fields: tuple[str, ...]) -> RowChanges:
    """Compare complete row values without attempting to pair edits with existing rows."""
    unmatched = list(existing)
    preserved: list[Any] = []
    added: list[dict[str, Any]] = []
    for values in submitted:
        submitted_key = tuple(values[field] for field in fields)
        match_index = next(
            (
                index
                for index, row in enumerate(unmatched)
                if tuple(getattr(row, field) for field in fields) == submitted_key
            ),
            None,
        )
        if match_index is None:
            added.append(values)
        else:
            preserved.append(unmatched.pop(match_index))
    return RowChanges(tuple(preserved), tuple(unmatched), tuple(added))


def _apply_relation_changes(session: Session, setting_id: int, model: type[Any], changes: RowChanges) -> None:
    for row in changes.removed:
        session.delete(row)
    session.add_all(model(setting_id=setting_id, **values) for values in changes.added)


def _parse_image_submissions(form: Any, existing_by_token: dict[str, ImageConversion]) -> list[ImageSubmission]:
    field_values = _aligned(
        form, ["image_token", "image_phrase", "image_separation", "image_expl", "image_mutations"]
    )
    tokens = field_values[0]
    phrases = field_values[1]
    separations = field_values[2]
    explanations = field_values[3]
    image_mutations = field_values[4]
    submissions: list[ImageSubmission] = []
    used_tokens: set[str] = set()
    for index, submitted_phrase in enumerate(phrases):
        phrase = validate_setting_text(submitted_phrase, translate("Image trigger phrase"), strip=True)
        if not phrase:
            continue
        token = str(tokens[index])
        validate_image_row_token(token, used_tokens)
        used_tokens.add(token)
        uploads = tuple(
            value
            for value in _values(form, IMAGE_UPLOAD_FIELD_PREFIX + token)
            if isinstance(value, UploadFile) and value.filename
        )
        existing = existing_by_token.get(token)
        if not uploads and existing is None:
            raise ValidationError(translate("Image conversion '%(value0)s' requires an image", value0=phrase))
        submissions.append(
            ImageSubmission(
                phrase=phrase,
                separation=max(1, int(separations[index])),
                explanation=validate_setting_text(explanations[index], translate("Image explanation")),
                mutations=parse_bool(image_mutations[index], "image_mutations"),
                existing_conversion_id=existing.id if existing is not None else None,
                uploads=uploads,
            )
        )
    return submissions


def _image_files_by_conversion(session: Session, setting_id: int) -> dict[int, list[ImageConversionFile]]:
    files = session.scalars(
        select(ImageConversionFile)
        .join(ImageConversion, ImageConversion.id == ImageConversionFile.image_conversion_id)
        .where(ImageConversion.setting_id == setting_id)
        .order_by(ImageConversionFile.image_conversion_id, ImageConversionFile.position)
    )
    grouped: defaultdict[int, list[ImageConversionFile]] = defaultdict(list)
    for image_file in files:
        grouped[image_file.image_conversion_id].append(image_file)
    return grouped


def setting_image_object_keys(session: Session, setting_id: int) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(ImageConversionFile.object_key)
            .join(ImageConversion, ImageConversion.id == ImageConversionFile.image_conversion_id)
            .where(ImageConversion.setting_id == setting_id)
        )
    )


def _upload_conversion_files(
    session: Session,
    setting: Setting,
    conversion: ImageConversion,
    uploads: tuple[UploadFile, ...],
    storage: ImageStorage,
    storage_changes: ImageStorageChanges,
) -> None:
    for position, upload in enumerate(uploads):
        object_key = storage.object_key(setting.owner_id, setting.id, upload.filename)
        try:
            storage.upload(upload.file, object_key)
        except ImageStorageError as exc:
            raise ValidationError(translate("Unable to store setting images")) from exc
        storage_changes.created_object(object_key)
        session.add(
            ImageConversionFile(
                image_conversion_id=conversion.id,
                object_key=object_key,
                size_bytes=validate_image_upload_size(upload.size, upload.filename),
                position=position,
            )
        )


async def update_setting_from_form(
    session: Session,
    setting_id: int,
    form: Any,
    storage: ImageStorage,
    storage_changes: ImageStorageChanges,
) -> None:
    setting = session.get(Setting, setting_id, with_for_update=True)
    if setting is None:
        raise ValidationError(translate("Setting not found"))
    active_job_id = session.scalar(
        select(ProcessingJob.id)
        .where(
            ProcessingJob.setting_id == setting_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .limit(1)
    )
    if active_job_id is not None:
        raise ValidationError(translate("Cannot update a setting while its job is queued or running"))
    name = validate_setting_text(form.get("set_name", ""), translate("Setting name"), strip=True, required=True)

    setting.name = name
    setting.public = parse_bool(form.get("set_public"), "set_public")
    setting.clean_empty = parse_bool(form.get("set_empty"), "set_empty")
    setting.convert_to_utf = parse_bool(form.get("set_utf"), "set_utf")
    setting.use_comma_separator = parse_bool(form.get("set_coma_sep"), "set_coma_sep")
    setting.expect_feet = parse_bool(form.get("set_expect_feet"), "set_expect_feet")

    existing_fandoms = list(session.scalars(select(Fandom).where(Fandom.setting_id == setting_id).order_by(Fandom.id)))
    existing_units = list(
        session.scalars(
            select(UnitConversion).where(UnitConversion.setting_id == setting_id).order_by(UnitConversion.id)
        )
    )
    existing_phrases = list(
        session.scalars(
            select(PhraseConversion).where(PhraseConversion.setting_id == setting_id).order_by(PhraseConversion.id)
        )
    )
    existing_images = list(
        session.scalars(
            select(ImageConversion).where(ImageConversion.setting_id == setting_id).order_by(ImageConversion.id)
        )
    )
    existing_images_by_token = {f"existing-{value.id}": value for value in existing_images}
    files_by_conversion = _image_files_by_conversion(session, setting_id)

    field_values = _aligned(
        form, ["fandom", "fandom_active", "fandom_separation", "fandom_value_1", "fandom_value_2"]
    )
    fandom = field_values[0]
    active = field_values[1]
    separation = field_values[2]
    value_1 = field_values[3]
    value_2 = field_values[4]
    submitted_fandoms: list[dict[str, Any]] = []
    for index, fandom_name in enumerate(fandom):
        submitted_fandoms.append(
            {
                "name": validate_setting_text(fandom_name, translate("Fandom name"), required=True),
                "active": parse_bool(active[index], "fandom_active"),
                "separation": max(1, int(separation[index])),
                "support_value_1": parse_bool(value_1[index], "fandom_value_1"),
                "support_value_2": parse_bool(value_2[index], "fandom_value_2"),
            }
        )

    field_values = _aligned(form, ["unit_from", "unit_to", "unit_convert", "unit_can"])
    unit_from = field_values[0]
    unit_to = field_values[1]
    conversion = field_values[2]
    can_be_word = field_values[3]
    submitted_units: list[dict[str, Any]] = []
    for index, phrase_from in enumerate(unit_from):
        phrase_from = validate_setting_text(phrase_from, translate("Unit source phrase"), strip=True)
        if phrase_from:
            submitted_units.append(
                {
                    "phrase_from": phrase_from,
                    "phrase_to": validate_setting_text(unit_to[index], translate("Unit replacement phrase")),
                    "conversion": float(conversion[index]),
                    "can_be_word": parse_bool(can_be_word[index], "unit_can"),
                }
            )

    field_values = _aligned(
        form, ["phrase_from", "phrase_to", "phrase_direct", "phrase_mutations", "phrase_regex"]
    )
    phrase_from = field_values[0]
    phrase_to = field_values[1]
    direct = field_values[2]
    mutations = field_values[3]
    regex_values = field_values[4]
    submitted_phrases: list[dict[str, Any]] = []
    for index, source in enumerate(phrase_from):
        source = validate_setting_text(source, translate("Phrase source"), strip=True)
        if source:
            direct_value = parse_bool(direct[index], "phrase_direct")
            regex_value = direct_value and parse_bool(regex_values[index], "phrase_regex")
            replacement = validate_setting_text(phrase_to[index], translate("Phrase replacement"))
            if regex_value:
                validate_phrase_regex(source, replacement)
            submitted_phrases.append(
                {
                    "phrase_from": source,
                    "phrase_to": replacement,
                    "direct": direct_value,
                    "mutations": parse_bool(mutations[index], "phrase_mutations"),
                    "regex": regex_value,
                }
            )

    image_submissions = _parse_image_submissions(form, existing_images_by_token)
    for submission in image_submissions:
        if (not submission.uploads and submission.existing_conversion_id is not None
                and not files_by_conversion[submission.existing_conversion_id]):
            raise ValidationError(translate("An image conversion has no stored images"))
        for upload in submission.uploads:
            validate_image_upload_size(upload.size, upload.filename)

    row_changes = (
        (
            Fandom,
            _calculate_row_changes(
                existing_fandoms,
                submitted_fandoms,
                ("name", "active", "separation", "support_value_1", "support_value_2"),
            ),
        ),
        (
            UnitConversion,
            _calculate_row_changes(
                existing_units,
                submitted_units,
                ("phrase_from", "phrase_to", "conversion", "can_be_word"),
            ),
        ),
        (
            PhraseConversion,
            _calculate_row_changes(
                existing_phrases,
                submitted_phrases,
                ("phrase_from", "phrase_to", "direct", "mutations", "regex"),
            ),
        ),
    )
    for model, changes in row_changes:
        _apply_relation_changes(session, setting_id, model, changes)

    retained_conversion_ids: set[int] = set()
    for submission in image_submissions:
        if submission.existing_conversion_id is None:
            conversion_row = ImageConversion(
                setting_id=setting_id,
                phrase=submission.phrase,
                separation=submission.separation,
                explanation=submission.explanation,
                mutations=submission.mutations,
            )
            session.add(conversion_row)
            session.flush()
        else:
            conversion_row = next(value for value in existing_images if value.id == submission.existing_conversion_id)
            retained_conversion_ids.add(conversion_row.id)
            conversion_row.phrase = submission.phrase
            conversion_row.separation = submission.separation
            conversion_row.explanation = submission.explanation
            conversion_row.mutations = submission.mutations
        if submission.uploads:
            for image_file in files_by_conversion[conversion_row.id]:
                storage_changes.obsolete_object(image_file.object_key)
                session.delete(image_file)
            session.flush()
            _upload_conversion_files(
                session,
                setting,
                conversion_row,
                submission.uploads,
                storage,
                storage_changes,
            )

    for conversion_row in existing_images:
        if conversion_row.id in retained_conversion_ids:
            continue
        for image_file in files_by_conversion[conversion_row.id]:
            storage_changes.obsolete_object(image_file.object_key)
            session.delete(image_file)
        session.delete(conversion_row)
    session.flush()


def job_paths(settings: AppSettings, job_id: int) -> tuple[Path, Path, Path]:
    root = settings.work_root / str(job_id)
    return root, root / "origin_files", root / "new_files"


def job_file_counts(settings: AppSettings, job: ProcessingJob | None) -> list[int]:
    if job is None:
        return [0, 0]
    paths = job_paths(settings, job.id)
    origin = paths[1]
    output = paths[2]
    total = sum(1 for value in origin.glob("*") if value.is_file()) if origin.exists() else 0
    completed = sum(1 for value in output.glob("*") if value.is_file()) if output.exists() else 0
    return [total, completed]


def _acquire_create_job_lock(session: Session, user_id: UUID) -> None:
    """Serialize job creation for one user until the surrounding transaction finishes."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:user_id, 0))"), {"user_id": str(user_id)}
    )


def _lock_setting_for_job(session: Session, setting_id: int) -> None:
    try:
        locked_setting_id = session.scalar(
            select(Setting.id).where(Setting.id == setting_id).with_for_update(read=True, nowait=True)
        )
    except OperationalError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise ValidationError(translate("Setting is currently being updated")) from exc
        raise
    if locked_setting_id is None:
        raise ValidationError(translate("Setting not found"))


async def create_job(
    session: Session,
    app_settings: AppSettings,
    user_id: UUID,
    setting_id: int,
    uploads: list[UploadFile],
) -> ProcessingJob:
    with session.begin_nested():
        _acquire_create_job_lock(session, user_id)
        return await _create_job_in_transaction(session, app_settings, user_id, setting_id, uploads)


async def _create_job_in_transaction(
    session: Session,
    app_settings: AppSettings,
    user_id: UUID,
    setting_id: int,
    uploads: list[UploadFile],
) -> ProcessingJob:
    _lock_setting_for_job(session, setting_id)
    old_jobs = list(
        session.scalars(
            select(ProcessingJob).where(
                ProcessingJob.user_id == user_id,
                ProcessingJob.status.in_({"failed", "cancelled"}),
            )
        )
    )
    if old_jobs:
        import shutil

        for old_job in old_jobs:
            paths = job_paths(app_settings, old_job.id)
            root = paths[0]
            shutil.rmtree(root, ignore_errors=True)
            session.delete(old_job)
        session.flush()
    active = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.user_id == user_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    completed = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.user_id == user_id,
            ProcessingJob.status == "completed",
        )
    )
    if active is not None:
        raise ValidationError(translate("There are still files being processed"))
    if completed is not None:
        raise ValidationError(translate("Download completed files before starting another job"))
    valid = [upload for upload in uploads if is_supported_document_filename(upload.filename)]
    if not valid:
        raise ValidationError(translate("Select at least one supported document"))
    job = ProcessingJob(user_id=user_id, setting_id=setting_id, status="queued")
    session.add(job)
    session.flush()
    paths = job_paths(app_settings, job.id)
    root = paths[0]
    origin = paths[1]
    output = paths[2]
    origin.mkdir(parents=True)
    output.mkdir(parents=True)
    total = 0
    try:
        used_names: set[str] = set()
        for upload in valid:
            filename = validate_upload_filename(upload.filename, used_names)
            used_names.add(filename)
            target = origin / filename
            with target.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > app_settings.upload_limit_bytes:
                        raise ValidationError(translate("Uploaded files exceed the configured size limit"))
                    destination.write(chunk)
    except Exception:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
        session.delete(job)
        raise
    return job


def request_job_cancellation(session: Session, user_id: UUID) -> ProcessingJob | None:
    job = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.user_id == user_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    if job is not None:
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = dt.datetime.now(dt.UTC)
        else:
            job.cancellation_requested = True
    return job
