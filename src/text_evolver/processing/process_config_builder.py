from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from text_evolver.db.models import Fandom, ImageConversion, PhraseConversion, Setting, UnitConversion
from text_evolver.fandoms import FandomName
from text_evolver.processing.pokemon_cache import PokemonRecord, load_pokemon_cache
from text_evolver.processing.text_analysis import ReplaceRules


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

    feet_conversion = None
    if configuration.expect_feet:
        feet_conversion = next(
            (
                float(value["conversion"])
                for value in configuration.units
                if value["phrase_from"] == "feet"
            ),
            30.3,
        )
    for value in configuration.units:
        phrase_from = str(value["phrase_from"])
        unit.units_list[value["phrase_from"]] = {
            "replace_rules": ReplaceRules(
                replace_with=str(value["phrase_to"]),
                lookup_length=len(phrase_from.split()),
                units=float(value["conversion"]),
                feet=feet_conversion,
                can_be_word=bool(value["can_be_word"]),
            ),
        }
    if configuration.expect_feet and "feet" not in unit.units_list:
        unit.units_list["feet"] = {
            "replace_rules": ReplaceRules(
                replace_with="cm",
                lookup_length=1,
                units=30.3,
                feet=feet_conversion,
                can_be_word=True,
            ),
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
            phrase_from = str(value["phrase_from"])
            unit.word_conversions[value["phrase_from"]] = {
                "replace_rules": ReplaceRules(
                    replace_with=str(value["phrase_to"]),
                    lookup_length=len(phrase_from.split()),
                    mutations=bool(value["mutations"]),
                ),
            }


def _load_pokemons(unit: object, pokemons: tuple[PokemonRecord, ...], default_separation: int) -> None:
    '''Loads pokemons data into ProcessUnit'''
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
