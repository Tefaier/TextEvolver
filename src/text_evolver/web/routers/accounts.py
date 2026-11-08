import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from text_evolver.api.tokens import PERMISSIONS, issue_token, revoke_tokens
from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import ApiToken, AuthFlow, GoogleIdentity, Passkey, UserAccount, UserPassword
from text_evolver.db.session import get_db
from text_evolver.web.auth import (
    current_user,
    login,
    logout,
    require_recent,
    require_user,
)
from text_evolver.web.flows import consume_reset_grant
from text_evolver.web.i18n import translate
from text_evolver.web.login_throttle import (
    clear_login_failure,
    client_ip,
    lock_account_login_failure,
    lock_login_failure,
    login_is_blocked,
    record_account_login_failure,
    record_login_failure,
)
from text_evolver.web.routines.accounts import create_account, has_alternative, lock_user
from text_evolver.web.routines.flashes import flash
from text_evolver.web.routines.password import add_password, hash_password, rehash_password_if_needed, verify_password
from text_evolver.web.routines.rendering import navigation_context, render
from text_evolver.web.routines.responses import form_redirect, redirect, validation_error_response
from text_evolver.web.sessions import revoke_user_sessions, state
from text_evolver.web.utils import utcnow
from text_evolver.web.validation_utils import (
    ValidationError,
    validate_account_action,
    validate_credentials,
    validate_language,
    validate_passkey_id,
    validate_passkey_name,
    validate_password,
    validate_username,
)

router = APIRouter()


@router.post("/login", name="login")
async def login_submit(
    request: Request,
    session: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    ip = client_ip(request.client.host if request.client is not None else None)
    now = dt.datetime.now(dt.UTC)
    user = session.scalar(select(UserAccount).where(UserAccount.username == username).with_for_update())
    login_failure = lock_login_failure(session, username, ip)
    account_login_failure = lock_account_login_failure(session, username)
    if login_is_blocked(login_failure, account_login_failure, now):
        return validation_error_response(
            request,
            "Too many failed login attempts. Try again later",
            "loging.html",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    stored_password = (
        None if user is None else session.scalar(select(UserPassword).where(UserPassword.user_id == user.id))
    )
    if not verify_password(password, stored_password):
        record_login_failure(session, login_failure, username, ip, now)
        record_account_login_failure(session, account_login_failure, username, now)
        return validation_error_response(
            request,
            "User with these username and password does not exist",
            "loging.html",
        )
    rehash_password_if_needed(stored_password, password)
    user.last_entry = now
    login(request, user)
    flash(
        request, 
        translate("You successfully logged into account %(username)s", username=user.username), 
        "success"
    )
    return form_redirect(request, "my_settings")


@router.get("/register", response_class=HTMLResponse, name="register_page")
def register_page(request: Request, session: Session = Depends(get_db)) -> Response:
    if current_user(request, session) is not None:
        return redirect(request, "my_settings")
    return render(request, "register.html")


@router.post("/register", name="register")
async def register_submit(
    request: Request,
    session: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    confirm = str(form.get("confirm", ""))
    try:
        validate_credentials(username, password, confirm)
        user = create_account(session, username)
        add_password(session, user.id, password)
    except ValidationError as exc:
        return validation_error_response(request, str(exc), "register.html")
    login(request, user)
    flash(
        request, 
        translate("You successfully registered account %(username)s", username=user.username), 
        "success"
    )
    return form_redirect(request, "my_settings")


@router.post("/logout", name="logout")
async def logout_submit(request: Request, session: Session = Depends(get_db)) -> Response:
    require_user(request, session)
    logout(request)
    return form_redirect(request, "login_page")


@router.post("/language", name="language")
async def language(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    form = await request.form()
    choice = str(form.get("language", ""))
    try:
        validate_language(choice)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    user.language = None if choice == "default" else choice
    return form_redirect(request, "account_settings")


@router.get("/account/settings", name="account_settings")
def settings_page(
    request: Request, db: Session = Depends(get_db), settings: AppSettings = Depends(get_application_settings)
):
    user = require_user(request, db)
    return render_account(request, db, settings, user)


def render_account(request, db, settings, user, issued_token=None):
    return render(
        request,
        "account.html",
        api_tokens={row.permission: row for row in db.scalars(select(ApiToken).where(ApiToken.user_id == user.id))},
        issued_token=issued_token,
        **navigation_context(db, user, settings),
        has_password=db.get(UserPassword, user.id) is not None,
        language_preference=user.language or "default",
        google=db.scalar(select(GoogleIdentity).where(GoogleIdentity.user_id == user.id)),
        passkeys=list(db.scalars(select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.id))),
    )


@router.post("/account/tokens/{permission}/{action}", name="account_token")
async def account_token(permission: str, action: str, request: Request, db: Session = Depends(get_db),
                        settings: AppSettings = Depends(get_application_settings)):
    user = require_recent(request, db)
    if permission not in PERMISSIONS or action not in {"create", "rotate", "delete"}:
        raise HTTPException(422, "Invalid token operation")
    if action == "delete":
        db.execute(delete(ApiToken).where(ApiToken.user_id == user.id, ApiToken.permission == permission))
        db.commit()
        return form_redirect(request, "account_settings")
    secret = issue_token(db, user.id, permission, rotate=action == "rotate")
    db.commit()
    return render_account(request, db, settings, user, issued_token=secret)


@router.post("/account/settings", name="account_update")
async def update_account(request: Request, db: Session = Depends(get_db)):
    require_user(request, db)
    user = require_recent(request, db)
    form = await request.form()
    action = form.get("action")
    try:
        validate_account_action(action)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    if action == "username":
        name = str(form.get("username", "")).strip()
        try:
            validate_username(name)
            with db.begin_nested():
                user.username = name
                db.flush()
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        except IntegrityError:
            raise HTTPException(409, "Username is already registered") from None
    elif action == "password":
        stored = db.get(UserPassword, user.id)
        if stored and not verify_password(str(form.get("current_password", "")), stored):
            raise HTTPException(422, "Authentication failed. Please try again.")
        if not stored and state(request).method not in {"google", "webauthn"}:
            raise HTTPException(403, "Authenticate with Google or a passkey to set your first password.")
        password = str(form.get("password", ""))
        confirm = str(form.get("confirm", ""))
        try:
            validate_password(password, confirm)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        if stored:
            stored.password_hash = hash_password(password)
        else:
            add_password(db, user.id, password)
        revoke_tokens(db, user.id)
        revoke_user_sessions(db, user.id, except_token_hash=state(request).token_hash)
        db.execute(delete(AuthFlow).where(AuthFlow.user_id == user.id))
        login(request, user, "password", recreate=False)
    elif action == "unlink_google":
        if not has_alternative(db, user.id, removing_google=True):
            raise HTTPException(409, "Keep at least one sign-in method.")
        db.execute(delete(GoogleIdentity).where(GoogleIdentity.user_id == user.id))
        db.execute(delete(AuthFlow).where(AuthFlow.user_id == user.id))
    elif action in {"rename_passkey", "remove_passkey"}:
        try:
            key_id = validate_passkey_id(form.get("passkey_id", ""))
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        key = db.scalar(select(Passkey).where(Passkey.id == key_id, Passkey.user_id == user.id).with_for_update())
        if key is None:
            raise HTTPException(404, "Invalid passkey")
        if action == "rename_passkey":
            try:
                key.name = validate_passkey_name(form.get("name", ""))
            except ValidationError as exc:
                raise HTTPException(422, str(exc)) from None
        else:
            if not has_alternative(db, user.id, removing_passkey=key.id):
                raise HTTPException(409, "Keep at least one sign-in method.")
            db.execute(delete(AuthFlow).where(AuthFlow.passkey_id == key.id))
            db.delete(key)
            db.flush()
    return form_redirect(request, "account_settings")


@router.get("/account/reauthenticate", name="reauthenticate_page")
def reauthenticate_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    return render(
        request,
        "reauthenticate.html",
        has_password=db.get(UserPassword, user.id) is not None,
        has_google=db.get(GoogleIdentity, user.id) is not None,
        has_passkey=db.scalar(select(Passkey.id).where(Passkey.user_id == user.id)) is not None,
    )


@router.post("/account/reauthenticate", name="reauthenticate")
async def reauthenticate(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    form = await request.form()
    user = lock_user(db, user.id)
    if not verify_password(str(form.get("password", "")), db.get(UserPassword, user.id)):
        raise HTTPException(422, "Authentication failed. Please try again.")
    login(request, user, "password", recreate=False)
    return form_redirect(request, "account_settings")


@router.get("/forgot-password", name="forgot_password")
def forgot_password(request: Request):
    return render(request, "recover.html")


@router.get("/reset-password", name="reset_password_page")
def reset_password_page(request: Request, db: Session = Depends(get_db)):
    grant = db.scalar(
        select(AuthFlow.id).where(
            AuthFlow.purpose == "reset",
            AuthFlow.browser_session_token_hash == state(request).token_hash,
            AuthFlow.expires_at > utcnow(),
        )
    )
    if grant is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    return render(request, "reset_password.html")


@router.post("/reset-password", name="reset_password")
async def reset_password(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    pending = db.scalar(
        select(AuthFlow).where(
            AuthFlow.purpose == "reset",
            AuthFlow.browser_session_token_hash == state(request).token_hash,
        )
    )
    if pending is None or pending.user_id is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    user = lock_user(db, pending.user_id)
    stored = db.get(UserPassword, user.id)
    key = db.get(Passkey, pending.passkey_id)
    if stored is None or key is None or key.user_id != user.id:
        raise HTTPException(400, "Authentication failed. Please try again.")
    try:
        validate_password(str(form.get("password", "")), str(form.get("confirm", "")))
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    consume_reset_grant(request, db, pending.id)
    stored.password_hash = hash_password(str(form.get("password", "")))
    clear_login_failure(db, user.username, client_ip(request.client.host if request.client else None))
    revoke_tokens(db, user.id)
    revoke_user_sessions(db, user.id)
    db.execute(delete(AuthFlow).where(AuthFlow.user_id == user.id))
    state(request).clear()
    request.session.clear()
    return form_redirect(request, "login_page")
