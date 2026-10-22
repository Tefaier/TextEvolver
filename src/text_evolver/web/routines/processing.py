from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from text_evolver.config import AppSettings
from text_evolver.services import job_file_counts, latest_job


def processing_snapshot(session: Session, user_id: UUID, app_settings: AppSettings) -> dict[str, object]:
    try:
        session.expire_all()
        job = latest_job(session, user_id)
        if job is None:
            return {
                "job_id": None,
                "status": "idle",
                "completed_files": 0,
                "total_files": 0,
                "can_download": False,
                "can_terminate": False,
            }
        file_counts = job_file_counts(app_settings, job)
        total_files = file_counts[0]
        completed_files = file_counts[1]
        return {
            "job_id": job.id,
            "status": job.status,
            "completed_files": completed_files,
            "total_files": max(total_files, completed_files),
            "can_download": job.status == "completed" and completed_files > 0,
            "can_terminate": job.status == "running" and not job.cancellation_requested,
        }
    finally:
        # A WebSocket lives much longer than a request. End each read transaction
        # so PgBouncer can reuse the connection and the next read sees worker updates.
        session.rollback()
