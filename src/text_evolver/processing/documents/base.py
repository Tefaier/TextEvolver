from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DocumentPart:
    text: str
    block_text: str
    starts_block: bool


class DocumentAdapter(ABC):
    """Stateful interface between text transformations and a native document format."""

    def __init__(self, source: Path, target: Path) -> None:
        self.source = source
        self.target = target
        self._has_last_part = False

    def _mark_part_read(self) -> None:
        self._has_last_part = True

    def _clear_last_part(self) -> None:
        self._has_last_part = False

    def _require_last_part(self) -> None:
        if not self._has_last_part:
            raise RuntimeError("Read a document part before modifying its block")

    @abstractmethod
    def read_part(self) -> DocumentPart | None:
        """Read the next native text part and retain its native write-back state."""

    @abstractmethod
    def overwrite_last_part(self, text: str) -> None:
        """Replace the native text part returned by the last successful read."""

    @abstractmethod
    def remove_last_block(self) -> None:
        """Remove the block containing the last successfully read part."""

    @abstractmethod
    def insert_image_before_last_block(self, image_data: str) -> None:
        """Insert a Base64 JPEG before the block containing the last read part."""

    @abstractmethod
    def _write(self, target: Path) -> None:
        """Serialize the native document to target."""

    def save(self) -> Path:
        """Atomically publish output, then consume the source document."""
        self.target.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = self.target.with_name(f".{self.target.name}.tmp")
        temporary_target.unlink(missing_ok=True)
        try:
            self._write(temporary_target)
            temporary_target.replace(self.target)
            self.source.unlink()
        except Exception:
            temporary_target.unlink(missing_ok=True)
            raise
        return self.target
