from __future__ import annotations

import base64
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from text_evolver.config import AppSettings
from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    PhraseConversion,
    ProcessingJob,
    Setting,
    UnitConversion,
    UserAccount,
)
from text_evolver.fandoms import FandomName

ALLOWED_EXTENSIONS = {"docx", "epub", "html", "fb2"}
ACTIVE_JOB_STATUSES = {"queued", "running"}
TRUTHY = {"true", "1", "yes", "on"}
FALSY = {"false", "0", "no", "off"}


class ValidationError(ValueError):
    pass


@dataclass
class SettingData:
    id: int
    owner_id: int
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

    @property
    def use_coma_sep(self) -> bool:  # compatibility with the existing template
        return self.use_comma_separator

    @property
    def user(self) -> SimpleNamespace:
        return SimpleNamespace(name=self.owner_name)


@dataclass
class UserView:
    id: int
    name: str
    set_limit: int
    settings: list[SettingData]
    thread: list[SimpleNamespace]


def parse_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    raise ValidationError(f"Invalid boolean value for {field_name}")


def validate_credentials(username: str, password: str, confirm: str | None = None) -> None:
    if not 4 <= len(username) <= 62:
        raise ValidationError("Username must contain between 4 and 62 characters")
    if not 4 <= len(password) <= 128:
        raise ValidationError("Password must contain between 4 and 128 characters")
    if confirm is not None and confirm != password:
        raise ValidationError("Password confirmation does not match")


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


def list_user_settings(session: Session, user_id: int) -> list[SettingData]:
    settings = session.scalars(select(Setting).where(Setting.owner_id == user_id).order_by(Setting.id))
    return [setting_data(session, value) for value in settings]


def latest_job(session: Session, user_id: int) -> ProcessingJob | None:
    return session.scalar(
        select(ProcessingJob).where(ProcessingJob.user_id == user_id).order_by(ProcessingJob.created_at.desc()).limit(1)
    )


def user_view(session: Session, user: UserAccount) -> UserView:
    job = latest_job(session, user.id)
    thread = []
    if job is not None and job.status in ACTIVE_JOB_STATUSES:
        thread = [SimpleNamespace(waits=job.status == "queued")]
    return UserView(user.id, user.username, user.setting_limit, list_user_settings(session, user.id), thread)


def create_default_setting(session: Session, user_id: int) -> Setting:
    setting = Setting(owner_id=user_id, name="New_setting")
    session.add(setting)
    session.flush()
    session.add(Fandom(setting_id=setting.id, name=FandomName.POKEMONS))
    session.flush()
    return setting


def copy_setting(session: Session, source_id: int, user_id: int) -> Setting:
    source = get_setting(session, source_id)
    if source is None:
        raise ValidationError("Setting not found")
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
            )
            for value in source.phrase_convs
        ]
    )
    session.add_all(
        [
            ImageConversion(
                setting_id=copied.id,
                phrase=value.phrase,
                separation=value.separation,
                explanation=value.explanation,
                mutations=value.mutations,
                images=value.images,
            )
            for value in source.image_convs
        ]
    )
    return copied


def search_settings(session: Session, phrase: str, page: int, page_size: int) -> dict[str, Any]:
    predicate = Setting.public.is_(True) & Setting.name.ilike(f"%{phrase}%")
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
    values = [_values(form, key) for key in keys]
    if len({len(value) for value in values}) > 1:
        raise ValidationError(f"Mismatched repeated fields: {', '.join(keys)}")
    return values


async def update_setting_from_form(
    session: Session, setting_id: int, form: Any, settings: AppSettings
) -> None:
    setting = session.get(Setting, setting_id)
    if setting is None:
        raise ValidationError("Setting not found")
    name = str(form.get("set_name", "")).strip()
    if not name or len(name) > 64:
        raise ValidationError("Setting name must contain between 1 and 64 characters")

    setting.name = name
    setting.public = parse_bool(form.get("set_public"), "set_public")
    setting.clean_empty = parse_bool(form.get("set_empty"), "set_empty")
    setting.convert_to_utf = parse_bool(form.get("set_utf"), "set_utf")
    setting.use_comma_separator = parse_bool(form.get("set_coma_sep"), "set_coma_sep")
    setting.expect_feet = parse_bool(form.get("set_expect_feet"), "set_expect_feet")

    old_images = {
        value.phrase: value.images
        for value in session.scalars(select(ImageConversion).where(ImageConversion.setting_id == setting_id))
    }
    for model in (Fandom, UnitConversion, PhraseConversion, ImageConversion):
        session.execute(delete(model).where(model.setting_id == setting_id))

    fandom, active, separation, value_1, value_2 = _aligned(
        form, ["fandom", "fandom_active", "fandom_separation", "fandom_value_1", "fandom_value_2"]
    )
    for index, fandom_name in enumerate(fandom):
        session.add(
            Fandom(
                setting_id=setting_id,
                name=str(fandom_name),
                active=parse_bool(active[index], "fandom_active"),
                separation=max(1, int(separation[index])),
                support_value_1=parse_bool(value_1[index], "fandom_value_1"),
                support_value_2=parse_bool(value_2[index], "fandom_value_2"),
            )
        )

    unit_from, unit_to, conversion, can_be_word = _aligned(
        form, ["unit_from", "unit_to", "unit_convert", "unit_can"]
    )
    for index, phrase_from in enumerate(unit_from):
        phrase_from = str(phrase_from).strip()
        if phrase_from:
            session.add(
                UnitConversion(
                    setting_id=setting_id,
                    phrase_from=phrase_from,
                    phrase_to=str(unit_to[index]),
                    conversion=float(conversion[index]),
                    can_be_word=parse_bool(can_be_word[index], "unit_can"),
                )
            )

    phrase_from, phrase_to, direct, mutations = _aligned(
        form, ["phrase_from", "phrase_to", "phrase_direct", "phrase_mutations"]
    )
    for index, source in enumerate(phrase_from):
        source = str(source).strip()
        if source:
            session.add(
                PhraseConversion(
                    setting_id=setting_id,
                    phrase_from=source,
                    phrase_to=str(phrase_to[index]),
                    direct=parse_bool(direct[index], "phrase_direct"),
                    mutations=parse_bool(mutations[index], "phrase_mutations"),
                )
            )

    origins, phrases, separations, explanations, image_mutations = _aligned(
        form, ["image_origin_name", "image_phrase", "image_separation", "image_expl", "image_mutations"]
    )
    uploads = [value for value in _values(form, "image_files") if isinstance(value, UploadFile) and value.filename]
    upload_index = 0
    total_image_bytes = 0
    for index, phrase in enumerate(phrases):
        phrase = str(phrase).strip()
        if not phrase:
            continue
        images = old_images.get(str(origins[index]), "")
        if upload_index < len(uploads):
            data = await uploads[upload_index].read()
            upload_index += 1
            total_image_bytes += len(data)
            images = base64.b64encode(data).decode("ascii")
        if not images:
            raise ValidationError(f"Image conversion '{phrase}' requires an image")
        session.add(
            ImageConversion(
                setting_id=setting_id,
                phrase=phrase,
                separation=max(1, int(separations[index])),
                explanation=str(explanations[index]),
                mutations=parse_bool(image_mutations[index], "image_mutations"),
                images=images,
            )
        )
    if total_image_bytes > settings.setting_limit_bytes:
        raise ValidationError("Images exceed the configured setting size limit")
    session.flush()


def job_paths(settings: AppSettings, job_id: int) -> tuple[Path, Path, Path]:
    root = settings.work_root / str(job_id)
    return root, root / "origin_files", root / "new_files"


def job_file_counts(settings: AppSettings, job: ProcessingJob | None) -> list[int]:
    if job is None:
        return [0, 0]
    _, origin, output = job_paths(settings, job.id)
    pending = sum(1 for value in origin.glob("*") if value.is_file()) if origin.exists() else 0
    completed = sum(1 for value in output.glob("*") if value.is_file()) if output.exists() else 0
    return [pending, completed]


async def create_job(
    session: Session,
    app_settings: AppSettings,
    user_id: int,
    setting_id: int,
    uploads: list[UploadFile],
) -> ProcessingJob:
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
            root, _, _ = job_paths(app_settings, old_job.id)
            shutil.rmtree(root, ignore_errors=True)
            session.delete(old_job)
        session.flush()
    active = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.user_id == user_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    )
    ready = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.user_id == user_id,
            ProcessingJob.status == "completed",
        )
    )
    if active is not None:
        raise ValidationError("There are still files being processed")
    if ready is not None:
        raise ValidationError("Download completed files before starting another job")
    valid = [
        upload
        for upload in uploads
        if upload.filename and upload.filename.rsplit(".", 1)[-1].lower() in ALLOWED_EXTENSIONS
    ]
    if not valid:
        raise ValidationError("Select at least one supported document")
    job = ProcessingJob(user_id=user_id, setting_id=setting_id, status="queued")
    session.add(job)
    session.flush()
    root, origin, output = job_paths(app_settings, job.id)
    origin.mkdir(parents=True)
    output.mkdir(parents=True)
    total = 0
    try:
        used_names: set[str] = set()
        for upload in valid:
            filename = Path(upload.filename or "").name
            if not filename or filename in used_names:
                raise ValidationError("Uploaded filenames must be unique")
            used_names.add(filename)
            target = origin / filename
            with target.open("wb") as destination:
                while chunk := await upload.read(1024 * 1024):
                    total += len(chunk)
                    if total > app_settings.upload_limit_bytes:
                        raise ValidationError("Uploaded files exceed the configured size limit")
                    destination.write(chunk)
    except Exception:
        import shutil

        shutil.rmtree(root, ignore_errors=True)
        session.delete(job)
        raise
    return job


def request_job_cancellation(session: Session, user_id: int) -> ProcessingJob | None:
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
