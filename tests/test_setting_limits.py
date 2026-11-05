from io import BytesIO
from uuid import uuid4

import pytest
from conftest import csrf_from
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from text_evolver.db.models import ImageConversion, ImageConversionFile, ProcessingJob, Setting, UserAccount
from text_evolver.image_storage import ImageStorageChanges
from text_evolver.services import _lock_setting_for_job, copy_setting, parse_setting_form, search_settings
from text_evolver.web.routines.settings import update_setting
from text_evolver.web.validation_utils import (
    MATCHER_MAX_LENGTH,
    SETTING_ELEMENT_LIMIT,
    SETTING_IMAGES_MAX_BYTES,
    ValidationError,
)


def setting_fields():
    return [
        ("set_name", "Limits"), ("set_public", "False"), ("set_empty", "False"),
        ("set_utf", "False"), ("set_coma_sep", "False"), ("set_expect_feet", "False"),
    ]


def unit_fields(source="metre"):
    return [("unit_from", source), ("unit_to", "m"), ("unit_convert", "1"), ("unit_can", "False")]


def phrase_fields(source="source"):
    return [
        ("phrase_from", source), ("phrase_to", "replacement"), ("phrase_direct", "True"),
        ("phrase_mutations", "False"), ("phrase_regex", "False"),
    ]


def image_fields(token, upload_size=None, phrase="trigger"):
    fields = [
        ("image_token", token), ("image_phrase", phrase), ("image_separation", "1"),
        ("image_expl", ""), ("image_mutations", "False"),
    ]
    if upload_size is not None:
        fields.append((f"image_files_{token}", UploadFile(BytesIO(b"x"), filename="image.png", size=upload_size)))
    return fields


@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_element_limit_is_combined_across_rule_types(extra, valid):
    fields = setting_fields()
    for _ in range(SETTING_ELEMENT_LIMIT // 2):
        fields.extend(unit_fields())
    for _ in range(SETTING_ELEMENT_LIMIT // 2 + extra):
        fields.extend(phrase_fields())
    if valid:
        data = parse_setting_form(FormData(fields))
        assert len(data.units) + len(data.phrases) == SETTING_ELEMENT_LIMIT
    else:
        with pytest.raises(ValidationError, match="elements in total"):
            parse_setting_form(FormData(fields))


def test_image_and_fandom_rows_count_toward_same_element_limit():
    fields = setting_fields() + unit_fields() * (SETTING_ELEMENT_LIMIT - 1)
    fields.extend(image_fields("new", 1))
    fields.extend([
        ("fandom", "Pokemons"), ("fandom_active", "False"), ("fandom_separation", "1"),
        ("fandom_value_1", "False"), ("fandom_value_2", "False"),
    ])
    with pytest.raises(ValidationError, match="elements in total"):
        parse_setting_form(FormData(fields))


@pytest.mark.parametrize("kind", ["unit", "phrase", "image", "regex"])
@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_matcher_lengths(kind, extra, valid):
    source = "я" * (MATCHER_MAX_LENGTH + extra)
    if kind == "unit":
        rows = unit_fields(source)
    elif kind == "image":
        rows = image_fields("new", 1, source)
    else:
        rows = phrase_fields(source)
        if kind == "regex":
            rows = [(key, "True" if key == "phrase_regex" else value) for key, value in rows]
    form = FormData(setting_fields() + rows)
    if valid:
        parse_setting_form(form)
    else:
        with pytest.raises(ValidationError, match="128 characters"):
            parse_setting_form(form)


@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_resulting_size_of_new_images_is_checked_across_rows(database, image_storage, extra, valid):
    with Session(database) as db:
        owner = UserAccount(id=uuid4(), username="new-image-total")
        db.add(owner)
        db.flush()
        setting = Setting(owner_id=owner.id, name="Original")
        db.add(setting)
        db.commit()
        fields = setting_fields() + image_fields("one", SETTING_IMAGES_MAX_BYTES // 2)
        fields.extend(image_fields("two", SETTING_IMAGES_MAX_BYTES // 2 + extra))
        data = parse_setting_form(FormData(fields))
        changes = ImageStorageChanges(image_storage)
        if valid:
            update_setting(db, setting.id, data, image_storage, changes)
            assert db.scalar(select(func.sum(ImageConversionFile.size_bytes))) == SETTING_IMAGES_MAX_BYTES
        else:
            with pytest.raises(ValidationError, match="512 MiB"):
                update_setting(db, setting.id, data, image_storage, changes)
            assert setting.name == "Original"
            assert image_storage.objects == {}


@pytest.mark.parametrize("operation", ["retain", "add", "replace", "remove"])
@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_resulting_image_size_accounts_for_all_changes(database, image_storage, operation, extra, valid):
    half_limit = SETTING_IMAGES_MAX_BYTES // 2
    with Session(database) as db:
        owner = UserAccount(id=uuid4(), username="resulting-image-total")
        db.add(owner)
        db.flush()
        setting = Setting(owner_id=owner.id, name="Original")
        db.add(setting)
        db.flush()
        images = [ImageConversion(setting_id=setting.id, phrase=str(index), explanation="") for index in range(2)]
        db.add_all(images)
        db.flush()
        stored_sizes = [half_limit, SETTING_IMAGES_MAX_BYTES]
        if operation == "retain":
            stored_sizes = [half_limit, half_limit + extra]
        elif operation == "remove":
            stored_sizes = [SETTING_IMAGES_MAX_BYTES + extra, SETTING_IMAGES_MAX_BYTES]
        for index, image in enumerate(images):
            db.add(ImageConversionFile(
                image_conversion_id=image.id, object_key=f"stored-{index}",
                size_bytes=stored_sizes[index], position=0,
            ))
            image_storage.objects[f"stored-{index}"] = b"stored-image"
        db.commit()
        fields = setting_fields() + image_fields(f"existing-{images[0].id}")
        if operation == "retain":
            fields.extend(image_fields(f"existing-{images[1].id}"))
        elif operation == "add":
            fields.extend(image_fields("new", half_limit + extra))
        elif operation == "replace":
            fields.extend(image_fields(f"existing-{images[1].id}", half_limit + extra))
        data = parse_setting_form(FormData(fields))
        changes = ImageStorageChanges(image_storage)
        if valid:
            update_setting(db, setting.id, data, image_storage, changes)
            db.commit()
            changes.database_committed()
            assert db.scalar(select(func.sum(ImageConversionFile.size_bytes))) == SETTING_IMAGES_MAX_BYTES
            assert "stored-0" in image_storage.objects
            assert ("stored-1" in image_storage.objects) == (operation == "retain")
        else:
            with pytest.raises(ValidationError, match="512 MiB"):
                update_setting(db, setting.id, data, image_storage, changes)
            assert setting.name == "Original"
            assert db.scalar(select(func.sum(ImageConversionFile.size_bytes))) == sum(stored_sizes)
            assert set(image_storage.objects) == {"stored-0", "stored-1"}
            assert changes.created == []
            assert changes.obsolete == []


@pytest.mark.parametrize("replace_existing", [False, True])
def test_image_budget_includes_retained_files_and_excludes_replaced_files(database, image_storage, replace_existing):
    with Session(database) as db:
        owner = UserAccount(id=uuid4(), username="budget")
        db.add(owner)
        db.flush()
        setting = Setting(owner_id=owner.id, name="Original")
        db.add(setting)
        db.flush()
        image = ImageConversion(setting_id=setting.id, phrase="trigger", explanation="")
        db.add(image)
        db.flush()
        old_file = ImageConversionFile(
            image_conversion_id=image.id, object_key="old", size_bytes=SETTING_IMAGES_MAX_BYTES, position=0,
        )
        db.add(old_file)
        db.commit()
        image_storage.objects["old"] = b"old-image"
        fields = setting_fields() + image_fields(f"existing-{image.id}", 1 if replace_existing else None)
        if not replace_existing:
            fields.extend(image_fields("new", 1))
        data = parse_setting_form(FormData(fields))
        changes = ImageStorageChanges(image_storage)
        if replace_existing:
            update_setting(db, setting.id, data, image_storage, changes)
            assert "old" in image_storage.objects
            db.commit()
            changes.database_committed()
            assert "old" not in image_storage.objects
            assert db.scalar(select(ImageConversionFile.size_bytes)) == 1
        else:
            with pytest.raises(ValidationError, match="512 MiB"):
                update_setting(db, setting.id, data, image_storage, changes)
            assert setting.name == "Original"
            assert image_storage.objects == {"old": b"old-image"}


@pytest.mark.parametrize("action", ["save", "save_run", "run", "copy"])
def test_pending_deletion_blocks_setting_operations(registered_client, database, action):
    with Session(database) as db:
        owner = db.scalar(select(UserAccount).where(UserAccount.username == "reader"))
        setting = Setting(owner_id=owner.id, name="Pending", deletion_requested=True)
        db.add(setting)
        db.commit()
        setting_id = setting.id
    page = registered_client.get(f"/setting/{setting_id}")
    assert "Pending deletion" in page.text
    assert '<fieldset class="setting-fields" disabled>' in page.text
    fields = dict(setting_fields())
    fields[action] = "1"
    fields["csrf_token"] = csrf_from(page)
    response = registered_client.post(
        f"/setting/{setting_id}", data=fields, headers={"Accept": "application/json"},
    )
    assert response.status_code == 422
    assert "pending deletion" in response.json()["message"]
    with Session(database) as db:
        assert db.get(Setting, setting_id).name == "Pending"
        assert db.scalar(select(ProcessingJob)) is None


def test_pending_deletion_blocks_service_calls_and_search(database, image_storage):
    with Session(database) as db:
        owner = UserAccount(id=uuid4(), username="pending-service")
        db.add(owner)
        db.flush()
        setting = Setting(owner_id=owner.id, name="Pending", public=True, deletion_requested=True)
        db.add(setting)
        db.commit()
        with pytest.raises(ValidationError, match="pending deletion"):
            _lock_setting_for_job(db, setting.id)
        with pytest.raises(ValidationError, match="pending deletion"):
            copy_setting(db, setting.id, owner.id, image_storage, ImageStorageChanges(image_storage))
        with pytest.raises(ValidationError, match="pending deletion"):
            update_setting(db, setting.id, parse_setting_form(FormData(setting_fields())),
                           image_storage, ImageStorageChanges(image_storage))
        assert search_settings(db, owner.id, "Pending", 1, 10)["settings"] == []


@pytest.mark.parametrize("field,value", [("unit_convert", "NaN"), ("unit_convert", "inf"),
                                        ("unit_convert", "bad"), ("image_separation", "bad")])
def test_invalid_numbers_raise_validation_errors(field, value):
    rows = image_fields("new", 1) if field == "image_separation" else unit_fields()
    rows = [(key, value if key == field else current) for key, current in rows]
    with pytest.raises(ValidationError):
        parse_setting_form(FormData(setting_fields() + rows))
