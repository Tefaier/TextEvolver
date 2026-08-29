from pathlib import Path

from bs4 import BeautifulSoup

from text_evolver.processing.browser import HTML_IMAGE_STYLE
from text_evolver.processing.documents.base import DocumentAdapter, DocumentPart
from text_evolver.processing.documents.markup import MarkupCursor


class HtmlDocumentAdapter(DocumentAdapter):
    def __init__(self, source: Path, target: Path) -> None:
        super().__init__(source, target)
        self._soup = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
        self._cursor = MarkupCursor(self._soup)

    def read_part(self) -> DocumentPart | None:
        part = self._cursor.read_part()
        if part is None:
            self._clear_last_part()
        else:
            self._mark_part_read()
        return part

    def overwrite_last_part(self, text: str) -> None:
        self._require_last_part()
        self._cursor.overwrite_last_part(text)

    def overwrite_last_block_parts(self, texts: list[str]) -> None:
        self._require_last_part()
        self._cursor.overwrite_last_block_parts(texts)

    def remove_last_block(self) -> None:
        self._require_last_part()
        self._cursor.remove_last_block()
        self._clear_last_part()

    def insert_image_before_last_block(self, image_data: str) -> None:
        self._require_last_part()
        image_tag = self._soup.new_tag(
            "img",
            src=f"data:image/jpeg;base64,{image_data}",
            style=HTML_IMAGE_STYLE,
        )
        self._cursor.last_block.insert_before(image_tag)

    def _write(self, target: Path) -> None:
        target.write_bytes(self._soup.encode())
