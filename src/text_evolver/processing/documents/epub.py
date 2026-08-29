from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from text_evolver.processing.documents.base import DocumentAdapter, DocumentPart
from text_evolver.processing.documents.markup import MarkupCursor, insert_xml_image


class EpubDocumentAdapter(DocumentAdapter):
    def __init__(self, source: Path, target: Path) -> None:
        super().__init__(source, target)
        self._book = epub.read_epub(source)
        self._documents = [
            (item, soup, MarkupCursor(soup))
            for item in self._book.get_items()
            if item.get_type() == ebooklib.ITEM_DOCUMENT
            for soup in [BeautifulSoup(item.get_content(), "xml")]
        ]
        self._document_index = 0
        self._last_cursor: MarkupCursor | None = None
        self._inserted_images: dict[int, list[bytes]] = {}

    def read_part(self) -> DocumentPart | None:
        while self._document_index < len(self._documents):
            _, _, cursor = self._documents[self._document_index]
            part = cursor.read_part()
            if part is not None:
                self._last_cursor = cursor
                self._mark_part_read()
                return part
            self._document_index += 1
        self._last_cursor = None
        self._clear_last_part()
        return None

    def overwrite_last_part(self, text: str) -> None:
        self._require_last_part()
        self._require_cursor().overwrite_last_part(text)

    def overwrite_last_block_parts(self, texts: list[str]) -> None:
        self._require_last_part()
        self._require_cursor().overwrite_last_block_parts(texts)

    def remove_last_block(self) -> None:
        self._require_last_part()
        self._require_cursor().remove_last_block()
        self._last_cursor = None
        self._clear_last_part()

    def insert_image_before_last_block(self, image_data: str) -> None:
        self._require_last_part()
        cursor = self._require_cursor()
        insert_xml_image(cursor, self._inserted_images.setdefault(self._document_index, []), image_data)

    def _require_cursor(self) -> MarkupCursor:
        if self._last_cursor is None:
            raise RuntimeError("Read a document part before modifying its block")
        return self._last_cursor

    def _write(self, target: Path) -> None:
        for item, soup, _ in self._documents:
            item.set_content(soup.encode())
        epub.write_epub(target, self._book)
