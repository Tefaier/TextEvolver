from pathlib import Path

import ebooklib
from docx import Document
from ebooklib import epub

from text_evolver.processing import processor
from text_evolver.processing.documents import create_document_adapter
from text_evolver.processing.process_config_builder import ProcessingConfiguration
from text_evolver.processing.processor import process_files
from text_evolver.processing.text_analysis import ReplaceRules


def configuration(
    *,
    clean_empty: bool = False,
    expect_feet: bool = False,
    units: tuple[dict[str, object], ...] = (),
    phrases: tuple[dict[str, object], ...] | None = None,
    images: tuple[dict[str, object], ...] = (),
) -> ProcessingConfiguration:
    return ProcessingConfiguration(
        use_comma_separator=False,
        expect_feet=expect_feet,
        clean_empty=clean_empty,
        convert_to_utf=False,
        fandoms=(),
        units=units,
        phrases=(
            phrases
            if phrases is not None
            else ({"phrase_from": "old", "phrase_to": "new", "direct": True, "mutations": False},)
        ),
        images=images,
    )


def run_one(tmp_path: Path, filename: str) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin"
    output = tmp_path / "output"
    origin.mkdir()
    return origin, output, origin / filename


def test_html_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.html")
    source.write_text("<html><body><p>An <strong>old</strong> road.</p></body></html>", encoding="utf-8")

    assert process_files(configuration(), origin, output) == 1

    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p>An <strong>new</strong> road.</p></body></html>"
    )
    assert not source.exists()


def test_fb2_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.fb2")
    source.write_text(
        "<FictionBook><body><section><p>An <emphasis>old</emphasis> road.</p></section></body></FictionBook>",
        encoding="utf-8",
    )

    process_files(configuration(), origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<FictionBook><body><section><p>An <emphasis>new</emphasis> road.</p></section></body></FictionBook>"
    )


def test_docx_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.docx")
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("An ")
    converted_run = paragraph.add_run("old")
    converted_run.bold = True
    paragraph.add_run(" road.")
    document.save(source)

    process_files(configuration(), origin, output)

    processed_paragraph = Document(output / source.name).paragraphs[0]
    assert "new road" in processed_paragraph.text
    assert processed_paragraph.runs[1].text == "new"
    assert processed_paragraph.runs[1].bold is True


def test_docx_replacement_crosses_runs(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "split.docx")
    document = Document()
    paragraph = document.add_paragraph()
    first_run = paragraph.add_run("old")
    first_run.bold = True
    paragraph.add_run(" road")
    document.save(source)
    cross_part_configuration = configuration(
        phrases=(
            {
                "phrase_from": "old road",
                "phrase_to": "brand new path",
                "direct": True,
                "mutations": False,
            },
        )
    )

    process_files(cross_part_configuration, origin, output)

    processed_paragraph = Document(output / source.name).paragraphs[0]
    assert processed_paragraph.text == "brand new path"
    assert [run.text for run in processed_paragraph.runs] == ["brand", " new path"]
    assert processed_paragraph.runs[0].bold is True


def test_epub_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.epub")
    book = epub.EpubBook()
    book.set_identifier("fixture")
    book.set_title("Fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>An <strong>old</strong> road.</p></body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(source, book)

    process_files(configuration(), origin, output)

    processed = epub.read_epub(output / source.name)
    chapter_content = next(
        item.get_content()
        for item in processed.get_items()
        if item.get_type() == ebooklib.ITEM_DOCUMENT and item.file_name == "chapter.xhtml"
    )
    assert chapter_content == (
        b"<?xml version='1.0' encoding='utf-8'?>\n"
        b"<!DOCTYPE html>\n"
        b'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        b'epub:prefix="z3998: http://www.daisy.org/z3998/2012/vocab/structure/#" lang="en" xml:lang="en">\n'
        b"  <head/>\n"
        b"  <body><p>An <strong>new</strong> road.</p>\n"
        b"</body>\n"
        b"</html>\n"
    )


def test_direct_replacement_crosses_markup_parts(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "split.html")
    source.write_text("<html><body><p><strong>old</strong> road</p></body></html>", encoding="utf-8")
    split_phrase_configuration = configuration(
        phrases=(
            {
                "phrase_from": "old road",
                "phrase_to": "brand new path",
                "direct": True,
                "mutations": False,
            },
        )
    )

    process_files(split_phrase_configuration, origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p><strong>brand</strong> new path</p></body></html>"
    )


def test_direct_phrase_uses_regex_only_when_enabled(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "regex.html")
    source.write_text("<html><body><p>Item 123 and a+b and aaab.</p></body></html>", encoding="utf-8")
    regex_configuration = configuration(
        phrases=(
            {
                "phrase_from": r"\d+",
                "phrase_to": "#",
                "direct": True,
                "mutations": False,
                "regex": True,
            },
            {
                "phrase_from": "a+b",
                "phrase_to": "literal",
                "direct": True,
                "mutations": False,
                "regex": False,
            },
        )
    )

    process_files(regex_configuration, origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p>Item # and literal and aaab.</p></body></html>"
    )


def test_non_direct_phrase_ignores_regex_flag(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "word-regex.html")
    source.write_text("<html><body><p>A colour and color.</p></body></html>", encoding="utf-8")
    regex_configuration = configuration(
        phrases=(
            {
                "phrase_from": "colou?r",
                "phrase_to": "shade",
                "direct": False,
                "mutations": False,
                "regex": True,
            },
        )
    )

    process_files(regex_configuration, origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p>A colour and color.</p></body></html>"
    )
    process_unit = processor.ProcessUnit(regex_configuration)
    assert process_unit.word_conversions["colou?r"].regex is False


def test_non_direct_replacement_updates_whole_phrase(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "phrase.html")
    source.write_text("<html><body><p>An old-road.</p></body></html>", encoding="utf-8")
    phrase_configuration = configuration(
        phrases=(
            {
                "phrase_from": "old road",
                "phrase_to": "brand new path",
                "direct": False,
                "mutations": False,
            },
        )
    )

    process_files(phrase_configuration, origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p>An brand-new path.</p></body></html>"
    )


def test_conversion_rules_are_built_when_process_unit_is_configured():
    process_unit = processor.ProcessUnit(
        configuration(
            units=(
                {
                    "phrase_from": "miles",
                    "phrase_to": "kilometers",
                    "conversion": 1.6,
                    "can_be_word": True,
                },
                {
                    "phrase_from": "square miles",
                    "phrase_to": "km²",
                    "conversion": 2.6,
                    "can_be_word": False,
                },
            ),
            phrases=(
                {
                    "phrase_from": "old road",
                    "phrase_to": "new path",
                    "direct": False,
                    "mutations": True,
                },
            ),
        )
    )

    assert process_unit.units_list["miles"] == ReplaceRules(
        replace_with="kilometers",
        lookup_length=1,
        units=1.6,
        can_be_word=True,
    )
    assert process_unit.word_conversions["old road"] == ReplaceRules(
        replace_with="new path",
        lookup_length=2,
        mutations=True,
    )
    assert process_unit.units_list["square miles"].lookup_length == 2

    assert process_unit.units_list["miles"] is not None
    assert process_unit.word_conversions["old road"] is not None


def test_feet_rule_converts_values_with_and_without_unit_word():
    process_unit = processor.ProcessUnit(configuration(expect_feet=True, phrases=()))

    assert process_unit.text_alteration("5'11 feet") == "179.3 cm"
    assert process_unit.text_alteration("Height 5'11 and 5’11.") == "Height 179.3 cm and 179.3 cm."
    assert process_unit.units_list["feet"].is_feet is True


def test_non_feet_rule_does_not_parse_feet_formatted_digits():
    process_unit = processor.ProcessUnit(
        configuration(
            units=(
                {
                    "phrase_from": "miles",
                    "phrase_to": "kilometers",
                    "conversion": 1.6,
                    "can_be_word": True,
                },
            ),
            phrases=(),
        )
    )

    assert process_unit.text_alteration("5'11 miles") == "5'11 miles"
    assert process_unit.text_alteration("From 5 to 10 miles") == "From 5 to 16 kilometers"
    assert process_unit.text_alteration("Some random words before 10 miles") == (
        "Some random words before 16 kilometers"
    )
    assert process_unit.text_alteration("This is a random ordinary word miles") == (
        "This is a random ordinary word miles"
    )
    assert process_unit.units_list["miles"].is_feet is False


def test_configured_feet_rule_converts_standalone_values_using_its_own_settings():
    process_unit = processor.ProcessUnit(
        configuration(
            expect_feet=True,
            units=(
                {
                    "phrase_from": "Feet",
                    "phrase_to": "meters",
                    "conversion": 0.3048,
                    "can_be_word": True,
                },
            ),
            phrases=(),
        )
    )

    assert process_unit.text_alteration("6'0 Feet and 6'0") == "1.8 Meters and 1.8 meters"
    assert set(process_unit.units_list) == {"Feet"}


def test_docx_table_paragraph_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "table.docx")
    document = Document()
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].add_run("An old road.")
    document.save(source)

    process_files(configuration(), origin, output)

    processed = Document(output / source.name)
    assert "new road" in processed.tables[0].cell(0, 0).text


def test_clean_empty_removes_whole_markup_block(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "clean.html")
    source.write_text("<html><body><p><strong>---</strong></p><p>keep</p></body></html>", encoding="utf-8")

    process_files(configuration(clean_empty=True, phrases=()), origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == "<html><body><p>keep</p></body></html>"


def test_clean_empty_removes_block_that_becomes_empty_after_replacement(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "clean-after-replacement.html")
    source.write_text("<html><body><p>remove 123</p><p>keep</p></body></html>", encoding="utf-8")
    remove_configuration = configuration(
        clean_empty=True,
        phrases=(
            {
                "phrase_from": r"remove \d+",
                "phrase_to": "",
                "direct": True,
                "mutations": False,
                "regex": True,
            },
        ),
    )

    process_files(remove_configuration, origin, output)

    assert (output / source.name).read_text(encoding="utf-8") == "<html><body><p>keep</p></body></html>"


def test_block_level_checks_run_once_for_multiple_native_parts(monkeypatch, tmp_path: Path):
    origin, output, source = run_one(tmp_path, "parts.html")
    source.write_text("<html><body><p>one <strong>two</strong> three</p></body></html>", encoding="utf-8")
    calls: list[str] = []
    original = processor.ProcessUnit.images_locate

    def track_block(self, text, document):
        calls.append(text)
        return original(self, text, document)

    monkeypatch.setattr(processor.ProcessUnit, "images_locate", track_block)

    process_files(configuration(phrases=()), origin, output)

    assert calls == ["one two three"]


def test_word_counter_ignores_empty_space_segments(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "spaces.html")
    source.write_text("<html><body><p>one  two</p><p>   </p></body></html>", encoding="utf-8")
    process_unit = processor.ProcessUnit(configuration(phrases=()))
    document = create_document_adapter(source, output / source.name)
    alteration_calls: list[str] = []
    original_alteration = process_unit.text_alteration

    def track_alteration(text: str) -> str:
        alteration_calls.append(text)
        return original_alteration(text)

    process_unit.text_alteration = track_alteration

    process_unit.process_document(document)

    assert process_unit.word_counter == 2
    assert alteration_calls == ["one  two"]
    assert (output / source.name).read_text(encoding="utf-8") == (
        "<html><body><p>one  two</p><p> </p></body></html>"
    )


def test_image_separation_state_resets_for_each_document(monkeypatch, tmp_path: Path):
    origin = tmp_path / "origin"
    output = tmp_path / "output"
    origin.mkdir()
    for filename in ("first.html", "second.html"):
        (origin / filename).write_text(
            "<html><body><p><strong>trigger</strong> trigger</p></body></html>",
            encoding="utf-8",
        )
    image_configuration = configuration(
        phrases=(),
        images=(
            {
                "phrase": "trigger",
                "separation": 1,
                "explanation": "",
                "mutations": False,
                "image_paths": (tmp_path / "trigger.png",),
            },
        ),
    )
    monkeypatch.setattr(processor, "get_image", lambda *_args: "aW1hZ2U=")

    assert process_files(image_configuration, origin, output) == 2

    expected = (
        '<html><body><img src="data:image/jpeg;base64,aW1hZ2U=" '
        'style="display: block; margin-left: auto; margin-right: auto; max-width: 99%;"/>'
        "<p><strong>trigger</strong> trigger</p></body></html>"
    )
    for filename in ("first.html", "second.html"):
        assert (output / filename).read_text(encoding="utf-8") == expected


def test_image_trigger_pattern_is_precompiled_and_escapes_phrases(tmp_path: Path):
    process_unit = processor.ProcessUnit(
        configuration(
            phrases=(),
            images=(
                {
                    "phrase": "a+b",
                    "separation": 1,
                    "explanation": "",
                    "mutations": False,
                    "image_paths": (tmp_path / "trigger.png",),
                },
            ),
        )
    )

    pattern = process_unit.image_trigger_pattern
    assert pattern is not None
    assert pattern.findall("a+b ab aab") == [("a+b", "")]
    assert process_unit.image_trigger_pattern is pattern
