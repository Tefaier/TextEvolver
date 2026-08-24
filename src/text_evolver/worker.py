from __future__ import annotations

import datetime as dt
import logging
import multiprocessing
import shutil
import signal
import time
from dataclasses import dataclass

import psutil
from sqlalchemy import select, update

from text_evolver.config import get_settings
from text_evolver.db.models import ProcessingJob
from text_evolver.db.session import session_scope
from text_evolver.processing.processor import process_files
from text_evolver.processing.settings_loader import ProcessingConfiguration, load_processing_configuration
from text_evolver.services import job_paths

LOGGER = logging.getLogger("text_evolver.worker")


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    setting_id: int


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def recover_interrupted_jobs() -> int:
    with session_scope() as session:
        result = session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.status == "running")
            .values(
                status="queued",
                cancellation_requested=False,
                started_at=None,
                heartbeat_at=None,
                error_message="Worker restarted; job returned to the queue",
            )
        )
        return result.rowcount or 0


def claim_job() -> ClaimedJob | None:
    with session_scope() as session:
        job = session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.status == "queued")
            .order_by(ProcessingJob.created_at, ProcessingJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.cancellation_requested:
            job.status = "cancelled"
            job.finished_at = utcnow()
            return None
        job.status = "running"
        job.started_at = utcnow()
        job.heartbeat_at = job.started_at
        job.error_message = None
        session.flush()
        return ClaimedJob(job.id, job.setting_id)


def load_configuration(setting_id: int) -> ProcessingConfiguration:
    with session_scope() as session:
        return load_processing_configuration(session, setting_id)


def cancellation_requested(job_id: int) -> bool:
    with session_scope() as session:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            return True
        job.heartbeat_at = utcnow()
        return job.cancellation_requested or job.status != "running"


def finish_job(job_id: int, status: str, error: str | None = None) -> None:
    with session_scope() as session:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            return
        job.status = status
        job.finished_at = utcnow()
        job.heartbeat_at = job.finished_at
        job.error_message = error[:4000] if error else None


def run_claimed_job(job: ClaimedJob) -> None:
    settings = get_settings()
    root, origin, output = job_paths(settings, job.id)
    try:
        configuration = load_configuration(job.setting_id)
    except Exception as exc:
        LOGGER.exception("Unable to load settings for job %s", job.id)
        finish_job(job.id, "failed", str(exc))
        return

    process = multiprocessing.Process(
        target=process_files,
        args=(configuration, origin, output),
        name=f"text-evolver-job-{job.id}",
    )
    process.start()
    cancelled = False
    while process.is_alive():
        process.join(timeout=settings.worker_cancel_poll_seconds)
        if process.is_alive() and cancellation_requested(job.id):
            cancelled = True
            process.terminate()
            process.join(timeout=10)
            if process.is_alive():
                process.kill()
                process.join()
    if cancelled:
        finish_job(job.id, "cancelled")
        shutil.rmtree(root, ignore_errors=True)
    elif process.exitcode == 0:
        finish_job(job.id, "completed")
    else:
        finish_job(job.id, "failed", f"Processing subprocess exited with code {process.exitcode}")
        shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    settings.ensure_directories()
    recovered = recover_interrupted_jobs()
    if recovered:
        LOGGER.warning("Returned %s interrupted job(s) to the queue", recovered)
    stopping = False

    def stop(_: int, __: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOGGER.info("Worker started")
    while not stopping:
        if psutil.virtual_memory().available < settings.worker_min_free_memory_bytes:
            time.sleep(settings.worker_poll_seconds)
            continue
        job = claim_job()
        if job is None:
            time.sleep(settings.worker_poll_seconds)
            continue
        LOGGER.info("Processing job %s", job.id)
        run_claimed_job(job)
    LOGGER.info("Worker stopped")


if __name__ == "__main__":
    main()

