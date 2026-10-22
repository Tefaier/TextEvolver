import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import AuthFlow, GoogleIdentity
from text_evolver.db.session import get_db
from text_evolver.web.auth import login, require_recent, require_user
from text_evolver.web.flows import consume_flow, create_flow
from text_evolver.web.routines import google as google_routines
from text_evolver.web.routines.accounts import create_account, lock_user
from text_evolver.web.routines.flashes import flash
from text_evolver.web.routines.rendering import render
from text_evolver.web.routines.responses import form_redirect
from text_evolver.web.sessions import state
from text_evolver.web.utils import utcnow
from text_evolver.web.validation_utils import ValidationError, validate_google_purpose, validate_username

router = APIRouter()


@router.post("/auth/google/start", name="google_start")
async def start(
    request: Request, 
    db: Session = Depends(get_db), 
    settings: AppSettings = Depends(get_application_settings)
):
    form = await request.form()
    purpose = str(form.get("purpose", "login"))
    try:
        validate_google_purpose(purpose)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None
    user = (
        require_recent(request, db)
        if purpose == "link"
        else (require_user(request, db) if purpose == "reauthenticate" else None)
    )
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    flow = create_flow(
        request,
        db,
        "google",
        {"purpose": purpose, "nonce": nonce, "verifier": verifier},
        user_id=user.id if user else None,
    )
    oauth = google_routines.client(settings)
    authorization = oauth.create_authorization_url(
        google_routines.AUTHORIZE, state=flow, nonce=nonce, code_verifier=verifier, prompt="select_account"
    )
    url = authorization[0]
    return RedirectResponse(url, 303)


@router.get("/auth/google/callback", name="google_callback")
async def callback(
    request: Request, db: Session = Depends(get_db), settings: AppSettings = Depends(get_application_settings)
):
    flow = consume_flow(request, db, "google", request.query_params.get("state", ""))
    payload = json.loads(flow.data)
    target_user = flow.user_id
    db.commit()  # Release the connection and consume state before contacting Google.
    if request.query_params.get("error") or not request.query_params.get("code"):
        flash(request, "Authentication failed or was cancelled. Please try again.")
        return RedirectResponse("/login" if target_user is None else "/account/settings", 303)
    try:
        claims = await google_routines.exchange_and_verify(
            request.app.state.http_client, 
            settings, 
            request.query_params["code"], 
            payload["verifier"], 
            payload["nonce"]
        )
    except Exception:
        # Provider responses can contain tokens. Do not log or display the exception.
        raise HTTPException(400, "Authentication failed. Please try again.") from None
    subject = claims["sub"]
    identity = db.scalar(
        select(GoogleIdentity).where(GoogleIdentity.issuer == google_routines.ISSUER, GoogleIdentity.subject == subject)
    )
    if payload["purpose"] == "login":
        if identity:
            user = lock_user(db, identity.user_id)
            identity = db.scalar(
                select(GoogleIdentity)
                .where(
                    GoogleIdentity.user_id == user.id,
                    GoogleIdentity.issuer == google_routines.ISSUER,
                    GoogleIdentity.subject == subject,
                )
                .execution_options(populate_existing=True)
            )
            if identity is None:
                raise HTTPException(403, "Authentication failed. Please try again.")
            login(request, user, "google")
            return RedirectResponse("/my_settings", 303)
        create_flow(request, db, "google_signup", {"subject": subject})
        return RedirectResponse("/auth/google/complete", 303)
    user = require_user(request, db)
    if user.id != target_user:
        raise HTTPException(403, "Authentication failed. Please try again.")
    user = lock_user(db, user.id)
    identity = db.scalar(
        select(GoogleIdentity)
        .where(GoogleIdentity.issuer == google_routines.ISSUER, GoogleIdentity.subject == subject)
        .execution_options(populate_existing=True)
    )
    if payload["purpose"] == "reauthenticate":
        if identity is None or identity.user_id != user.id:
            raise HTTPException(403, "Authentication failed. Please try again.")
        login(request, user, "google", recreate=False)
    else:
        require_recent(request, db)
        if identity and identity.user_id != user.id:
            raise HTTPException(409, "This Google account is already linked.")
        existing = db.scalar(select(GoogleIdentity).where(GoogleIdentity.user_id == user.id))
        if existing and existing.subject != subject:
            raise HTTPException(409, "This account already has a linked Google identity.")
        if not existing:
            try:
                with db.begin_nested():
                    db.add(GoogleIdentity(user_id=user.id, issuer=google_routines.ISSUER, subject=subject))
                    db.flush()
            except IntegrityError:
                raise HTTPException(409, "This Google account is already linked.") from None
    return RedirectResponse("/account/settings", 303)


@router.get("/auth/google/complete", name="google_complete_page")
def complete_page(request: Request):
    return render(request, "google_complete.html")


@router.post("/auth/google/complete", name="google_complete")
async def complete(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("username", "")).strip()
    try:
        validate_username(name)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from None

    pending = db.scalar(
        select(AuthFlow).where(
            AuthFlow.browser_session_token_hash == state(request).token_hash,
            AuthFlow.purpose == "google_signup",
            AuthFlow.expires_at > utcnow(),
        )
    )
    if pending is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    try:
        with db.begin_nested():
            user = create_account(db, name)
            db.add(GoogleIdentity(
                user_id=user.id, issuer=google_routines.ISSUER, subject=json.loads(pending.data)["subject"]
            ))
            db.delete(pending)
            db.flush()
    except (IntegrityError, ValidationError):
        raise HTTPException(409, "Username or Google account is already registered.") from None
    login(request, user, "google")
    return form_redirect(request, "my_settings")
