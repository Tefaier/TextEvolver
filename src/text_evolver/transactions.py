"""Database commits coordinated with staged files and durable object cleanup."""

import logging
import shutil
from contextlib import contextmanager

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from text_evolver.db.models import SettingImageDeletion
from text_evolver.image_storage import ImageStorageChanges, ImageStorageError

LOGGER = logging.getLogger(__name__)


def rollback_staged_jobs(session) -> None:
    for root in session.info.pop("staged_job_roots", []):
        shutil.rmtree(root, ignore_errors=True)


def commit(session) -> None:
    try:
        session.commit()
    except BaseException:
        session.rollback()
        rollback_staged_jobs(session)
        raise
    session.info.pop("staged_job_roots", None)


@contextmanager
def setting_changes(session, storage):
    changes = ImageStorageChanges(storage)
    try:
        yield changes
        keys = list(dict.fromkeys(changes.obsolete))
        if keys:
            session.execute(insert(SettingImageDeletion).values([{"object_key": key} for key in keys])
                            .on_conflict_do_nothing())
        commit(session)
    except BaseException:
        session.rollback()
        rollback_staged_jobs(session)
        try:
            changes.database_rolled_back()
        except ImageStorageError:
            # Uploads can have reached S3 even when the operation raised. Keep
            # their keys durable if immediate compensation also fails.
            try:
                if changes.created:
                    session.execute(insert(SettingImageDeletion).values(
                        [{"object_key": key} for key in dict.fromkeys(changes.created)]
                    ).on_conflict_do_nothing())
                    session.commit()
            except SQLAlchemyError:
                session.rollback()
                LOGGER.exception("Could not queue rolled-back setting image cleanup")
        raise
    try:
        changes.database_committed()
    except ImageStorageError:
        # The worker retries the transactionally queued keys.
        LOGGER.warning("Deferred setting image cleanup to worker")
    else:
        if keys:
            try:
                session.execute(delete(SettingImageDeletion).where(SettingImageDeletion.object_key.in_(keys)))
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                LOGGER.warning("Deferred image cleanup acknowledgement to worker")
