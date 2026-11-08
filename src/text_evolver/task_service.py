"""Task snapshots and consuming downloads shared by browser and API."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from text_evolver.config import AppSettings
from text_evolver.db.models import ProcessingJob
from text_evolver.services import job_paths
from text_evolver.transactions import commit


def consume_result(
    session: Session, settings: AppSettings, user_id: UUID, job_id: int | None = None,
) -> StreamingResponse:
    query = select(ProcessingJob).where(ProcessingJob.user_id == user_id)
    if job_id is None:
        query = query.where(ProcessingJob.status == "completed").order_by(ProcessingJob.finished_at.desc()).limit(1)
    else:
        query = query.where(ProcessingJob.id == job_id)
    job = session.scalar(query.with_for_update().execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(404, "Task not found")
    if job.status != "completed":
        raise HTTPException(409, "Task result is not ready")
    paths = job_paths(settings, job.id)
    archive = SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
            for source in sorted(paths[2].iterdir()):
                if source.is_file():
                    zip_file.write(source, Path(source.name))
        archive.seek(0)
        session.delete(job)
        commit(session)
    except BaseException:
        archive.close()
        session.rollback()
        raise
    shutil.rmtree(paths[0], ignore_errors=True)

    def chunks() -> Iterator[bytes]:
        try:
            while chunk := archive.read(64 * 1024):
                yield chunk
        finally:
            archive.close()

    return StreamingResponse(
        chunks(), 
        media_type="application/zip", 
        background=BackgroundTask(archive.close),
        headers={"Content-Disposition": 'attachment; filename="Files.zip"'},
    )
