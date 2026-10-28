"""Document import: PDF, DOCX, EPUB and TXT to the shared document model."""

from typing import Any

from .model import Block, DocWarning, Document, Span, TableData

__all__ = ["Block", "DocWarning", "Document", "Span", "TableData", "import_document"]


def __getattr__(name: str) -> Any:
    # Lazy: importing the package must not load PyMuPDF/python-docx.
    if name == "import_document":
        from .importer import import_document

        return import_document
    raise AttributeError(name)
