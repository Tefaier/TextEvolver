from pathlib import Path

from text_evolver.processing.documents.base import DocumentAdapter, DocumentPart
from text_evolver.processing.documents.docx import DocxDocumentAdapter
from text_evolver.processing.documents.epub import EpubDocumentAdapter
from text_evolver.processing.documents.fb2 import Fb2DocumentAdapter
from text_evolver.processing.documents.html import HtmlDocumentAdapter

DOCUMENT_ADAPTERS: dict[str, type[DocumentAdapter]] = {
    ".docx": DocxDocumentAdapter,
    ".epub": EpubDocumentAdapter,
    ".fb2": Fb2DocumentAdapter,
    ".html": HtmlDocumentAdapter,
}


def create_document_adapter(source: str | Path, target: str | Path) -> DocumentAdapter:
    source_path = Path(source)
    target_path = Path(target)
    try:
        adapter = DOCUMENT_ADAPTERS[source_path.suffix.casefold()]
    except KeyError as exc:
        raise ValueError(f"Unsupported document format: {source_path.suffix or source_path.name}") from exc
    return adapter(source_path, target_path)


__all__ = ["DocumentAdapter", "DocumentPart", "create_document_adapter"]
