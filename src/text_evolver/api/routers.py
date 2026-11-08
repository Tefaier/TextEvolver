"""Versioned settings and task endpoints."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError as SchemaValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from text_evolver.api.routines import setting_view, submission_from_setting, task_view
from text_evolver.api.schemas import (
    DeletionView,
    ImageRule,
    ImageView,
    Page,
    SettingCreate,
    SettingPatch,
    SettingView,
    TaskView,
)
from text_evolver.api.tokens import api_user, bearer, require_write
from text_evolver.db.models import ImageConversion, ImageConversionFile, ProcessingJob, Setting, UserAccount
from text_evolver.db.session import get_db
from text_evolver.image_storage import ImageStorage, get_image_storage
from text_evolver.services import (
    ACTIVE_JOB_STATUSES,
    ImageSubmission,
    SettingSubmission,
    copy_setting,
    create_job,
    request_job_cancellation,
)
from text_evolver.setting_service import authorize_setting, create_owned_setting, update_setting
from text_evolver.task_service import consume_result
from text_evolver.transactions import commit, setting_changes
from text_evolver.web.routines.accounts import lock_user
from text_evolver.web.validation_utils import ConflictError

router = APIRouter(dependencies=[Depends(bearer)])
DB = Annotated[Session, Depends(get_db)]
User = Annotated[UserAccount, Depends(api_user)]
Storage = Annotated[ImageStorage, Depends(get_image_storage)]
WRITE = [Depends(require_write)]


def pagination(request, page_size):
    return page_size if page_size is not None else request.app.state.settings.results_per_page


@router.get("/settings", response_model=Page[SettingView], tags=["Settings"])
def list_settings(
    request: Request, db: DB, user: User, scope: Literal["own", "accessible"] = "own", q: str = "",
    page: int = Query(1, ge=1), page_size: int | None = Query(None, ge=1, le=200),
):
    size = pagination(request, page_size)
    predicate = Setting.owner_id == user.id
    if scope == "accessible":
        predicate = predicate | (Setting.public.is_(True) & Setting.deletion_requested.is_(False))
    predicate = predicate & Setting.name.ilike(f"%{q}%")
    total = db.scalar(select(func.count()).select_from(Setting).where(predicate)) or 0
    ids = db.scalars(select(Setting.id).where(predicate).order_by(Setting.name, Setting.id)
                     .offset((page - 1) * size).limit(size))
    return Page[SettingView](items=[setting_view(db, value) for value in ids], total=total, page=page, page_size=size)


@router.get("/settings/{setting_id}", response_model=SettingView, tags=["Settings"])
def read_setting(setting_id: int, db: DB, user: User):
    authorize_setting(db, setting_id, user.id)
    return setting_view(db, setting_id)


@router.post("/settings", response_model=SettingView, status_code=201, dependencies=WRITE, tags=["Settings"])
def add_setting(data: SettingCreate, db: DB, user: User, storage: Storage):
    with setting_changes(db, storage) as changes:
        setting = create_owned_setting(db, user)
        setting_id = setting.id
        submission = SettingSubmission(**data.model_dump(), images=[])
        update_setting(db, setting_id, submission, storage, changes)
        result = setting_view(db, setting_id)
    return result


@router.patch("/settings/{setting_id}", response_model=SettingView, dependencies=WRITE, tags=["Settings"])
def edit_setting(setting_id: int, data: SettingPatch, db: DB, user: User, storage: Storage):
    with setting_changes(db, storage) as changes:
        authorize_setting(db, setting_id, user.id, write=True, lock=True)
        submission = replace(submission_from_setting(db, setting_id), **data.model_dump(exclude_unset=True))
        update_setting(db, setting_id, submission, storage, changes)
        result = setting_view(db, setting_id)
    return result


@router.post("/settings/{setting_id}/copy", response_model=SettingView, status_code=201,
             dependencies=WRITE, tags=["Settings"])
def duplicate_setting(setting_id: int, db: DB, user: User, storage: Storage):
    with setting_changes(db, storage) as changes:
        user = lock_user(db, user.id)
        source = authorize_setting(db, setting_id, user.id)
        if source.owner_id == user.id:
            raise ConflictError("You already own this setting")
        count = db.scalar(select(func.count()).select_from(Setting).where(Setting.owner_id == user.id)) or 0
        if count >= user.setting_limit:
            raise ConflictError("Reached the allowed settings limit")
        copied = copy_setting(db, setting_id, user.id, storage, changes)
        result = setting_view(db, copied.id)
    return result


@router.delete("/settings/{setting_id}", response_model=DeletionView, status_code=202,
               dependencies=WRITE, tags=["Settings"])
def delete_setting(setting_id: int, db: DB, user: User):
    setting = authorize_setting(db, setting_id, user.id, write=True, lock=True)
    setting.deletion_requested = True
    commit(db)
    return DeletionView()


def parse_image_metadata(metadata):
    try:
        return ImageRule.model_validate_json(metadata)
    except SchemaValidationError:
        # Do not reflect user input or upload content in error responses.
        raise HTTPException(422, "Invalid image metadata") from None


def save_image(setting_id, image_id, metadata, files, db, user, storage):
    rule = parse_image_metadata(metadata)
    with setting_changes(db, storage) as changes:
        authorize_setting(db, setting_id, user.id, write=True, lock=True)
        submission = submission_from_setting(db, setting_id)
        existing_ids = {image.existing_conversion_id for image in submission.images}
        if image_id is not None and image_id not in existing_ids:
            raise HTTPException(404, "Image rule not found")
        images = [image for image in submission.images if image.existing_conversion_id != image_id]
        images.append(ImageSubmission(**rule.model_dump(), existing_conversion_id=image_id, uploads=tuple(files)))
        update_setting(db, setting_id, replace(submission, images=images), storage, changes)
        views = setting_view(db, setting_id).images
        result = next(image for image in views if image.id == image_id) if image_id is not None else views[-1]
    return result


@router.post("/settings/{setting_id}/images", response_model=ImageView, status_code=201,
             dependencies=WRITE, tags=["Images"])
def add_image(setting_id: int, db: DB, user: User, storage: Storage,
              metadata: Annotated[str, Form()], files: Annotated[list[UploadFile], File()]):
    return save_image(setting_id, None, metadata, files, db, user, storage)


@router.put("/settings/{setting_id}/images/{image_id}", response_model=ImageView,
            dependencies=WRITE, tags=["Images"])
def edit_image(setting_id: int, image_id: int, db: DB, user: User, storage: Storage,
               metadata: Annotated[str, Form()], files: Annotated[list[UploadFile] | None, File()] = None):
    return save_image(setting_id, image_id, metadata, files or [], db, user, storage)


@router.delete("/settings/{setting_id}/images/{image_id}", status_code=204,
               dependencies=WRITE, tags=["Images"])
def delete_image(setting_id: int, image_id: int, db: DB, user: User, storage: Storage):
    with setting_changes(db, storage) as changes:
        authorize_setting(db, setting_id, user.id, write=True, lock=True)
        submission = submission_from_setting(db, setting_id)
        images = [image for image in submission.images if image.existing_conversion_id != image_id]
        if len(images) == len(submission.images):
            raise HTTPException(404, "Image rule not found")
        update_setting(db, setting_id, replace(submission, images=images), storage, changes)
    return Response(status_code=204)


@router.get("/settings/{setting_id}/images/{image_id}/files/{file_id}", tags=["Images"],
            response_class=FileResponse)
def read_image_file(setting_id: int, image_id: int, file_id: int, request: Request,
                    db: DB, user: User, storage: Storage):
    authorize_setting(db, setting_id, user.id)
    image_file = db.scalar(select(ImageConversionFile).join(ImageConversion)
                           .where(ImageConversion.setting_id == setting_id, ImageConversion.id == image_id,
                                  ImageConversionFile.id == file_id))
    if image_file is None:
        raise HTTPException(404, "Image file not found")
    temporary = TemporaryDirectory(prefix="api-image-", dir=request.app.state.settings.temp_root)
    destination = Path(temporary.name) / "image"
    try:
        storage.download(image_file.object_key, destination)
    except BaseException:
        temporary.cleanup()
        raise
    return FileResponse(destination, media_type="application/octet-stream", filename=f"image-{file_id}",
                        background=BackgroundTask(temporary.cleanup))


@router.get("/tasks", response_model=Page[TaskView], tags=["Tasks"])
def list_tasks(request: Request, db: DB, user: User, page: int = Query(1, ge=1),
               page_size: int | None = Query(None, ge=1, le=200)):
    size = pagination(request, page_size)
    predicate = ProcessingJob.user_id == user.id
    total = db.scalar(select(func.count()).select_from(ProcessingJob).where(predicate)) or 0
    jobs = db.scalars(select(ProcessingJob).where(predicate).order_by(ProcessingJob.created_at.desc(),
                      ProcessingJob.id.desc()).offset((page - 1) * size).limit(size))
    return Page[TaskView](items=[task_view(request.app.state.settings, job) for job in jobs],
                          total=total, page=page, page_size=size)


@router.post("/tasks", response_model=TaskView, status_code=202, dependencies=WRITE, tags=["Tasks"])
async def submit_task(request: Request, response: Response, db: DB, user: User,
                      setting_id: Annotated[int, Form()], files: Annotated[list[UploadFile], File()]):
    authorize_setting(db, setting_id, user.id)
    job = await create_job(db, request.app.state.settings, user.id, setting_id, files)
    result = task_view(request.app.state.settings, job)
    commit(db)
    response.headers["Location"] = str(request.url_for("read_task", task_id=result.id))
    return result


def owned_task(db, user_id, task_id, *, lock=False):
    query = select(ProcessingJob).where(ProcessingJob.id == task_id, ProcessingJob.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    job = db.scalar(query)
    if job is None:
        raise HTTPException(404, "Task not found")
    return job


@router.get("/tasks/{task_id}", response_model=TaskView, tags=["Tasks"])
def read_task(task_id: int, request: Request, db: DB, user: User):
    return task_view(request.app.state.settings, owned_task(db, user.id, task_id))


@router.post("/tasks/{task_id}/cancel", response_model=TaskView, dependencies=WRITE, tags=["Tasks"])
def cancel_task(task_id: int, request: Request, db: DB, user: User):
    job = owned_task(db, user.id, task_id, lock=True)
    if job.status not in ACTIVE_JOB_STATUSES:
        raise ConflictError("Task is already terminal")
    request_job_cancellation(db, user.id, job_id=task_id)
    result = task_view(request.app.state.settings, job)
    commit(db)
    return result


@router.post("/tasks/{task_id}/download", tags=["Tasks"], response_class=StreamingResponse,
             responses={200: {"content": {"application/zip": {}}, "description": "ZIP; consumes the task and result"}})
def download_task(task_id: int, request: Request, db: DB, user: User):
    return consume_result(db, request.app.state.settings, user.id, task_id)
