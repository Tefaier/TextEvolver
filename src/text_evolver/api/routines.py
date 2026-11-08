"""Adapt explicit API schemas to shared processing services."""

from text_evolver.api.schemas import FandomRule, ImageFileView, ImageView, PhraseRule, SettingView, TaskView, UnitRule
from text_evolver.services import (
    ImageSubmission,
    SettingSubmission,
    _image_files_by_conversion,
    get_setting,
    job_file_counts,
)


def task_view(settings, job) -> TaskView:
    counts = job_file_counts(settings, job)
    return TaskView(
        id=job.id, setting_id=job.setting_id, status=job.status,
        cancellation_requested=job.cancellation_requested, created_at=job.created_at,
        started_at=job.started_at, finished_at=job.finished_at,
        total_files=max(counts), completed_files=counts[1],
        can_download=job.status == "completed" and counts[1] > 0,
    )


def submission_from_setting(db, setting_id):
    data = get_setting(db, setting_id)
    return SettingSubmission(
        name=data.name, public=data.public, clean_empty=data.clean_empty,
        convert_to_utf=data.convert_to_utf, use_comma_separator=data.use_comma_separator,
        expect_feet=data.expect_feet,
        fandoms=[FandomRule.model_validate(row).model_dump() for row in data.fandoms],
        units=[UnitRule.model_validate(row).model_dump() for row in data.unit_convs],
        phrases=[PhraseRule.model_validate(row).model_dump() for row in data.phrase_convs],
        images=[ImageSubmission(
            phrase=row.phrase, separation=row.separation, explanation=row.explanation,
            mutations=row.mutations, existing_conversion_id=row.id, uploads=(),
        ) for row in data.image_convs],
    )


def setting_view(db, setting_id):
    data = get_setting(db, setting_id)
    files = _image_files_by_conversion(db, setting_id)
    submission = submission_from_setting(db, setting_id)
    return SettingView(
        id=data.id, owner_id=data.owner_id, owner_name=data.owner_name,
        deletion_requested=data.deletion_requested,
        name=data.name, public=data.public, clean_empty=data.clean_empty,
        convert_to_utf=data.convert_to_utf, use_comma_separator=data.use_comma_separator,
        expect_feet=data.expect_feet, fandoms=submission.fandoms, units=submission.units,
        phrases=submission.phrases,
        images=[ImageView(
            id=row.id, phrase=row.phrase, separation=row.separation, explanation=row.explanation,
            mutations=row.mutations, files=[ImageFileView.model_validate(item) for item in files[row.id]],
        ) for row in data.image_convs],
    )
