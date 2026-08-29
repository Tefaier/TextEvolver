import random
import re
from pathlib import Path

from text_evolver.processing.documents import DocumentAdapter, create_document_adapter
from text_evolver.processing.images import get_image, get_pokemon_image
from text_evolver.processing.process_config_builder import ProcessingConfiguration, configure_process_unit
from text_evolver.processing.text_analysis import (
    redistribute_transformed_text,
    convert_utf8_symbols,
    find_in_clean,
    possible_mutations,
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


class ProcessUnit:
    def __init__(self, configuration: ProcessingConfiguration):
        self.settings: dict = {}
        self.pokemons_list: dict = {}
        self.units_list: dict = {}
        self.word_conversions: dict = {}
        self.direct_conversions: dict = {}
        self.extra_img_list: dict = {}
        self.word_counter = 0
        self.mutations_string = "|".join(possible_mutations)
        configure_process_unit(self, configuration)
        self.pokemons_navigation_map = [[name.lower(), name] for name in self.pokemons_list]
        self.images_navigation_map = [[name.lower(), name] for name in self.extra_img_list]

    def images_locate(self, string: str, document: DocumentAdapter) -> None:
        clean_text = text_cleaner(string, self.settings)
        if self.settings["pokemon"]:
            results = re.findall(
                fr"\b(?i:({'|'.join(self.pokemons_list.keys())}))({self.mutations_string})?\b",
                clean_text,
            )
            for result in results:
                key = next(value[1] for value in self.pokemons_navigation_map if value[0] == result[0].lower())
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
        if self.extra_img_list:
            results = re.findall(
                fr"\b(?i:({'|'.join(self.extra_img_list.keys())}))({self.mutations_string})?\b",
                clean_text,
            )
            for result in results:
                key = next(value[1] for value in self.images_navigation_map if value[0] == result[0].lower())
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
        for source, replacement in self.direct_conversions.items():
            string = string.replace(source, replacement)
        return string

    def text_alteration(self, text: str) -> str:
        words = text.split(" ")
        clean_text = text_cleaner(text, self.settings).split(" ")

        for phrase, item in self.units_list.items():
            results = find_in_clean(
                clean_text,
                item["split"],
                phrase,
                False,
                {
                    "units": item["conversion"],
                    "replace_with": item["new unit"],
                    "feet": self.units_list["feet"]["conversion"] if self.settings["feet check"] else None,
                    "can be word": item["can be word"],
                },
            )
            if results["found"]:
                try:
                    words = replace_iteration(results["replace_map"], words.copy())
                    text = " ".join(words)
                    clean_text = text_cleaner(text, self.settings).split(" ")
                except Exception:
                    pass
        for phrase, item in self.word_conversions.items():
            results = find_in_clean(
                clean_text,
                item["split"],
                phrase,
                item["mutation"],
                {
                    "units": None,
                    "replace_with": item["new words"],
                    "feet": None,
                    "can be word": None,
                },
            )
            if results["found"]:
                try:
                    words = replace_iteration(results["replace_map"], words.copy())
                    text = " ".join(words)
                    clean_text = text_cleaner(text, self.settings).split(" ")
                except Exception:
                    pass
        return " ".join(words)

    def process_document(self, document: DocumentAdapter) -> Path:
        block_parts: list[str] = []
        while (part := document.read_part()) is not None:
            if part.starts_block:
                block_parts = []
                if self.settings["clean empty"] and not string_with_meaning(part.block_text):
                    document.remove_last_block()
                    continue
                self.images_locate(part.block_text, document)
            block_parts.append(part.text)
            if not part.ends_block:
                continue

            text = self.direct_replace(part.block_text)
            transformed_text = text if string_empty(text) else self.text_alteration(text)
            self.word_counter += len(text_cleaner(transformed_text, self.settings).split())
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
