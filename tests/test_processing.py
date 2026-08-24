from pathlib import Path

from docx import Document
from ebooklib import epub

from text_evolver.processing.processor import process_files
from text_evolver.processing.settings_loader import ProcessingConfiguration


def configuration() -> ProcessingConfiguration:
    return ProcessingConfiguration(
        use_comma_separator=False,
        expect_feet=False,
        clean_empty=False,
        convert_to_utf=False,
        fandoms=(),
        units=(),
        phrases=({"phrase_from": "old", "phrase_to": "new", "direct": True, "mutations": False},),
        images=(),
    )


def run_one(tmp_path: Path, filename: str) -> Path:
    origin = tmp_path / "origin"
    output = tmp_path / "output"
    origin.mkdir()
    return origin, output, origin / filename


def test_html_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.html")
    source.write_text("<html><body><p>An old road.</p></body></html>", encoding="utf-8")
    assert process_files(configuration(), origin, output) == 1
    assert "new road" in (output / source.name).read_text(encoding="utf-8")
    assert not source.exists()


def test_fb2_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.fb2")
    source.write_text(
        "<FictionBook><body><section><p>An old road.</p></section></body></FictionBook>",
        encoding="utf-8",
    )
    process_files(configuration(), origin, output)
    assert "new road" in (output / source.name).read_text(encoding="utf-8")


def test_docx_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.docx")
    document = Document()
    document.add_paragraph("An old road.")
    document.save(source)
    process_files(configuration(), origin, output)
    assert "new road" in Document(output / source.name).paragraphs[0].text


def test_epub_processing(tmp_path: Path):
    origin, output, source = run_one(tmp_path, "book.epub")
    book = epub.EpubBook()
    book.set_identifier("fixture")
    book.set_title("Fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>An old road.</p></body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(source, book)
    process_files(configuration(), origin, output)
    processed = epub.read_epub(output / source.name)
    assert any(b"new road" in item.get_content() for item in processed.get_items())
