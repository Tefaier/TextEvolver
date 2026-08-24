from __future__ import annotations

import datetime as dt
import io
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from text_evolver.config import AppSettings, get_settings
from text_evolver.db.models import ProcessingJob, Setting, UserAccount
from text_evolver.db.session import get_db
from text_evolver.services import (
    ALLOWED_EXTENSIONS,
    ValidationError,
    copy_setting,
    create_default_setting,
    create_job,
    get_setting,
    job_file_counts,
    job_paths,
    latest_job,
    list_user_settings,
    request_job_cancellation,
    search_settings,
    update_setting_from_form,
    user_view,
    validate_credentials,
)
from text_evolver.web.auth import (
    csrf_token,
    current_user,
    flash,
    hash_password,
    login,
    logout,
    pop_flashes,
    require_user,
    validate_csrf,
    verify_password,
)

router = APIRouter()


def template_context(request: Request, **values: object) -> dict[str, object]:
    return {
        "request": request,
        "csrf_token": csrf_token(request),
        "get_flashed_messages": lambda with_categories=True: pop_flashes(request),
        **values,
    }


def render(request: Request, name: str, **values: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name=name,
        context=template_context(request, **values),
    )


def redirect(request: Request, endpoint: str, **params: object) -> RedirectResponse:
    return RedirectResponse(request.url_for(endpoint, **params), status_code=status.HTTP_303_SEE_OTHER)


def authenticated(request: Request, session: Session) -> UserAccount:
    return require_user(request, session)


def navigation_context(session: Session, user: UserAccount, app_settings: AppSettings) -> dict[str, object]:
    job = latest_job(session, user.id)
    return {
        "user": user_view(session, user),
        "files_situation": job_file_counts(app_settings, job),
        "job": job,
    }


@router.get("/health/live", name="health_live")
def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", name="health_ready")
def health_ready(session: Session = Depends(get_db)) -> dict[str, str]:
    session.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/", response_class=HTMLResponse, name="index")
@router.get("/login", response_class=HTMLResponse, name="login_page")
def login_page(request: Request, session: Session = Depends(get_db)) -> Response:
    if current_user(request, session) is not None:
        return redirect(request, "my_settings")
    return render(request, "loging.html")


@router.post("/login", name="login")
async def login_submit(request: Request, session: Session = Depends(get_db)) -> Response:
    await validate_csrf(request)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    user = session.scalar(select(UserAccount).where(UserAccount.username == username))
    if user is None or not verify_password(password, user.password_hash):
        flash(request, "User with these username and password does not exist")
        return render(request, "loging.html" if user is None else "loging.html")
    user.last_entry = dt.datetime.now(dt.UTC)
    login(request, user)
    flash(request, f"You successfully logged into account {user.username}", "success")
    return redirect(request, "my_settings")


@router.get("/register", response_class=HTMLResponse, name="register_page")
def register_page(request: Request, session: Session = Depends(get_db)) -> Response:
    if current_user(request, session) is not None:
        return redirect(request, "my_settings")
    return render(request, "register.html")


@router.post("/register", name="register")
async def register_submit(request: Request, session: Session = Depends(get_db)) -> Response:
    await validate_csrf(request)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))
    try:
        validate_credentials(username, password, confirm)
        if session.scalar(select(UserAccount.id).where(UserAccount.username == username)) is not None:
            raise ValidationError("Username is already registered")
        user = UserAccount(username=username, password_hash=hash_password(password))
        session.add(user)
        session.flush()
    except ValidationError as exc:
        flash(request, str(exc))
        return render(request, "register.html")
    login(request, user)
    flash(request, f"You successfully registered account {user.username}", "success")
    return redirect(request, "my_settings")


@router.post("/logout", name="logout")
async def logout_submit(request: Request, session: Session = Depends(get_db)) -> Response:
    require_user(request, session)
    await validate_csrf(request)
    logout(request)
    return redirect(request, "login_page")


@router.get("/my_settings", response_class=HTMLResponse, name="my_settings")
def my_settings(
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_settings),
) -> Response:
    user = authenticated(request, session)
    return render(
        request,
        "all_settings.html",
        **navigation_context(session, user, app_settings),
        limit=user.setting_limit,
    )


@router.post("/add_set", name="add_set")
async def add_set(request: Request, session: Session = Depends(get_db)) -> Response:
    user = authenticated(request, session)
    await validate_csrf(request)
    count = len(list_user_settings(session, user.id))
    if count >= user.setting_limit:
        flash(request, "Reached the allowed settings limit")
    else:
        create_default_setting(session, user.id)
    return redirect(request, "my_settings")


@router.post("/delete_set/{setting_id}", name="delete_set")
async def delete_set(setting_id: int, request: Request, session: Session = Depends(get_db)) -> Response:
    user = authenticated(request, session)
    await validate_csrf(request)
    setting = session.scalar(select(Setting).where(Setting.id == setting_id, Setting.owner_id == user.id))
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    referenced = session.scalar(select(ProcessingJob.id).where(ProcessingJob.setting_id == setting_id).limit(1))
    if referenced is not None:
        raise HTTPException(status_code=409, detail="Setting is referenced by a processing job")
    session.delete(setting)
    return Response(status_code=204)


@router.post("/search_req", name="search_req")
async def search_req(request: Request, session: Session = Depends(get_db)) -> Response:
    authenticated(request, session)
    await validate_csrf(request)
    form = await request.form()
    phrase = str(form.get("search", "")).strip()
    return redirect(request, "search", phrase=phrase or "_", page=1)


@router.get("/search/{phrase}/{page}", response_class=HTMLResponse, name="search")
def search(
    phrase: str,
    page: int,
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_settings),
) -> Response:
    user = authenticated(request, session)
    phrase = "" if phrase == "_" else phrase
    results = search_settings(session, phrase, page, app_settings.results_per_page)
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
    app_settings: AppSettings = Depends(get_settings),
) -> Response:
    user = authenticated(request, session)
    setting = get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    if not setting.public and setting.owner_id != user.id:
        raise HTTPException(status_code=403, detail="This setting is private")
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
    app_settings: AppSettings = Depends(get_settings),
) -> Response:
    user = authenticated(request, session)
    await validate_csrf(request)
    form = await request.form()
    setting = get_setting(session, setting_id)
    if setting is None:
        raise HTTPException(status_code=404, detail="Setting not found")
    if not setting.public and setting.owner_id != user.id:
        raise HTTPException(status_code=403, detail="This setting is private")
    try:
        with session.begin_nested():
            if "copy" in form:
                if setting.owner_id == user.id:
                    raise ValidationError("You already own this setting")
                if len(list_user_settings(session, user.id)) >= user.setting_limit:
                    raise ValidationError("Reached the allowed settings limit")
                copy_setting(session, setting_id, user.id)
                return redirect(request, "my_settings")

            owns_setting = setting.owner_id == user.id
            if ("save" in form or "save_run" in form) and not owns_setting:
                raise HTTPException(status_code=403, detail="Only the owner can edit a setting")
            if "save" in form or "save_run" in form:
                await update_setting_from_form(session, setting_id, form, app_settings)
                flash(request, f'Setting change successful with name "{form.get("set_name")}"', "success")
            if "run" in form or "save_run" in form:
                uploads = [value for value in form.getlist("Process_files") if isinstance(value, UploadFile)]
                await create_job(session, app_settings, user.id, setting_id, uploads)
        return redirect(request, "my_settings")
    except ValidationError as exc:
        flash(request, str(exc))
        refreshed = get_setting(session, setting_id) or setting
        return render(
            request,
            "setting.html",
            **navigation_context(session, user, app_settings),
            setting=refreshed,
            allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        )


@router.post("/terminate", name="terminate")
async def terminate(request: Request, session: Session = Depends(get_db)) -> Response:
    user = authenticated(request, session)
    await validate_csrf(request)
    request_job_cancellation(session, user.id)
    return redirect(request, "my_settings")


@router.post("/download_files", name="download_files")
async def download_files(
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_settings),
) -> Response:
    user = authenticated(request, session)
    await validate_csrf(request)
    job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.user_id == user.id, ProcessingJob.status == "completed")
        .order_by(ProcessingJob.finished_at.desc())
        .limit(1)
    )
    if job is None:
        return redirect(request, "my_settings")
    root, _, output = job_paths(app_settings, job.id)
    archive = io.BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for source in sorted(output.iterdir()):
            if source.is_file():
                zip_file.write(source, Path("Your files") / source.name)
    archive.seek(0)
    session.delete(job)
    shutil.rmtree(root, ignore_errors=True)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="Files.zip"'},
    )
