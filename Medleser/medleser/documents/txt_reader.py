"""Plain-text reader.

Decoding: UTF-32/UTF-16/UTF-8 byte-order marks first, then strict UTF-8,
then Windows-1252 (the usual encoding of older Norwegian Windows files).
Files with NUL bytes (outside UTF-16/32) or many control characters are
not text and are refused.

Structure: paragraphs are separated by blank lines; single line breaks
inside a paragraph become spaces (with end-of-line hyphenation joined).
Markdown-style ``# Heading`` lines and «setext» headings (a line underlined
with ``===``/``---``) become headings; lines starting with a bullet or an
enumeration («- », «• », «1. ») become list items.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from ..errors import DocumentImportError
from .cleanup import (
    bullet_prefix_length,
    hyphenated_vocabulary,
    join_lines,
    normalize_text,
    starts_with_enumeration,
)
from .common import MSG_EMPTY_DOCUMENT, Progress, ReadResult, check_cancel
from .model import Block

MSG_NOT_TEXT = (
    "Filen ser ikke ut til å være en vanlig tekstfil. Medleser kan lese PDF, Word (.docx), "
    "EPUB og tekstfiler (.txt)."
)

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE_RE = re.compile(r"^([-=*_])(?:\s*\1){2,}\s*$")


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode bytes of a text file. Returns ``(text, encoding)``.

    Raises DocumentImportError when the data does not look like text.
    """
    boms = (
        (b"\x00\x00\xfe\xff", "utf-32-be"),
        (b"\xff\xfe\x00\x00", "utf-32-le"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    )
    for bom, enc in boms:
        if data.startswith(bom):
            body = data[len(bom):] if enc != "utf-8-sig" else data
            try:
                return body.decode(enc), enc.replace("-sig", "")
            except UnicodeDecodeError as exc:
                raise DocumentImportError(
                    MSG_NOT_TEXT, code="unsupported_type", details=f"bad {enc}: {exc}"
                ) from exc
    if b"\x00" in data:
        raise DocumentImportError(MSG_NOT_TEXT, code="unsupported_type", details="NUL bytes in text file")
    try:
        text, enc = data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
        enc = "cp1252"
        if text.count("�") > max(3, len(text) // 200):
            raise DocumentImportError(MSG_NOT_TEXT, code="unsupported_type", details="undecodable bytes")
    control = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r\f\v\x1a")
    if control > max(3, len(text) // 200):
        raise DocumentImportError(MSG_NOT_TEXT, code="unsupported_type", details="control characters")
    return text, enc


def read_txt(
    path: Path,
    *,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> ReadResult:
    report = progress or Progress(None)
    report(0.0, "Leser tekstfilen …")
    text, encoding = decode_text(path.read_bytes())
    lines = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n")
    vocab = hyphenated_vocabulary(lines)

    blocks: list[Block] = []
    para: list[str] = []
    para_kind = "paragraph"

    def flush() -> None:
        nonlocal para, para_kind
        if para:
            joined, _ = join_lines(para, hyphenated_words=vocab)
            if para_kind == "list_item":
                joined = joined[bullet_prefix_length(joined):]
            t = normalize_text(joined)
            if t:
                blocks.append(Block(id="", kind=para_kind, text=t))
        para = []
        para_kind = "paragraph"

    total = max(1, len(lines))
    for i, raw in enumerate(lines):
        if i % 5000 == 0:
            check_cancel(cancel)
            report(i / total, "Leser tekstfilen …")
        stripped = raw.strip()
        if not stripped:
            flush()
            continue
        m = _MD_HEADING_RE.match(stripped)
        if m and m.group(2):
            flush()
            blocks.append(Block(id="", kind="heading", text=normalize_text(m.group(2)), level=len(m.group(1))))
            continue
        if _RULE_RE.match(stripped):
            if len(para) == 1 and para_kind == "paragraph" and stripped[0] in "=-":
                heading = normalize_text(para[0])
                para = []
                blocks.append(Block(id="", kind="heading", text=heading, level=1 if stripped[0] == "=" else 2))
            else:
                flush()
            continue
        if bullet_prefix_length(stripped) or starts_with_enumeration(stripped):
            flush()
            para_kind = "list_item"
        para.append(stripped)
    flush()

    if not blocks:
        raise DocumentImportError(MSG_EMPTY_DOCUMENT, code="no_text", details="empty text file")
    report(1.0, "Tekstfilen er lest.")
    return ReadResult(blocks=blocks, metadata={"encoding": encoding})
