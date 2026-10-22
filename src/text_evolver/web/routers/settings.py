from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import Setting
from text_evolver.db.session import get_db
from text_evolver.image_storage import ImageStorage, ImageStorageChanges, get_image_storage
from text_evolver.services import (
    copy_setting,
    create_default_setting,
    create_job,
    get_setting,
    job_paths,
    list_user_settings,
    lock_setting_jobs_for_deletion,
    search_settings,
    setting_image_object_keys,
    update_setting_from_form,
)
from text_evolver.web.auth import require_user
from text_evolver.web.i18n import translate
from text_evolver.web.routines.accounts import lock_user
from text_evolver.web.routines.flashes import flash
from text_evolver.web.routines.rendering import navigation_context, render
from text_evolver.web.routines.responses import accepts_json, form_redirect, validation_error_response
from text_evolver.web.validation_utils import ALLOWED_EXTENSIONS, ValidationError

router = APIRouter()


@router.get("/my_settings", response_class=HTMLResponse, name="my_settings")
def my_settings(
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
) -> Response:
    user = require_user(request, session)
    return render(
        request,
        "all_settings.html",
        **navigation_context(session, user, app_settings),
        limit=user.setting_limit,
    )


@router.post("/add_set", name="add_set")
async def add_set(request: Request, session: Session = Depends(get_db)) -> Response:
    user = require_user(request, session)
    lock_user(session, user.id)
    count = len(list_user_settings(session, user.id))
    if count >= user.setting_limit:
        if accepts_json(request):
            return JSONResponse(
                {"message": translate("Reached the allowed settings limit"), "category": "error"},
                status_code=status.HTTP_409_CONFLICT,
            )
        flash(request, "Reached the allowed settings limit")
    else:
        create_default_setting(session, user.id)
    return form_redirect(request, "my_settings")


@router.post("/delete_set/{setting_id}", name="delete_set")
async def delete_set(
    setting_id: int,
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
    image_storage: ImageStorage = Depends(get_image_storage),
) -> Response:
    user = require_user(request, session)
    setting = session.scalar(
        select(Setting).where(Setting.id == setting_id, Setting.owner_id == user.id).with_for_update()
    )
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    jobs = lock_setting_jobs_for_deletion(session, setting_id)
    if any(job.status == "running" for job in jobs):
        raise HTTPException(status_code=409, detail="Setting is used by a running job")
    storage_changes = ImageStorageChanges(image_storage)
    for object_key in setting_image_object_keys(session, setting_id):
        storage_changes.obsolete_object(object_key)
    for job in jobs:
        session.delete(job)
    session.execute(delete(Setting).where(Setting.id == setting_id))
    try:
        session.commit()
    except Exception:
        session.rollback()
        storage_changes.database_rolled_back()
        raise
    for job in jobs:
        paths = job_paths(app_settings, job.id)
        root = paths[0]
        shutil.rmtree(root, ignore_errors=True)
    storage_changes.database_committed()
    return Response(status_code=204)


@router.post("/search_req", name="search_req")
async def search_req(request: Request, session: Session = Depends(get_db)) -> Response:
    require_user(request, session)
    form = await request.form()
    phrase = str(form.get("search", "")).strip()
    destination = request.url_for("search").include_query_params(phrase=phrase, page=1)
    if accepts_json(request):
        return JSONResponse({"redirect": str(destination)})
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/search", response_class=HTMLResponse, name="search")
def search(
    request: Request,
    phrase: str = "",
    page: int = 1,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
) -> Response:
    user = require_user(request, session)
    results = search_settings(session, user.id, phrase, page, app_settings.results_per_page)
    return render(
        request,
        "search.html",
        **navigation_context(session, user, app_settings),
        results=results,
    )


@router.get("/setting/{setting_id}", response_class=HTMLResponse, name="setting")
def setting_page(
    setting_id: int,
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
) -> Response:
    user = require_user(request, session)
    setting = get_setting(session, setting_id)
    if setting is None or (not setting.public and setting.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Setting not found")
    return render(
        request,
        "setting.html",
        **navigation_context(session, user, app_settings),
        setting=setting,
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
    )


@router.post("/setting/{setting_id}", name="setting_submit")
async def setting_submit(
    setting_id: int,
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
    image_storage: ImageStorage = Depends(get_image_storage),
) -> Response:
    user = require_user(request, session)
    form = await request.form()
    setting = get_setting(session, setting_id)
    if setting is None or (not setting.public and setting.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Setting not found")
    storage_changes = ImageStorageChanges(image_storage)
    database_committed = False
    try:
        with session.begin_nested():
            if "copy" in form:
                if setting.owner_id == user.id:
                    raise ValidationError("You already own this setting")
                if len(list_user_settings(session, user.id)) >= user.setting_limit:
                    raise ValidationError("Reached the allowed settings limit")
                copy_setting(session, setting_id, user.id, image_storage, storage_changes)
            else:
                owns_setting = setting.owner_id == user.id
                if ("save" in form or "save_run" in form) and not owns_setting:
                    raise HTTPException(status_code=403, detail="Only the owner can edit a setting")
                if "save" in form or "save_run" in form:
                    await update_setting_from_form(
                        session,
                        setting_id,
                        form,
                        image_storage,
                        storage_changes,
                    )
                    flash(
                        request,
                        translate('Setting change successful with name "%(name)s"', name=form.get("set_name")),
                        "success",
                    )
                if "run" in form or "save_run" in form:
                    uploads = [value for value in form.getlist("Process_files") if isinstance(value, UploadFile)]
                    await create_job(session, app_settings, user.id, setting_id, uploads)
        session.commit()
        storage_changes.database_committed()
        database_committed = True
        return form_redirect(request, "my_settings")
    except ValidationError as exc:
        session.rollback()
        storage_changes.database_rolled_back()
        refreshed = get_setting(session, setting_id) or setting
        return validation_error_response(
            request,
            str(exc),
            "setting.html",
            **navigation_context(session, user, app_settings),
            setting=refreshed,
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        )
    except Exception:
        if not database_committed:
            session.rollback()
            storage_changes.database_rolled_back()
        raise
