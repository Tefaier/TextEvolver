from pathlib import Path

from bs4 import BeautifulSoup
from docx import Document
from ebooklib import epub

from text_evolver.processing import processor
from text_evolver.processing.process_config_builder import ProcessingConfiguration
from text_evolver.processing.processor import process_files


def configuration(
    *,
    clean_empty: bool = False,
    phrases: tuple[dict[str, object], ...] | None = None,
    images: tuple[dict[str, object], ...] = (),
) -> ProcessingConfiguration:
    return ProcessingConfiguration(
        use_comma_separator=False,
        expect_feet=False,
        clean_empty=clean_empty,
        convert_to_utf=False,
        fandoms=(),
        units=(),
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

    soup = BeautifulSoup((output / source.name).read_bytes(), "html.parser")
    assert "new road" in soup.get_text()
    assert soup.strong is not None and soup.strong.get_text() == "new"
    assert not source.exists()


def test_fb2_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.fb2")
    source.write_text(
        "<FictionBook><body><section><p>An <emphasis>old</emphasis> road.</p></section></body></FictionBook>",
        encoding="utf-8",
    )

    process_files(configuration(), origin, output)

    soup = BeautifulSoup((output / source.name).read_bytes(), "xml")
    assert "new road" in soup.get_text()
    assert soup.emphasis is not None and soup.emphasis.get_text() == "new"


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
    contents = [item.get_content() for item in processed.get_items()]
    rendered_text = " ".join(
        " ".join(BeautifulSoup(content, "xml").get_text(" ").split()) for content in contents
    )
    assert "new road" in rendered_text
    assert any(b"<strong>new</strong>" in content for content in contents)


def test_phrase_replacement_does_not_cross_markup_parts(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "split.html")
    source.write_text("<html><body><p><strong>old</strong> road</p></body></html>", encoding="utf-8")
    split_phrase_configuration = configuration(
        phrases=(
            {
                "phrase_from": "old road",
                "phrase_to": "new path",
                "direct": False,
                "mutations": False,
            },
        )
    )

    process_files(split_phrase_configuration, origin, output)

    result = BeautifulSoup((output / source.name).read_bytes(), "html.parser").get_text()
    assert "old road" in result
    assert "new path" not in result


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

    soup = BeautifulSoup((output / source.name).read_bytes(), "html.parser")
    assert [paragraph.get_text() for paragraph in soup.find_all("p")] == ["keep"]


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
                "images": "aW1hZ2U=",
            },
        ),
    )
    monkeypatch.setattr(processor, "get_image", lambda *_args: "aW1hZ2U=")

    assert process_files(image_configuration, origin, output) == 2

    for filename in ("first.html", "second.html"):
        soup = BeautifulSoup((output / filename).read_bytes(), "html.parser")
        assert len(soup.find_all("img")) == 1
