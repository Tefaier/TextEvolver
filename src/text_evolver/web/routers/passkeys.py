import json

import webauthn
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import Passkey, UserPassword
from text_evolver.db.session import get_db
from text_evolver.web.auth import login, require_recent, require_user
from text_evolver.web.flows import consume_flow, create_flow
from text_evolver.web.i18n import translate
from text_evolver.web.routines.accounts import lock_user
from text_evolver.web.routines.passkeys import decode
from text_evolver.web.utils import utcnow
from text_evolver.web.validation_utils import (
    PASSKEY_LIMIT,
    ValidationError,
    validate_passkey_name,
    validate_webauthn_body,
    validate_webauthn_credential,
    validate_webauthn_operation,
)

router = APIRouter()


@router.post("/auth/webauthn/{operation}/options", name="passkey_options")
async def options(
    operation: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_application_settings),
):
    try:
        validate_webauthn_operation(operation)
    except ValidationError as exc:
        raise HTTPException(404, str(exc)) from None
    user = None
    if operation == "register":
        user = require_recent(request, db)
        keys = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
        if len(keys) >= PASSKEY_LIMIT:
            raise HTTPException(409, "A maximum of 3 passkeys is allowed.")
        public = webauthn.generate_registration_options(
            rp_id=settings.webauthn_rp_id,
            rp_name=translate("TEXT EVOLVER"),
            user_name=user.username,
            user_id=user.id.bytes,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                require_resident_key=True,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=decode(key.credential_id)) for key in keys],
        )
    elif operation in {"login", "reauthenticate", "recover"}:
        if operation == "reauthenticate":
            user = require_user(request, db)
        public = webauthn.generate_authentication_options(
            rp_id=settings.webauthn_rp_id, 
            user_verification=UserVerificationRequirement.REQUIRED
        )
    token = create_flow(
        request, db, operation, {"challenge": bytes_to_base64url(public.challenge)}, user_id=user.id if user else None
    )
    return JSONResponse({"flow": token, "publicKey": json.loads(options_to_json(public))})


@router.post("/auth/webauthn/{operation}/verify", name="passkey_verify")
async def verify(
    operation: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: AppSettings = Depends(get_application_settings),
):
    try:
        validate_webauthn_operation(operation)
    except ValidationError as exc:
        raise HTTPException(404, str(exc)) from None
    try:
        validate_webauthn_body(await request.body())
        payload = await request.json()
        credential = payload["credential"]
        validate_webauthn_credential(credential)
        credential_id = bytes_to_base64url(decode(credential["id"]))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Authentication failed. Please try again.") from None
    if operation == "register":
        user = require_recent(request, db)
        flow = consume_flow(request, db, operation, payload.get("flow", ""))
        if flow.user_id != user.id:
            raise HTTPException(403, "Authentication failed. Please try again.")
        try:
            name = validate_passkey_name(payload.get("name", ""))
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            result = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=decode(json.loads(flow.data)["challenge"]),
                expected_rp_id=settings.webauthn_rp_id,
                expected_origin=settings.webauthn_origin,
                require_user_verification=True,
            )
        except Exception:
            raise HTTPException(400, "Authentication failed. Please try again.") from None
        lock_user(db, user.id)
        count = db.scalar(select(func.count()).select_from(Passkey).where(Passkey.user_id == user.id)) or 0
        if count >= PASSKEY_LIMIT:
            raise HTTPException(409, "A maximum of 3 passkeys is allowed.")
        try:
            with db.begin_nested():
                db.add(
                    Passkey(
                        user_id=user.id,
                        credential_id=bytes_to_base64url(result.credential_id),
                        public_key=bytes_to_base64url(result.credential_public_key),
                        sign_count=result.sign_count,
                        backed_up=result.credential_backed_up,
                        name=name,
                        created_at=utcnow(),
                        transports=json.dumps(credential.get("response", {}).get("transports", [])),
                    )
                )
                db.flush()
        except IntegrityError:
            raise HTTPException(409, "This passkey is already registered.") from None
        return JSONResponse({"redirect": "/account/settings"})
    key = db.scalar(select(Passkey).where(Passkey.credential_id == credential_id))
    if key is None:
        raise HTTPException(400, "Authentication failed. Please try again.")
    user = lock_user(db, key.user_id)
    key = db.scalar(
        select(Passkey).where(Passkey.id == key.id).with_for_update().execution_options(populate_existing=True)
    )
    flow = consume_flow(request, db, operation, payload.get("flow", ""))
    if key is None or (flow.user_id is not None and flow.user_id != user.id):
        raise HTTPException(400, "Authentication failed. Please try again.")
    try:
        handle = credential["response"]["userHandle"]
        valid_handle = isinstance(handle, str) and decode(handle) == user.id.bytes
    except (KeyError, ValueError):
        valid_handle = False
    if not valid_handle:
        raise HTTPException(400, "Authentication failed. Please try again.")
    try:
        result = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=decode(json.loads(flow.data)["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origin,
            credential_public_key=decode(key.public_key),
            credential_current_sign_count=0 if key.backed_up else key.sign_count,
            require_user_verification=True,
        )
    except Exception:
        raise HTTPException(400, "Authentication failed. Please try again.") from None
    key.sign_count = max(key.sign_count, result.new_sign_count)
    key.backed_up = result.credential_backed_up
    key.last_used_at = utcnow()
    if operation == "recover":
        if db.get(UserPassword, user.id) is None:
            raise HTTPException(400, "Authentication failed. Please try again.")
        create_flow(request, db, "reset", {}, user_id=user.id, passkey_id=key.id)
        return JSONResponse({"redirect": "/reset-password"})
    if operation in {"login", "reauthenticate"}:
        if operation == "reauthenticate" and require_user(request, db).id != user.id:
            raise HTTPException(403, "Authentication failed. Please try again.")
        login(request, user, "webauthn", recreate=operation == "login")
        return JSONResponse({"redirect": "/account/settings" if operation == "reauthenticate" else "/my_settings"})
    raise HTTPException(404, "Invalid account action")
