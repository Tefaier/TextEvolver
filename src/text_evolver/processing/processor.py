import random
import re
from pathlib import Path

from text_evolver.processing.documents import DocumentAdapter, create_document_adapter
from text_evolver.processing.images import get_image, get_pokemon_image
from text_evolver.processing.process_config_builder import ProcessingConfiguration, configure_process_unit
from text_evolver.processing.text_analysis import (
    convert_utf8_symbols,
    find_in_clean,
    possible_mutations,
    redistribute_transformed_text,
    replace_iteration,
    string_empty,
    string_with_meaning,
    text_cleaner,
)


def image_choser(obj: dict):  # if an official image is chosen return None, otherwise return stored binary
    official_image = obj.get("image_path")
    images_num = len(obj["binary"]) + (0 if official_image is None else 1)
    random_num = random.randint(0, images_num - 1)
    if official_image is not None and random_num == 0:
        return None
    return obj["binary"][random_num - (0 if official_image is None else 1)]


def _compile_image_trigger_pattern(triggers: dict[str, object]) -> re.Pattern[str] | None:
    if not triggers:
        return None
    trigger_pattern = "|".join(
        sorted((re.escape(trigger) for trigger in triggers), key=len, reverse=True)
    )
    mutation_pattern = "|".join(re.escape(mutation) for mutation in possible_mutations)
    return re.compile(fr"\b(?i:({trigger_pattern}))({mutation_pattern})?\b")


class ProcessUnit:
    def __init__(self, configuration: ProcessingConfiguration):
        self.settings: dict = {}
        self.pokemons_list: dict = {}
        self.units_list: dict = {}
        self.word_conversions: dict = {}
        self.direct_conversions: dict = {}
        self.extra_img_list: dict = {}
        self.word_counter = 0
        configure_process_unit(self, configuration)
        self.pokemons_navigation_map = {name.casefold(): name for name in self.pokemons_list}
        self.images_navigation_map = {name.casefold(): name for name in self.extra_img_list}
        self.pokemon_trigger_pattern = _compile_image_trigger_pattern(self.pokemons_list)
        self.image_trigger_pattern = _compile_image_trigger_pattern(self.extra_img_list)

    def images_locate(self, string: str, document: DocumentAdapter) -> None:
        clean_text = text_cleaner(string, self.settings)
        if self.settings["pokemon"] and self.pokemon_trigger_pattern is not None:
            results = self.pokemon_trigger_pattern.findall(clean_text)
            for result in results:
                key = self.pokemons_navigation_map[result[0].casefold()]
                item = self.pokemons_list[key]
                if item["last word"] is None or (
                    item["last word"] + item["separation"] < self.word_counter and item["separation"] != 1
                ):
                    image_data = get_pokemon_image(
                        key,
                        self.settings,
                        item["image_path"],
                        item["height"],
                        item["weight"],
                        image_choser(item),
                        item["explanation"],
                    )
                    if image_data is not None:
                        item["last word"] = self.word_counter
                        document.insert_image_before_last_block(image_data)
        if self.image_trigger_pattern is not None:
            results = self.image_trigger_pattern.findall(clean_text)
            for result in results:
                key = self.images_navigation_map[result[0].casefold()]
                item = self.extra_img_list[key]
                if (result[1] == "" or item["mutation"]) and (
                    item["last word"] is None
                    or (
                        item["last word"] + item["separation"] < self.word_counter
                        and item["separation"] != 1
                    )
                ):
                    image_data = get_image(image_choser(item), key, key, item["explanation"])
                    if image_data is not None:
                        item["last word"] = self.word_counter
                        document.insert_image_before_last_block(image_data)

    def direct_replace(self, string: str) -> str:
        if self.settings["convert to utf"]:
            string = convert_utf8_symbols(string)
        for source, replace_rules in self.direct_conversions.items():
            if replace_rules.regex:
                string = re.sub(source, replace_rules.replace_with, string)
            else:
                string = string.replace(source, replace_rules.replace_with)
        return string

    def text_alteration(self, text: str) -> str:
        words = text.split(" ")
        clean_text = text_cleaner(text, self.settings).split(" ")

        for phrase, replace_rules in self.units_list.items():
            results = find_in_clean(
                clean_text,
                phrase,
                replace_rules,
            )
            if results["found"]:
                try:
                    words = replace_iteration(results["replace_map"], words.copy())
                    text = " ".join(words)
                    clean_text = text_cleaner(text, self.settings).split(" ")
                except ValueError:
                    pass
        for phrase, replace_rules in self.word_conversions.items():
            results = find_in_clean(
                clean_text,
                phrase,
                replace_rules,
            )
            if results["found"]:
                try:
                    words = replace_iteration(results["replace_map"], words.copy())
                    text = " ".join(words)
                    clean_text = text_cleaner(text, self.settings).split(" ")
                except ValueError:
                    pass
        return " ".join(words)

    def process_document(self, document: DocumentAdapter) -> Path:
        block_parts: list[str] = []
        while (part := document.read_part()) is not None:
            if part.starts_block:
                block_parts = []
            block_parts.append(part.text)
            if not part.ends_block:
                continue

            text = self.direct_replace(part.block_text)
            transformed_text = text if string_empty(text) else self.text_alteration(text)
            if self.settings["clean empty"] and not string_with_meaning(transformed_text):
                document.remove_last_block()
                continue
            self.word_counter += len(text_cleaner(transformed_text, self.settings).split())
            self.images_locate(transformed_text, document)
            document.overwrite_last_block_parts(redistribute_transformed_text(block_parts, transformed_text))
        return document.save()


def process_files(
    configuration: ProcessingConfiguration,
    origin_directory: str | Path,
    output_directory: str | Path,
) -> int:
    """Process every supported document in origin_directory."""
    origin = Path(origin_directory)
    output = Path(output_directory)
    if not origin.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {origin}")
    output.mkdir(parents=True, exist_ok=True)
    processed = 0
    for source in sorted(origin.iterdir()):
        if not source.is_file():
            continue
        processor = ProcessUnit(configuration)
        document = create_document_adapter(source, output / source.name)
        processor.process_document(document)
        processed += 1
    return processed
