"""Document model shared by importers, text preparation, storage and the UI.

Design rules
------------
* ``Block.text`` is what the user sees. The importer fills it with the
  extracted text; if the user corrects it, the extracted text is kept in
  ``Block.original_text`` (the original is never lost).
* Nothing is deleted at import time. Running headers, page numbers,
  footnotes, reference lists, URL's etc. are *classified* (block ``kind``,
  ``section`` and character ``spans``) so the reading-text builder can skip
  them according to the user's choices, and the UI can show what is
  skipped.
* All offsets are Python string indices into ``Block.text`` (end exclusive).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

BlockKind = Literal[
    "heading",  # section/chapter heading, see Block.level (1 = top level)
    "paragraph",  # ordinary body text
    "list_item",  # bullet/numbered list entry
    "quote",  # block quotation
    "caption",  # figure/table caption («Figur 1: …», «Table 2. …»)
    "footnote",  # footnote/endnote text
    "reference",  # one entry in a reference list/bibliography
    "table",  # table, structured data in Block.table
    "header",  # running page header (repeated on many pages)
    "footer",  # running page footer
    "page_number",  # page number line
    "figure_text",  # text that sits inside a figure/diagram
]

SpanKind = Literal[
    "url",  # http(s)://…, www.…
    "doi",  # doi:10.…, https://doi.org/10.…
    "email",
    "footnote_ref",  # superscript/inline footnote marker in body text
]

BLOCK_KINDS: tuple[str, ...] = (
    "heading",
    "paragraph",
    "list_item",
    "quote",
    "caption",
    "footnote",
    "reference",
    "table",
    "header",
    "footer",
    "page_number",
    "figure_text",
)


@dataclass
class Span:
    """A classified character range inside ``Block.text``."""

    start: int
    end: int
    kind: str  # SpanKind

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "kind": self.kind}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Span":
        return Span(int(d["start"]), int(d["end"]), str(d["kind"]))


@dataclass
class TableData:
    """Structured table content.

    ``header`` holds the column headings when they could be identified;
    ``rows`` holds the body rows (each a list of cell strings, same length
    as ``header`` when a header exists). ``reliable`` is False when the
    importer is unsure about the structure (merged cells, ragged rows,
    no detectable header …) – the UI then warns the user before the table
    is read aloud.
    """

    rows: list[list[str]]
    header: Optional[list[str]] = None
    reliable: bool = True
    note: Optional[str] = None  # Norwegian explanation when not reliable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TableData":
        return TableData(
            rows=[[str(c) for c in row] for row in d.get("rows", [])],
            header=[str(c) for c in d["header"]] if d.get("header") is not None else None,
            reliable=bool(d.get("reliable", True)),
            note=d.get("note"),
        )


@dataclass
class Block:
    id: str  # stable within a document, e.g. "b0001"
    kind: str  # BlockKind
    text: str
    level: int = 0  # heading level 1–6 (0 for non-headings)
    page: Optional[int] = None  # 1-based page number where the block starts
    spans: list[Span] = field(default_factory=list)
    table: Optional[TableData] = None
    section: Optional[str] = None  # "references" for the reference list heading + entries
    ocr: bool = False  # text produced by OCR (may contain recognition errors)
    original_text: Optional[str] = None  # extracted text, kept when the user edits ``text``
    original_spans: Optional[list[Span]] = None  # importer's spans for ``original_text``

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind, "text": self.text}
        if self.level:
            d["level"] = self.level
        if self.page is not None:
            d["page"] = self.page
        if self.spans:
            d["spans"] = [s.to_dict() for s in self.spans]
        if self.table is not None:
            d["table"] = self.table.to_dict()
        if self.section:
            d["section"] = self.section
        if self.ocr:
            d["ocr"] = True
        if self.original_text is not None:
            d["original_text"] = self.original_text
        if self.original_spans is not None:
            d["original_spans"] = [s.to_dict() for s in self.original_spans]
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Block":
        return Block(
            id=str(d["id"]),
            kind=str(d["kind"]),
            text=str(d.get("text", "")),
            level=int(d.get("level", 0)),
            page=d.get("page"),
            spans=[Span.from_dict(s) for s in d.get("spans", [])],
            table=TableData.from_dict(d["table"]) if d.get("table") else None,
            section=d.get("section"),
            ocr=bool(d.get("ocr", False)),
            original_text=d.get("original_text"),
            original_spans=[Span.from_dict(s) for s in d["original_spans"]] if d.get("original_spans") is not None else None,
        )


@dataclass
class DocWarning:
    """A Norwegian, user-facing note about the import (shown in the workspace)."""

    code: str  # e.g. "ocr_used", "ocr_failed", "table_uncertain", "little_text"
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass
class Document:
    title: str
    source_name: str  # original file name (no directories)
    source_type: str  # "pdf" | "docx" | "epub" | "txt"
    size_bytes: int
    blocks: list[Block]
    page_count: Optional[int] = None
    language: str = "und"  # "nb", "nn", "en", … or "und" (unknown)
    language_confidence: float = 0.0  # 0..1
    warnings: list[DocWarning] = field(default_factory=list)
    ocr_used: bool = False
    metadata: dict[str, str] = field(default_factory=dict)  # author, etc. (strings only)

    # -- helpers ---------------------------------------------------------
    def block_by_id(self, block_id: str) -> Optional[Block]:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    def chapters(self) -> list[dict[str, Any]]:
        """Chapter list derived from headings.

        Uses the top-most heading level that occurs at least twice (a single
        level-1 heading is usually the document title). Returns
        ``[{"title": str, "block_id": str, "index": int}]``.
        """
        levels: dict[int, int] = {}
        for b in self.blocks:
            if b.kind == "heading" and b.level > 0 and b.section != "references":
                levels[b.level] = levels.get(b.level, 0) + 1
        if not levels:
            return []
        candidates = sorted(levels)
        chapter_level = next((lvl for lvl in candidates if levels[lvl] >= 2), candidates[0])
        out = []
        for idx, b in enumerate(self.blocks):
            if b.kind == "heading" and b.level == chapter_level and b.section != "references":
                title = " ".join(b.text.split())
                if title:
                    out.append({"title": title[:200], "block_id": b.id, "index": idx})
        return out

    def plain_text(self, kinds: Optional[set[str]] = None) -> str:
        """All block texts joined by blank lines (optionally only some kinds)."""
        parts = []
        for b in self.blocks:
            if kinds is not None and b.kind not in kinds:
                continue
            if b.kind == "table" and b.table is not None:
                rows = ([b.table.header] if b.table.header else []) + b.table.rows
                parts.append("\n".join(" | ".join(r) for r in rows))
            else:
                parts.append(b.text)
        return "\n\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "medleser-document",
            "version": 1,
            "title": self.title,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
            "language": self.language,
            "language_confidence": self.language_confidence,
            "warnings": [w.to_dict() for w in self.warnings],
            "ocr_used": self.ocr_used,
            "metadata": dict(self.metadata),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Document":
        return Document(
            title=str(d.get("title", "")),
            source_name=str(d.get("source_name", "")),
            source_type=str(d.get("source_type", "")),
            size_bytes=int(d.get("size_bytes", 0)),
            blocks=[Block.from_dict(b) for b in d.get("blocks", [])],
            page_count=d.get("page_count"),
            language=str(d.get("language", "und")),
            language_confidence=float(d.get("language_confidence", 0.0)),
            warnings=[DocWarning(str(w["code"]), str(w["message"])) for w in d.get("warnings", [])],
            ocr_used=bool(d.get("ocr_used", False)),
            metadata={str(k): str(v) for k, v in d.get("metadata", {}).items()},
        )


def make_block_id(index: int) -> str:
    return f"b{index:05d}"
