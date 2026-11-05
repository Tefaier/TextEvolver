"""Persist validated setting submissions without parsing HTTP forms."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    ImageConversionFile,
    PhraseConversion,
    ProcessingJob,
    Setting,
    UnitConversion,
)
from text_evolver.image_storage import ImageStorage, ImageStorageChanges, ImageStorageError
from text_evolver.services import (
    ACTIVE_JOB_STATUSES,
    SettingSubmission,
    _image_files_by_conversion,
    ensure_setting_available,
)
from text_evolver.web.i18n import translate
from text_evolver.web.validation_utils import ValidationError, validate_image_upload_size, validate_setting_image_size


@dataclass(frozen=True)
class RowChanges:
    preserved: tuple[Any, ...]
    removed: tuple[Any, ...]
    added: tuple[dict[str, Any], ...]


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


def update_setting(
    session: Session,
    setting_id: int,
    data: SettingSubmission,
    storage: ImageStorage,
    storage_changes: ImageStorageChanges,
) -> None:
    setting = session.scalar(
        select(Setting).where(Setting.id == setting_id).with_for_update().execution_options(populate_existing=True)
    )
    if setting is None:
        raise ValidationError(translate("Setting not found"))
    ensure_setting_available(setting)
    active_job_id = session.scalar(
        select(ProcessingJob.id)
        .where(ProcessingJob.setting_id == setting_id, ProcessingJob.status.in_(ACTIVE_JOB_STATUSES))
        .limit(1)
    )
    if active_job_id is not None:
        raise ValidationError(translate("Cannot update a setting while its job is queued or running"))
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
    files_by_conversion = _image_files_by_conversion(session, setting_id)

    # Count exactly the files that will remain after this save. Submitted uploads
    # replace a conversion's files; omitted conversions are removed entirely.
    resulting_image_bytes = 0
    existing_ids = {image.id for image in existing_images}
    for submission in data.images:
        if submission.existing_conversion_id is not None and submission.existing_conversion_id not in existing_ids:
            raise ValidationError(translate("Image conversion row identifier is invalid"))
        if submission.uploads:
            resulting_image_bytes += sum(validate_image_upload_size(upload.size, upload.filename)
                                     for upload in submission.uploads)
        elif submission.existing_conversion_id is not None:
            retained_files = files_by_conversion[submission.existing_conversion_id]
            if not retained_files:
                raise ValidationError(translate("An image conversion has no stored images"))
            resulting_image_bytes += sum(image.size_bytes for image in retained_files)
    validate_setting_image_size(resulting_image_bytes)

    setting.name = data.name
    setting.public = data.public
    setting.clean_empty = data.clean_empty
    setting.convert_to_utf = data.convert_to_utf
    setting.use_comma_separator = data.use_comma_separator
    setting.expect_feet = data.expect_feet
    row_changes = (
        (
            Fandom,
            _calculate_row_changes(
                existing_fandoms,
                data.fandoms,
                ("name", "active", "separation", "support_value_1", "support_value_2"),
            ),
        ),
        (
            UnitConversion,
            _calculate_row_changes(
                existing_units,
                data.units,
                ("phrase_from", "phrase_to", "conversion", "can_be_word"),
            ),
        ),
        (
            PhraseConversion,
            _calculate_row_changes(
                existing_phrases,
                data.phrases,
                ("phrase_from", "phrase_to", "direct", "mutations", "regex"),
            ),
        ),
    )
    for model, changes in row_changes:
        _apply_relation_changes(session, setting_id, model, changes)

    retained_conversion_ids: set[int] = set()
    for submission in data.images:
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

