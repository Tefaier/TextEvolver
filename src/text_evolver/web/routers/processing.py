from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import UserAccount
from text_evolver.db.session import get_db
from text_evolver.services import ACTIVE_JOB_STATUSES, request_job_cancellation
from text_evolver.task_service import consume_result
from text_evolver.web.auth import require_user
from text_evolver.web.processing_status import ProcessingConnectionLimiter, wait_for_disconnect
from text_evolver.web.routines.processing import processing_snapshot
from text_evolver.web.routines.responses import form_redirect, redirect
from text_evolver.web.sessions import retrieve_current_session, seconds_remaining, state

router = APIRouter()


@router.websocket("/ws/processing", name="processing_status")
async def processing_status(
    websocket: WebSocket,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
) -> None:
    browser = retrieve_current_session(session, state(websocket))
    user_id = browser.user_id if browser else None
    if not isinstance(user_id, UUID) or session.get(UserAccount, user_id) is None:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    snapshot = processing_snapshot(session, user_id, app_settings)
    if snapshot["status"] not in ACTIVE_JOB_STATUSES:
        await websocket.accept()
        await websocket.send_json(snapshot)
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        return

    limiter: ProcessingConnectionLimiter = websocket.app.state.websocket_limiter
    if not await limiter.register(websocket):
        await websocket.accept()
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return

    try:
        await websocket.accept()
        while True:
            if retrieve_current_session(session, state(websocket)) is None:
                session.rollback()
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            session.rollback()
            await websocket.send_json(snapshot)
            if snapshot["status"] not in ACTIVE_JOB_STATUSES:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return
            interval = min(app_settings.websocket_update_seconds, seconds_remaining(state(websocket)))
            if await wait_for_disconnect(websocket, interval):
                return
            snapshot = processing_snapshot(session, user_id, app_settings)
    except WebSocketDisconnect:
        return
    finally:
        await limiter.unregister(websocket)


@router.post("/terminate", name="terminate")
async def terminate(request: Request, session: Session = Depends(get_db)) -> Response:
    user = require_user(request, session)
    request_job_cancellation(session, user.id)
    return form_redirect(request, "my_settings")


@router.post("/download_files", name="download_files")
async def download_files(
    request: Request,
    session: Session = Depends(get_db),
    app_settings: AppSettings = Depends(get_application_settings),
) -> Response:
    user = require_user(request, session)
    try:
        return consume_result(session, app_settings, user.id)
    except HTTPException as exc:
        if exc.status_code == 404:
            return redirect(request, "my_settings")
        raise
