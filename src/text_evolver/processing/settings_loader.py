from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from text_evolver.db.models import Fandom, ImageConversion, PhraseConversion, Setting, UnitConversion
from text_evolver.fandoms import FandomName
from text_evolver.processing.pokemon_cache import PokemonRecord, load_pokemon_cache


@dataclass(frozen=True)
class ProcessingConfiguration:
    use_comma_separator: bool
    expect_feet: bool
    clean_empty: bool
    convert_to_utf: bool
    fandoms: tuple[dict[str, object], ...]
    units: tuple[dict[str, object], ...]
    phrases: tuple[dict[str, object], ...]
    images: tuple[dict[str, object], ...]
    pokemons: tuple[PokemonRecord, ...] = ()


def load_processing_configuration(
    session: Session,
    setting_id: int,
    temp_root: Path | None = None,
) -> ProcessingConfiguration:
    '''Reads settings tables to collect setting object fully'''
    setting = session.get(Setting, setting_id)
    if setting is None:
        raise LookupError(f"Setting {setting_id} does not exist")
    fandoms = tuple(session.scalars(select(Fandom).where(Fandom.setting_id == setting_id).order_by(Fandom.id)))
    units = session.scalars(
        select(UnitConversion).where(UnitConversion.setting_id == setting_id).order_by(UnitConversion.id)
    )
    phrases = session.scalars(
        select(PhraseConversion).where(PhraseConversion.setting_id == setting_id).order_by(PhraseConversion.id)
    )
    images = session.scalars(
        select(ImageConversion).where(ImageConversion.setting_id == setting_id).order_by(ImageConversion.id)
    )
    return ProcessingConfiguration(
        use_comma_separator=setting.use_comma_separator,
        expect_feet=setting.expect_feet,
        clean_empty=setting.clean_empty,
        convert_to_utf=setting.convert_to_utf,
        fandoms=tuple(
            {
                "name": value.name,
                "active": value.active,
                "separation": value.separation,
                "support_value_1": value.support_value_1,
                "support_value_2": value.support_value_2,
            }
            for value in fandoms
        ),
        units=tuple(
            {
                "phrase_from": value.phrase_from,
                "phrase_to": value.phrase_to,
                "conversion": value.conversion,
                "can_be_word": value.can_be_word,
            }
            for value in units
        ),
        phrases=tuple(
            {
                "phrase_from": value.phrase_from,
                "phrase_to": value.phrase_to,
                "direct": value.direct,
                "mutations": value.mutations,
            }
            for value in phrases
        ),
        images=tuple(
            {
                "phrase": value.phrase,
                "separation": value.separation,
                "explanation": value.explanation,
                "mutations": value.mutations,
                "images": value.images,
            }
            for value in images
        ),
        pokemons=(
            load_pokemon_cache(temp_root)
            if temp_root is not None and any(value.name == FandomName.POKEMONS and value.active for value in fandoms)
            else ()
        ),
    )


def configure_process_unit(unit: object, configuration: ProcessingConfiguration) -> None:
    '''Applies configuration to given ProcessUnit'''
    unit.settings.update(
        {
            "pokemon": False,
            "coma in digits": configuration.use_comma_separator,
            "feet check": configuration.expect_feet,
            "clean empty": configuration.clean_empty,
            "convert to utf": configuration.convert_to_utf,
        }
    )
    for fandom in configuration.fandoms:
        match fandom["name"]:
            case FandomName.POKEMONS:
                active_and_loaded = bool(fandom["active"] and configuration.pokemons)
                unit.settings["pokemon"] = active_and_loaded
                if active_and_loaded:
                    unit.settings["show_pokemon_weight"] = fandom["support_value_1"]
                    unit.settings["show_pokemon_height"] = fandom["support_value_2"]
                    _load_pokemons(unit, configuration.pokemons, int(fandom["separation"]))
    for value in configuration.units:
        unit.units_list[value["phrase_from"]] = {
            "split": str(value["phrase_to"]).split(" "),
            "new unit": value["phrase_to"],
            "conversion": value["conversion"],
            "can be word": value["can_be_word"],
        }
    for value in configuration.images:
        binaries = str(value["images"]).split("*")
        if value["phrase"] in unit.pokemons_list:
            item = unit.pokemons_list[value["phrase"]]
            item.update(
                {
                    "separation": value["separation"],
                    "explanation": value["explanation"],
                }
            )
            item["binary"].extend(binaries)
        else:
            unit.extra_img_list[value["phrase"]] = {
                "split": str(value["phrase"]).split(" "),
                "separation": value["separation"],
                "last word": None,
                "mutation": value["mutations"],
                "binary": binaries,
                "explanation": value["explanation"],
            }
    for value in configuration.phrases:
        if value["direct"]:
            unit.direct_conversions[value["phrase_from"]] = value["phrase_to"]
        else:
            unit.word_conversions[value["phrase_from"]] = {
                "split": str(value["phrase_from"]).split(" "),
                "new words": value["phrase_to"],
                "mutation": value["mutations"],
            }
    if configuration.expect_feet and "feet" not in unit.units_list:
        unit.units_list["feet"] = {
            "split": ["feet"],
            "new unit": "cm",
            "conversion": 30.3,
            "can be word": True,
        }


def _load_pokemons(unit: object, pokemons: tuple[PokemonRecord, ...], default_separation: int) -> None:
    for pokemon in pokemons:
        unit.pokemons_list.setdefault(
            pokemon.name,
            {
                "split": pokemon.name.split(" "),
                "separation": default_separation,
                "image_path": pokemon.image_path,
                "height": pokemon.height,
                "weight": pokemon.weight,
                "last word": None,
                "binary": [],
                "explanation": None,
            },
        )
