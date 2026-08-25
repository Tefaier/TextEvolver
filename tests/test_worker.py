from contextlib import contextmanager

from sqlalchemy.orm import Session

import text_evolver.worker as worker
from text_evolver.db.models import ProcessingJob, Setting, UserAccount
from text_evolver.services import job_paths, request_job_cancellation


def install_worker_database(monkeypatch, database, app_settings):
    @contextmanager
    def test_session_scope():
        with Session(database) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(worker, "session_scope", test_session_scope)
    monkeypatch.setattr(worker, "get_settings", lambda: app_settings)


def create_queued_job(database) -> int:
    with Session(database) as session:
        user = UserAccount(username="worker-user", password_hash="unused")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="worker-setting")
        session.add(setting)
        session.flush()
        job = ProcessingJob(user_id=user.id, setting_id=setting.id, status="queued")
        session.add(job)
        session.commit()
        return job.id


def test_worker_claims_and_completes_a_document(monkeypatch, database, app_settings):
    install_worker_database(monkeypatch, database, app_settings)
    job_id = create_queued_job(database)
    _, origin, output = job_paths(app_settings, job_id)
    origin.mkdir(parents=True)
    output.mkdir(parents=True)
    (origin / "book.html").write_text("<html><body><p>text</p></body></html>", encoding="utf-8")

    claimed = worker.claim_job()
    assert claimed is not None
    assert claimed.id == job_id
    worker.run_claimed_job(claimed)

    with Session(database) as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.finished_at is not None
    assert (output / "book.html").exists()


def test_queued_cancellation_and_restart_recovery(monkeypatch, database, app_settings):
    install_worker_database(monkeypatch, database, app_settings)
    job_id = create_queued_job(database)
    with Session(database) as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        request_job_cancellation(session, job.user_id)
        session.commit()
        assert job.status == "cancelled"

        job.status = "running"
        job.finished_at = None
        session.commit()

        request_job_cancellation(session, job.user_id)
        session.commit()
        assert job.cancellation_requested
        assert worker.cancellation_requested(job_id)

    assert worker.recover_interrupted_jobs() == 1
    with Session(database) as session:
        recovered = session.get(ProcessingJob, job_id)
        assert recovered is not None
        assert recovered.status == "queued"
        assert recovered.error_message is not None
        assert "returned to the queue" in recovered.error_message
