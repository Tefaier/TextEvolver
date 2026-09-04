import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy.orm import Session

import text_evolver.worker as worker
from text_evolver.db.models import ImageConversion, ImageConversionFile, ProcessingJob, Setting, UserAccount
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
    monkeypatch.setattr(worker, "get_application_settings", lambda: app_settings)


def create_queued_job(database) -> int:
    with Session(database) as session:
        user = UserAccount(username="worker-user")
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
    assert not worker.job_temp_path(app_settings, job_id).exists()


def create_job_with_image(database, object_key: str) -> int:
    with Session(database) as session:
        user = UserAccount(username=f"image-worker-{object_key}")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="image-setting")
        session.add(setting)
        session.flush()
        conversion = ImageConversion(
            setting_id=setting.id,
            phrase="trigger",
            separation=1,
            explanation="",
            mutations=False,
        )
        session.add(conversion)
        session.flush()
        session.add(
            ImageConversionFile(
                image_conversion_id=conversion.id,
                object_key=object_key,
                size_bytes=5,
                position=0,
            )
        )
        job = ProcessingJob(user_id=user.id, setting_id=setting.id, status="queued")
        session.add(job)
        session.commit()
        return job.id


def test_worker_downloads_images_into_the_job_temp_directory(
    monkeypatch, database, app_settings, image_storage
):
    install_worker_database(monkeypatch, database, app_settings)
    object_key = "users/1/settings/1/image.png"
    image_storage.objects[object_key] = b"image"
    job_id = create_job_with_image(database, object_key)
    claimed = worker.claim_job()
    assert claimed is not None

    configuration = worker.load_configuration(claimed, app_settings, image_storage)

    image_paths = configuration.images[0]["image_paths"]
    assert len(image_paths) == 1
    staged = image_paths[0]
    assert isinstance(staged, Path)
    assert staged.parent == worker.job_temp_path(app_settings, job_id) / "images"
    assert staged.read_bytes() == b"image"


def test_worker_cleans_staging_after_image_download_failure(
    monkeypatch, database, app_settings, image_storage
):
    install_worker_database(monkeypatch, database, app_settings)
    job_id = create_job_with_image(database, "users/1/settings/1/missing.png")
    claimed = worker.claim_job()
    assert claimed is not None

    worker.run_claimed_job(claimed, app_settings, image_storage)

    with Session(database) as session:
        job = session.get(ProcessingJob, job_id)
        assert job is not None
        assert job.status == "failed"
    assert not worker.job_temp_path(app_settings, job_id).exists()


def test_worker_startup_cleanup_preserves_pokemon_cache(app_settings):
    abandoned = worker.job_temp_path(app_settings, 99)
    abandoned.mkdir(parents=True)
    (abandoned / "image.png").write_bytes(b"image")
    pokemon_cache = app_settings.temp_root / "Pokemons" / "images"
    pokemon_cache.mkdir(parents=True)
    pokemon_image = pokemon_cache / "pikachu.png"
    pokemon_image.write_bytes(b"pokemon")

    worker.cleanup_stale_job_directories(app_settings)

    assert not abandoned.exists()
    assert (app_settings.temp_root / "jobs").is_dir()
    assert pokemon_image.read_bytes() == b"pokemon"


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


def test_worker_pool_runs_configured_jobs_in_parallel(monkeypatch, app_settings):
    settings = app_settings.model_copy(update={"worker_concurrency": 3})
    stopping = threading.Event()
    jobs = [worker.ClaimedJob(id=value, setting_id=value) for value in range(1, 4)]
    lock = threading.Lock()
    barrier = threading.Barrier(settings.worker_concurrency)
    processed: dict[int, str] = {}

    def claim_job():
        with lock:
            return jobs.pop() if jobs else None

    def run_claimed_job(job, _settings):
        processed[job.id] = threading.current_thread().name
        barrier.wait(timeout=5)
        if len(processed) == settings.worker_concurrency:
            stopping.set()

    monkeypatch.setattr(worker, "claim_job", claim_job)
    monkeypatch.setattr(worker, "run_claimed_job", run_claimed_job)

    worker.run_worker_pool(settings, stopping)

    assert set(processed) == {1, 2, 3}
    assert len(set(processed.values())) == settings.worker_concurrency
