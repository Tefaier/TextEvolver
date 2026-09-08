import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    ImageConversionFile,
    PhraseConversion,
    ProcessingJob,
    Setting,
    UnitConversion,
    UserAccount,
)
from text_evolver.image_storage import ImageStorageChanges
from text_evolver.services import (
    ImageSubmission,
    ValidationError,
    _acquire_create_job_lock,
    _calculate_row_changes,
    _lock_setting_for_job,
    _validate_image_submission_size,
    copy_setting,
    lock_setting_jobs_for_deletion,
    update_setting_from_form,
)


class TrackingUploadFile(UploadFile):
    def __init__(self, data: bytes, filename: str):
        super().__init__(BytesIO(data), size=len(data), filename=filename)
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return await super().read(size)


def test_calculate_row_changes_matches_duplicate_values_one_for_one():
    first = SimpleNamespace(value="same")
    second = SimpleNamespace(value="same")
    removed = SimpleNamespace(value="removed")

    changes = _calculate_row_changes(
        [first, second, removed],
        [{"value": "same"}, {"value": "added"}],
        ("value",),
    )

    assert changes.preserved == (first,)
    assert changes.removed == (second, removed)
    assert changes.added == ({"value": "added"},)


def test_oversized_upload_is_rejected_before_storage_is_used():
    upload = TrackingUploadFile(b"123456", "large.png")
    submission = ImageSubmission("Example", 1, "", False, None, (upload,))

    with pytest.raises(ValidationError, match="size limit"):
        _validate_image_submission_size([submission], {}, setting_limit_bytes=5)

    assert upload.read_sizes == []


def test_empty_upload_is_rejected_before_content_is_read():
    upload = TrackingUploadFile(b"", "empty.png")
    submission = ImageSubmission("Example", 1, "", False, None, (upload,))

    with pytest.raises(ValidationError, match="is empty"):
        _validate_image_submission_size([submission], {}, setting_limit_bytes=5)

    assert upload.read_sizes == []


def test_combined_upload_size_is_checked_before_any_content_is_read():
    first = TrackingUploadFile(b"1234", "first.png")
    second = TrackingUploadFile(b"5678", "second.png")
    submission = ImageSubmission("Example", 1, "", False, None, (first, second))

    with pytest.raises(ValidationError, match="size limit"):
        _validate_image_submission_size([submission], {}, setting_limit_bytes=7)

    assert first.read_sizes == []
    assert second.read_sizes == []


def test_create_job_uses_postgresql_transaction_lock_for_user():
    executed = []
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=lambda statement, parameters: executed.append((statement, parameters)),
    )

    _acquire_create_job_lock(session, user_id=42)

    assert len(executed) == 1
    statement, parameters = executed[0]
    assert str(statement) == "SELECT pg_advisory_xact_lock(:user_id)"
    assert parameters == {"user_id": 42}


def test_create_job_lock_is_skipped_for_sqlite():
    executed = []
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        execute=lambda statement, parameters: executed.append((statement, parameters)),
    )

    _acquire_create_job_lock(session, user_id=42)

    assert executed == []


def test_lock_setting_for_job_uses_for_share_nowait():
    statements = []
    session = SimpleNamespace(scalar=lambda statement: statements.append(statement) or 42)

    _lock_setting_for_job(session, setting_id=42)

    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "WHERE setting.id =" in sql
    assert sql.endswith("FOR SHARE NOWAIT")


def test_lock_setting_for_job_rejects_missing_setting():
    session = SimpleNamespace(scalar=lambda _statement: None)

    with pytest.raises(ValidationError, match="Setting not found"):
        _lock_setting_for_job(session, setting_id=42)


def test_lock_setting_for_job_rejects_locked_setting():
    class LockNotAvailable(Exception):
        sqlstate = "55P03"

    def raise_lock_error(_statement):
        raise OperationalError("SELECT", {}, LockNotAvailable())

    session = SimpleNamespace(scalar=raise_lock_error)

    with pytest.raises(ValidationError, match="Setting is currently being updated"):
        _lock_setting_for_job(session, setting_id=42)


def test_lock_setting_jobs_for_deletion_uses_for_update():
    statements = []
    session = SimpleNamespace(scalars=lambda statement: statements.append(statement) or ())

    assert lock_setting_jobs_for_deletion(session, setting_id=42) == []

    assert len(statements) == 1
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert "WHERE processing_job.setting_id =" in sql
    assert sql.endswith("FOR UPDATE")


@pytest.mark.parametrize("job_status", ["queued", "running"])
def test_update_setting_rejects_setting_used_by_active_job(
    database,
    app_settings,
    image_storage,
    job_status: str,
):
    with Session(database) as session:
        user = UserAccount(username=f"owner-{job_status}")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="Original")
        session.add(setting)
        session.flush()
        session.add(ProcessingJob(user_id=user.id, setting_id=setting.id, status=job_status))
        session.commit()
        form = FormData(
            [
                ("set_name", "Updated"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
            ]
        )

        with pytest.raises(ValidationError, match="while its job is queued or running"):
            asyncio.run(
                update_setting_from_form(
                    session,
                    setting.id,
                    form,
                    app_settings,
                    image_storage,
                    ImageStorageChanges(image_storage),
                )
            )

        assert setting.name == "Original"


def test_update_setting_preserves_unchanged_rows(database, app_settings, image_storage):
    with Session(database) as session:
        user = UserAccount(username="owner")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="Conversions")
        session.add(setting)
        session.flush()
        fandom = Fandom(
            setting_id=setting.id,
            name="Pokemons",
            active=True,
            separation=100,
            support_value_1=True,
            support_value_2=False,
        )
        preserved_unit = UnitConversion(
            setting_id=setting.id,
            phrase_from="meter",
            phrase_to="metre",
            conversion=1.0,
            can_be_word=True,
        )
        changed_unit = UnitConversion(
            setting_id=setting.id,
            phrase_from="foot",
            phrase_to="feet",
            conversion=2.0,
            can_be_word=False,
        )
        phrase = PhraseConversion(
            setting_id=setting.id,
            phrase_from="old",
            phrase_to="new",
            direct=True,
            mutations=False,
            regex=False,
        )
        image = ImageConversion(
            setting_id=setting.id,
            phrase="Pikachu",
            separation=50,
            explanation="Electric mouse",
            mutations=True,
        )
        replaced_image = ImageConversion(
            setting_id=setting.id,
            phrase="Eevee",
            separation=75,
            explanation="Evolution Pokemon",
            mutations=False,
        )
        session.add_all((fandom, preserved_unit, changed_unit, phrase, image, replaced_image))
        session.flush()
        image_file = ImageConversionFile(
            image_conversion_id=image.id,
            object_key="users/1/settings/1/pikachu.png",
            size_bytes=len(b"existing"),
            position=0,
        )
        replaced_file = ImageConversionFile(
            image_conversion_id=replaced_image.id,
            object_key="users/1/settings/1/old-eevee.png",
            size_bytes=len(b"old-eevee"),
            position=0,
        )
        session.add_all((image_file, replaced_file))
        session.commit()
        image_storage.objects[image_file.object_key] = b"existing"
        image_storage.objects[replaced_file.object_key] = b"old-eevee"

        setting_id = setting.id
        preserved_ids = {
            "fandom": fandom.id,
            "unit": preserved_unit.id,
            "phrase": phrase.id,
            "image": image.id,
        }
        changed_unit_id = changed_unit.id
        replaced_image_id = replaced_image.id
        old_eevee_key = replaced_file.object_key
        replacement_data = b"new-eevee-image"
        replacement_data_2 = b"another-eevee"
        replacement_upload = TrackingUploadFile(replacement_data, "eevee.png")
        replacement_upload_2 = TrackingUploadFile(replacement_data_2, "eevee-2.png")

        form = FormData(
            [
                ("set_name", "Conversions"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
                ("fandom", "Pokemons"),
                ("fandom_active", "True"),
                ("fandom_separation", "100"),
                ("fandom_value_1", "True"),
                ("fandom_value_2", "False"),
                ("unit_from", "meter"),
                ("unit_to", "metre"),
                ("unit_convert", "1.0"),
                ("unit_can", "True"),
                ("unit_from", "foot"),
                ("unit_to", "feet changed"),
                ("unit_convert", "2.0"),
                ("unit_can", "False"),
                ("unit_from", "yard"),
                ("unit_to", "yards"),
                ("unit_convert", "3.0"),
                ("unit_can", "False"),
                ("phrase_from", "old"),
                ("phrase_to", "new"),
                ("phrase_direct", "True"),
                ("phrase_mutations", "False"),
                ("phrase_regex", "False"),
                ("image_token", f"existing-{image.id}"),
                ("image_phrase", "Pikachu"),
                ("image_separation", "50"),
                ("image_expl", "Electric mouse"),
                ("image_mutations", "True"),
                ("image_token", f"existing-{replaced_image.id}"),
                ("image_phrase", "Eevee"),
                ("image_separation", "75"),
                ("image_expl", "Evolution Pokemon"),
                ("image_mutations", "False"),
                (f"image_files_existing-{replaced_image.id}", replacement_upload),
                (f"image_files_existing-{replaced_image.id}", replacement_upload_2),
            ]
        )

        storage_changes = ImageStorageChanges(image_storage)
        asyncio.run(
            update_setting_from_form(
                session,
                setting_id,
                form,
                app_settings,
                image_storage,
                storage_changes,
            )
        )
        session.commit()
        storage_changes.database_committed()
        session.expire_all()

        assert session.scalar(select(Fandom.id).where(Fandom.setting_id == setting_id)) == preserved_ids["fandom"]
        assert (
            session.scalar(select(PhraseConversion.id).where(PhraseConversion.setting_id == setting_id))
            == preserved_ids["phrase"]
        )
        images = {
            value.phrase: value
            for value in session.scalars(select(ImageConversion).where(ImageConversion.setting_id == setting_id))
        }
        assert images["Pikachu"].id == preserved_ids["image"]
        assert images["Eevee"].id == replaced_image_id
        pikachu_files = list(
            session.scalars(
                select(ImageConversionFile).where(
                    ImageConversionFile.image_conversion_id == images["Pikachu"].id
                )
            )
        )
        assert [value.object_key for value in pikachu_files] == [image_file.object_key]
        eevee_files = list(
            session.scalars(
                select(ImageConversionFile)
                .where(ImageConversionFile.image_conversion_id == images["Eevee"].id)
                .order_by(ImageConversionFile.position)
            )
        )
        assert [image_storage.objects[value.object_key] for value in eevee_files] == [
            replacement_data,
            replacement_data_2,
        ]
        assert old_eevee_key in image_storage.deleted

        units = list(
            session.scalars(
                select(UnitConversion).where(UnitConversion.setting_id == setting_id).order_by(UnitConversion.id)
            )
        )
        units_by_source = {value.phrase_from: value for value in units}
        assert units_by_source["meter"].id == preserved_ids["unit"]
        assert units_by_source["foot"].phrase_to == "feet changed"
        assert units_by_source["foot"].id != changed_unit_id
        assert units_by_source["yard"].phrase_to == "yards"
        assert session.get(UnitConversion, changed_unit_id) is None


def test_copy_setting_copies_image_objects_to_independent_keys(database, image_storage):
    with Session(database) as session:
        owner = UserAccount(username="source-owner")
        copier = UserAccount(username="copier")
        session.add_all((owner, copier))
        session.flush()
        source = Setting(owner_id=owner.id, name="Public", public=True)
        session.add(source)
        session.flush()
        conversion = ImageConversion(
            setting_id=source.id,
            phrase="trigger",
            separation=1,
            explanation="",
            mutations=False,
        )
        session.add(conversion)
        session.flush()
        source_key = f"users/{owner.id}/settings/{source.id}/source.png"
        session.add(
            ImageConversionFile(
                image_conversion_id=conversion.id,
                object_key=source_key,
                size_bytes=5,
                position=0,
            )
        )
        image_storage.objects[source_key] = b"image"
        session.commit()

        storage_changes = ImageStorageChanges(image_storage)
        copied = copy_setting(session, source.id, copier.id, image_storage, storage_changes)
        session.commit()
        storage_changes.database_committed()

        copied_file = session.scalar(
            select(ImageConversionFile)
            .join(ImageConversion)
            .where(ImageConversion.setting_id == copied.id)
        )
        assert copied_file is not None
        assert copied_file.object_key != source_key
        assert image_storage.objects[copied_file.object_key] == b"image"
        assert image_storage.objects[source_key] == b"image"


def test_new_image_object_is_deleted_when_database_rolls_back(database, app_settings, image_storage):
    with Session(database) as session:
        owner = UserAccount(username="rollback-owner")
        session.add(owner)
        session.flush()
        setting = Setting(owner_id=owner.id, name="Rollback")
        session.add(setting)
        session.commit()
        upload = TrackingUploadFile(b"new-image", "new.png")
        form = FormData(
            [
                ("set_name", "Rollback"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
                ("image_token", "new-row"),
                ("image_phrase", "trigger"),
                ("image_separation", "1"),
                ("image_expl", ""),
                ("image_mutations", "False"),
                ("image_files_new-row", upload),
            ]
        )
        storage_changes = ImageStorageChanges(image_storage)

        asyncio.run(
            update_setting_from_form(
                session,
                setting.id,
                form,
                app_settings,
                image_storage,
                storage_changes,
            )
        )
        created_keys = set(image_storage.objects)
        session.rollback()
        storage_changes.database_rolled_back()

        assert created_keys
        assert not created_keys.intersection(image_storage.objects)
        assert session.scalar(select(ImageConversion).where(ImageConversion.setting_id == setting.id)) is None


def test_removing_image_conversion_deletes_its_object_after_commit(database, app_settings, image_storage):
    with Session(database) as session:
        owner = UserAccount(username="remove-owner")
        session.add(owner)
        session.flush()
        setting = Setting(owner_id=owner.id, name="Remove")
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
        object_key = f"users/{owner.id}/settings/{setting.id}/remove.png"
        session.add(
            ImageConversionFile(
                image_conversion_id=conversion.id,
                object_key=object_key,
                size_bytes=5,
                position=0,
            )
        )
        image_storage.objects[object_key] = b"image"
        session.commit()
        form = FormData(
            [
                ("set_name", "Remove"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
            ]
        )
        storage_changes = ImageStorageChanges(image_storage)

        asyncio.run(
            update_setting_from_form(
                session,
                setting.id,
                form,
                app_settings,
                image_storage,
                storage_changes,
            )
        )
        assert object_key in image_storage.objects
        session.commit()
        storage_changes.database_committed()

        assert object_key not in image_storage.objects
        assert object_key in image_storage.deleted


def test_update_setting_rejects_invalid_phrase_regex(database, app_settings, image_storage):
    with Session(database) as session:
        user = UserAccount(username="owner")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="Conversions")
        session.add(setting)
        session.flush()
        form = FormData(
            [
                ("set_name", "Conversions"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
                ("phrase_from", "["),
                ("phrase_to", "replacement"),
                ("phrase_direct", "True"),
                ("phrase_mutations", "False"),
                ("phrase_regex", "True"),
            ]
        )

        with pytest.raises(ValidationError, match="Invalid phrase regular expression"):
            asyncio.run(
                update_setting_from_form(
                    session,
                    setting.id,
                    form,
                    app_settings,
                    image_storage,
                    ImageStorageChanges(image_storage),
                )
            )


def test_update_setting_forces_regex_false_for_non_direct_phrase(database, app_settings, image_storage):
    with Session(database) as session:
        user = UserAccount(username="owner")
        session.add(user)
        session.flush()
        setting = Setting(owner_id=user.id, name="Conversions")
        session.add(setting)
        session.flush()
        form = FormData(
            [
                ("set_name", "Conversions"),
                ("set_public", "False"),
                ("set_empty", "False"),
                ("set_utf", "False"),
                ("set_coma_sep", "False"),
                ("set_expect_feet", "False"),
                ("phrase_from", "["),
                ("phrase_to", "replacement"),
                ("phrase_direct", "False"),
                ("phrase_mutations", "False"),
                ("phrase_regex", "True"),
            ]
        )

        asyncio.run(
            update_setting_from_form(
                session,
                setting.id,
                form,
                app_settings,
                image_storage,
                ImageStorageChanges(image_storage),
            )
        )

        phrase = session.scalar(select(PhraseConversion).where(PhraseConversion.setting_id == setting.id))
        assert phrase is not None
        assert phrase.direct is False
        assert phrase.regex is False


def _setting_form_for_length_case(field_name: str, value: str) -> FormData:
    fields: list[tuple[str, object]] = [
        ("set_name", value if field_name == "set_name" else "Conversions"),
        ("set_public", "False"),
        ("set_empty", "False"),
        ("set_utf", "False"),
        ("set_coma_sep", "False"),
        ("set_expect_feet", "False"),
    ]
    if field_name == "fandom":
        fields.extend(
            [
                ("fandom", value),
                ("fandom_active", "False"),
                ("fandom_separation", "1"),
                ("fandom_value_1", "False"),
                ("fandom_value_2", "False"),
            ]
        )
    elif field_name in {"unit_from", "unit_to"}:
        fields.extend(
            [
                ("unit_from", value if field_name == "unit_from" else "source"),
                ("unit_to", value if field_name == "unit_to" else "replacement"),
                ("unit_convert", "1"),
                ("unit_can", "False"),
            ]
        )
    elif field_name in {"phrase_from", "phrase_to"}:
        fields.extend(
            [
                ("phrase_from", value if field_name == "phrase_from" else "source"),
                ("phrase_to", value if field_name == "phrase_to" else "replacement"),
                ("phrase_direct", "False"),
                ("phrase_mutations", "False"),
                ("phrase_regex", "False"),
            ]
        )
    elif field_name in {"image_phrase", "image_expl"}:
        token = "new-length-test"
        fields.extend(
            [
                ("image_token", token),
                ("image_phrase", value if field_name == "image_phrase" else "trigger"),
                ("image_separation", "1"),
                ("image_expl", value if field_name == "image_expl" else "explanation"),
                ("image_mutations", "False"),
                (f"image_files_{token}", TrackingUploadFile(b"image", "image.png")),
            ]
        )
    return FormData(fields)


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("set_name", "Setting name"),
        ("fandom", "Fandom name"),
        ("unit_from", "Unit source phrase"),
        ("unit_to", "Unit replacement phrase"),
        ("phrase_from", "Phrase source"),
        ("phrase_to", "Phrase replacement"),
        ("image_phrase", "Image trigger phrase"),
        ("image_expl", "Image explanation"),
    ],
)
def test_update_setting_rejects_overlong_text_fields(
    database,
    app_settings,
    image_storage,
    field_name: str,
    message: str,
):
    with Session(database) as session:
        owner = UserAccount(username=f"length-{field_name}")
        session.add(owner)
        session.flush()
        setting = Setting(owner_id=owner.id, name="Conversions")
        session.add(setting)
        session.commit()
        changes = ImageStorageChanges(image_storage)

        with pytest.raises(ValidationError, match=message):
            asyncio.run(
                update_setting_from_form(
                    session,
                    setting.id,
                    _setting_form_for_length_case(field_name, "x" * 65),
                    app_settings,
                    image_storage,
                    changes,
                )
            )

        assert image_storage.objects == {}


def test_update_setting_accepts_text_at_the_explicit_limit(database, app_settings, image_storage):
    with Session(database) as session:
        owner = UserAccount(username="length-boundary")
        session.add(owner)
        session.flush()
        setting = Setting(owner_id=owner.id, name="Conversions")
        session.add(setting)
        session.commit()

        asyncio.run(
            update_setting_from_form(
                session,
                setting.id,
                _setting_form_for_length_case("set_name", "x" * 64),
                app_settings,
                image_storage,
                ImageStorageChanges(image_storage),
            )
        )
        session.flush()

        assert setting.name == "x" * 64
