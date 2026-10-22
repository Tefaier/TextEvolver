from __future__ import annotations

import io
import shutil
from pathlib import Path
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from text_evolver.config import AppSettings, get_application_settings
from text_evolver.db.models import ProcessingJob, UserAccount
from text_evolver.db.session import get_db
from text_evolver.services import ACTIVE_JOB_STATUSES, job_paths, request_job_cancellation
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
    job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.user_id == user.id, ProcessingJob.status == "completed")
        .order_by(ProcessingJob.finished_at.desc())
        .limit(1)
    )
    if job is None:
        return redirect(request, "my_settings")
    paths = job_paths(app_settings, job.id)
    root = paths[0]
    output = paths[2]
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
