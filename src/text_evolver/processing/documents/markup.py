import hashlib
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

from text_evolver.processing.documents.base import DocumentPart

IMAGE_HASH_CHUNK_CHARACTERS = 1024 * 1024


@dataclass(slots=True)
class _MarkupBlock:
    tag: Tag
    text: str
    parts: list[NavigableString | None]


class MarkupCursor:
    """Stateful traversal and write-back for p/span blocks in one parsed markup document."""

    def __init__(self, soup: BeautifulSoup) -> None:
        self.soup = soup
        self._blocks = [
            _MarkupBlock(
                tag=tag,
                text=tag.get_text(),
                parts=list(tag.find_all(string=True)) or [None],
            )
            for tag in soup.find_all(["p", "span"])
            if tag.find_parent(["p", "span"]) is None
        ]
        self._block_index = 0
        self._part_index = 0
        self._last_block: _MarkupBlock | None = None
        self._last_part: NavigableString | None = None

    @property
    def has_last_part(self) -> bool:
        return self._last_block is not None

    @property
    def last_block(self) -> Tag:
        if self._last_block is None:
            raise RuntimeError("Read a document part before modifying its block")
        return self._last_block.tag

    def read_part(self) -> DocumentPart | None:
        while self._block_index < len(self._blocks):
            block = self._blocks[self._block_index]
            if self._part_index >= len(block.parts):
                self._block_index += 1
                self._part_index = 0
                continue
            part_index = self._part_index
            part = block.parts[part_index]
            self._part_index += 1
            if part is not None and part.parent is None:
                continue
            self._last_block = block
            self._last_part = part
            return DocumentPart(
                text=str(part) if part is not None else "",
                block_text=block.text,
                starts_block=part_index == 0,
                ends_block=part_index == len(block.parts) - 1,
            )
        self.clear_last_part()
        return None

    def overwrite_last_part(self, text: str) -> None:
        block = self.last_block
        if self._last_part is None:
            if text:
                replacement = NavigableString(text)
                block.append(replacement)
                self._last_part = replacement
            return
        replacement = NavigableString(text)
        self._last_part.replace_with(replacement)
        self._last_part = replacement

    def overwrite_last_block_parts(self, texts: list[str]) -> None:
        block = self.last_block
        if self._last_block is None or len(texts) != len(self._last_block.parts):
            raise ValueError("Replacement part count does not match the current document block")
        replacements: list[NavigableString | None] = []
        for part, text in zip(self._last_block.parts, texts, strict=True):
            if part is None:
                replacement = NavigableString(text) if text else None
                if replacement is not None:
                    block.append(replacement)
            else:
                replacement = NavigableString(text)
                part.replace_with(replacement)
            replacements.append(replacement)
        self._last_block.parts = replacements
        self._last_part = replacements[-1]

    def remove_last_block(self) -> None:
        block = self.last_block
        block.decompose()
        self._part_index = len(self._last_block.parts) if self._last_block is not None else 0
        self.clear_last_part()

    def clear_last_part(self) -> None:
        self._last_block = None
        self._last_part = None


def _image_hash(image_data: str) -> bytes:
    digest = hashlib.sha256()
    for start in range(0, len(image_data), IMAGE_HASH_CHUNK_CHARACTERS):
        digest.update(image_data[start : start + IMAGE_HASH_CHUNK_CHARACTERS].encode("ascii"))
    return digest.digest()


def insert_xml_image(cursor: MarkupCursor, inserted_images: list[bytes], image_data: str) -> None:
    block = cursor.last_block
    image_hash = _image_hash(image_data)
    try:
        image_number = inserted_images.index(image_hash)
    except ValueError:
        image_number = len(inserted_images)
        inserted_images.append(image_hash)
        binary_tag = cursor.soup.new_tag("binary", id=f"{image_number}.jpg", **{"content-type": "image/jpeg"})
        binary_tag.string = image_data
        block.insert_before(binary_tag)
    image_tag = cursor.soup.new_tag("image", **{"href": f"#{image_number}.jpg"})
    block.insert_before(image_tag)
