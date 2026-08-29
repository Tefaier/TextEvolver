from pathlib import Path

from bs4 import BeautifulSoup

from text_evolver.processing.documents.base import DocumentAdapter, DocumentPart
from text_evolver.processing.documents.markup import MarkupCursor, insert_xml_image


class Fb2DocumentAdapter(DocumentAdapter):
    def __init__(self, source: Path, target: Path) -> None:
        super().__init__(source, target)
        self._soup = BeautifulSoup(source.read_text(encoding="utf-8"), "xml")
        self._cursor = MarkupCursor(self._soup)
        self._inserted_images: list[bytes] = []

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

    def remove_last_block(self) -> None:
        self._require_last_part()
        self._cursor.remove_last_block()
        self._clear_last_part()

    def insert_image_before_last_block(self, image_data: str) -> None:
        self._require_last_part()
        insert_xml_image(self._cursor, self._inserted_images, image_data)

    def _write(self, target: Path) -> None:
        target.write_bytes(self._soup.encode())
