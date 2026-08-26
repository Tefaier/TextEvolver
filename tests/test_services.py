import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from text_evolver.db.models import (
    Fandom,
    ImageConversion,
    PhraseConversion,
    Setting,
    UnitConversion,
    UserAccount,
)
from text_evolver.services import _calculate_row_changes, update_setting_from_form


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
            images="encoded-image",
        )
        session.add_all((fandom, preserved_unit, changed_unit, phrase, image))
        session.commit()

        setting_id = setting.id
        preserved_ids = {
            "fandom": fandom.id,
            "unit": preserved_unit.id,
            "phrase": phrase.id,
            "image": image.id,
        }
        changed_unit_id = changed_unit.id

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
                ("image_origin_name", "Pikachu"),
                ("image_phrase", "Pikachu"),
                ("image_separation", "50"),
                ("image_expl", "Electric mouse"),
                ("image_mutations", "True"),
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
        assert (
            session.scalar(select(ImageConversion.id).where(ImageConversion.setting_id == setting_id))
            == preserved_ids["image"]
        )

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
