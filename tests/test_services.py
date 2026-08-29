import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    PhraseConversion,
    Setting,
    UnitConversion,
    UserAccount,
)
from text_evolver.services import (
    ImageSubmission,
    ValidationError,
    _acquire_create_job_lock,
    _calculate_row_changes,
    _materialize_image_submissions,
    _read_upload_with_limit,
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


def test_oversized_upload_is_rejected_before_content_is_read():
    upload = TrackingUploadFile(b"123456", "large.png")

    with pytest.raises(ValidationError, match="size limit"):
        asyncio.run(_read_upload_with_limit(upload, available_bytes=5))

    assert upload.read_sizes == []


def test_empty_upload_is_rejected_before_content_is_read():
    upload = TrackingUploadFile(b"", "empty.png")

    with pytest.raises(ValidationError, match="is empty"):
        asyncio.run(_read_upload_with_limit(upload, available_bytes=5))

    assert upload.read_sizes == []


def test_combined_upload_size_is_checked_before_any_content_is_read():
    first = TrackingUploadFile(b"1234", "first.png")
    second = TrackingUploadFile(b"5678", "second.png")
    submission = ImageSubmission("Example", 1, "", False, "", (first, second))

    with pytest.raises(ValidationError, match="size limit"):
        asyncio.run(_materialize_image_submissions([submission], setting_limit_bytes=7))

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


def test_update_setting_preserves_unchanged_rows(database, app_settings):
    with Session(database) as session:
        user = UserAccount(username="owner", password_hash="hash")
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
        )
        image = ImageConversion(
            setting_id=setting.id,
            phrase="Pikachu",
            separation=50,
            explanation="Electric mouse",
            mutations=True,
            images=base64.b64encode(b"existing").decode("ascii"),
        )
        replaced_image = ImageConversion(
            setting_id=setting.id,
            phrase="Eevee",
            separation=75,
            explanation="Evolution Pokemon",
            mutations=False,
            images=base64.b64encode(b"old-eevee").decode("ascii"),
        )
        session.add_all((fandom, preserved_unit, changed_unit, phrase, image, replaced_image))
        session.commit()

        setting_id = setting.id
        preserved_ids = {
            "fandom": fandom.id,
            "unit": preserved_unit.id,
            "phrase": phrase.id,
            "image": image.id,
        }
        changed_unit_id = changed_unit.id
        replaced_image_id = replaced_image.id
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

        asyncio.run(update_setting_from_form(session, setting_id, form, app_settings))
        session.commit()
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
        assert images["Pikachu"].images == base64.b64encode(b"existing").decode("ascii")
        assert images["Eevee"].id != replaced_image_id
        assert images["Eevee"].images == "*".join(
            (
                base64.b64encode(replacement_data).decode("ascii"),
                base64.b64encode(replacement_data_2).decode("ascii"),
            )
        )
        assert replacement_upload.read_sizes == [len(replacement_data)]
        assert replacement_upload_2.read_sizes == [len(replacement_data_2)]

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
