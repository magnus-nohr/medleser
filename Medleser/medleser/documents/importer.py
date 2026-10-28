"""Document import: validate a file by its content and turn it into a Document.

``import_document`` is the only entry point the rest of the app needs. It

1. checks size limits (``config.MAX_UPLOAD_BYTES``) and refuses empty files;
2. identifies the type from the *content*, not the name: ``%PDF-`` header;
   zip containers with ``[Content_Types].xml`` + ``word/document.xml`` (DOCX,
   macro-enabled documents and templates are refused) or ``mimetype`` =
   ``application/epub+zip`` + ``META-INF/container.xml`` (EPUB); plain text
   (decodable, no NUL bytes). Old Word files, executables, RTF, images,
   spreadsheets etc. get a friendly Norwegian explanation. Zip containers
   pass the zip-bomb guards in :mod:`ziputil` before anything is read;
3. runs the format reader with progress and cancellation;
4. classifies reference sections, assigns block ids and url/doi/email spans,
   detects the language and chooses a title.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

from .. import config
from ..errors import DocumentImportError
from .common import MSG_NO_TEXT, Progress, ReadResult, WARN_OCR_USED, check_cancel
from .language import detect_language
from .model import DocWarning, Document
from .postprocess import choose_title, finalize_blocks, language_sample, mark_reference_sections
from .xmlsafe import UnsafeXMLError, parse_xml
from .ziputil import open_zip, read_member

MSG_NOT_FOUND = "Fant ikke filen. Den kan ha blitt flyttet eller slettet."
MSG_EMPTY = "Filen er tom."
MSG_UNSUPPORTED = (
    "Filtypen støttes ikke. Medleser kan lese PDF, Word (.docx), EPUB og tekstfiler (.txt)."
)
MSG_LEGACY_OFFICE = (
    "Dette ser ut til å være et eldre Word-dokument (.doc) eller et passordbeskyttet "
    "Office-dokument. Åpne det i Word, lagre det som .docx uten passord, og prøv igjen."
)
MSG_MACRO = (
    "Word-dokumenter med makroer (.docm) åpnes ikke av sikkerhetshensyn. Lagre dokumentet som "
    "vanlig Word-dokument (.docx) og prøv igjen."
)
MSG_TEMPLATE = "Word-maler (.dotx) støttes ikke. Lagre malen som vanlig Word-dokument (.docx) og prøv igjen."
MSG_PROGRAM = (
    "Filen er et program og ikke et dokument. Medleser åpner bare PDF, Word (.docx), EPUB og "
    "tekstfiler (.txt)."
)
MSG_RTF = "RTF-filer støttes ikke. Åpne filen i Word og lagre den som .docx eller PDF, og prøv igjen."
MSG_IMAGE = (
    "Bildefiler støttes ikke direkte. Lagre bildet som PDF og bruk tekstgjenkjenning (OCR), "
    "eller bruk en PDF-versjon av dokumentet."
)
MSG_SPREADSHEET = "Regneark og presentasjoner støttes ikke. Lagre innholdet som PDF eller Word-dokument (.docx)."
MSG_ODF = "OpenDocument-filer (.odt) støttes ikke. Lagre dokumentet som .docx eller PDF og prøv igjen."
MSG_MISMATCH = "Filen har endelsen {ext}, men innholdet er ikke en gyldig {name}. Filen kan være skadet."

TEXT_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ""}
WML_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_FORMAT_NAMES = {".pdf": "PDF-fil", ".docx": "Word-fil (.docx)", ".epub": "EPUB-fil"}
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")
_ARCHIVE_MAGIC = (b"Rar!", b"7z\xbc\xaf", b"\x1f\x8b", b"BZh", b"\xfd7zXZ", b"RIFF", b"ID3", b"\x00\x00\x00")
_PROGRAM_MAGIC = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"#!")

ProgressFn = Callable[[float, str], None]


def _source_name(original_name: str | None, path: Path) -> str:
    name = PureWindowsPath(str(original_name)).name if original_name else path.name
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return (name or path.name)[:255]


def _looks_like_text(head: bytes) -> bool:
    if head.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in head:
        return False
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start >= len(head) - 4:  # a multi-byte character cut at the end of the sample
            text = head[: exc.start].decode("utf-8", errors="replace")
        else:
            text = head.decode("cp1252", errors="replace")
    control = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r\f\v\x1a")
    return control <= max(2, len(text) // 200) and text.count("�") <= max(2, len(text) // 200)


def _zip_kind(path: Path, ext: str) -> str:
    zf = open_zip(path)  # runs the zip-bomb guards
    with zf:
        names = set(zf.namelist())
        lower = {n.lower(): n for n in names}
        if "mimetype" in names:
            mt = read_member(zf, "mimetype", max_bytes=256).decode("ascii", errors="replace").strip()
            if mt == "application/epub+zip":
                if "meta-inf/container.xml" not in lower:
                    raise DocumentImportError(
                        MSG_MISMATCH.format(ext=ext or ".epub", name="EPUB-fil"),
                        code="damaged",
                        details="EPUB without META-INF/container.xml",
                    )
                return "epub"
            if mt.startswith("application/vnd.oasis.opendocument"):
                raise DocumentImportError(MSG_ODF, code="unsupported_type", details=f"mimetype {mt}")
        if "[Content_Types].xml" in names:
            content_types: dict[str, str] = {}
            try:
                root = parse_xml(read_member(zf, "[Content_Types].xml", max_bytes=4 << 20))
                for el in root:
                    if isinstance(el.tag, str) and el.tag.endswith("Override"):
                        content_types[(el.get("PartName") or "").lower()] = el.get("ContentType") or ""
            except UnsafeXMLError as exc:
                raise DocumentImportError(
                    MSG_MISMATCH.format(ext=ext or ".docx", name="Word-fil (.docx)"),
                    code="damaged",
                    details=f"[Content_Types].xml: {exc}",
                ) from exc
            main_types = [ct for ct in content_types.values() if ct.endswith(".main+xml")]
            if "word/document.xml" in names or any("wordprocessingml" in ct or "ms-word" in ct for ct in main_types):
                main = content_types.get("/word/document.xml", "")
                macro = (
                    "word/vbaproject.bin" in lower
                    or "macroenabled" in main.lower()
                    or any("macroenabled" in ct.lower() for ct in main_types)
                )
                if macro:
                    raise DocumentImportError(MSG_MACRO, code="docm_macro", details=f"main content type {main!r}")
                if "template" in main.lower():
                    raise DocumentImportError(MSG_TEMPLATE, code="unsupported_type", details=main)
                if "word/document.xml" not in names or (main and main != WML_MAIN):
                    raise DocumentImportError(
                        MSG_MISMATCH.format(ext=ext or ".docx", name="Word-fil (.docx)"),
                        code="damaged",
                        details=f"unexpected Word package: {main!r}",
                    )
                return "docx"
            if "xl/workbook.xml" in lower or "ppt/presentation.xml" in lower:
                raise DocumentImportError(MSG_SPREADSHEET, code="unsupported_type", details="xlsx/pptx")
        raise DocumentImportError(MSG_UNSUPPORTED, code="unsupported_type", details="zip archive without a known document")


def detect_type(path: Path, source_name: str) -> str:
    """Return ``"pdf" | "docx" | "epub" | "txt"`` by content or raise DocumentImportError."""
    ext = Path(source_name).suffix.lower()
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError as exc:
        raise DocumentImportError(MSG_NOT_FOUND, code="not_found", details=str(exc)) from exc
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return _zip_kind(path, ext)
    if head.startswith(_OLE2):
        raise DocumentImportError(MSG_LEGACY_OFFICE, code="legacy_office", details="OLE2 compound file")
    if head.startswith(_PROGRAM_MAGIC) and not _looks_like_text(head):
        raise DocumentImportError(MSG_PROGRAM, code="unsupported_type", details="executable signature")
    if head.lstrip().startswith(b"{\\rtf"):
        raise DocumentImportError(MSG_RTF, code="unsupported_type", details="RTF")
    if head.startswith(_IMAGE_MAGIC) and not _looks_like_text(head):
        raise DocumentImportError(MSG_IMAGE, code="unsupported_type", details="image signature")
    if head.startswith(_ARCHIVE_MAGIC):
        raise DocumentImportError(MSG_UNSUPPORTED, code="unsupported_type", details="archive signature")
    if _looks_like_text(head):
        if ext in TEXT_EXTENSIONS:
            return "txt"
        if ext in _FORMAT_NAMES:
            raise DocumentImportError(
                MSG_MISMATCH.format(ext=ext, name=_FORMAT_NAMES[ext]), code="damaged", details="text content"
            )
        raise DocumentImportError(MSG_UNSUPPORTED, code="unsupported_type", details=f"text with extension {ext}")
    if ext in _FORMAT_NAMES:
        raise DocumentImportError(
            MSG_MISMATCH.format(ext=ext, name=_FORMAT_NAMES[ext]), code="damaged", details="unknown binary content"
        )
    if ext in (".doc", ".dot"):
        raise DocumentImportError(MSG_LEGACY_OFFICE, code="legacy_office", details="binary .doc")
    raise DocumentImportError(MSG_UNSUPPORTED, code="unsupported_type", details="unknown binary content")


def _read(kind: str, path: Path, force_ocr: bool, progress: Progress, cancel: threading.Event | None) -> ReadResult:
    if kind == "pdf":
        from .pdf_reader import read_pdf

        return read_pdf(path, force_ocr=force_ocr, progress=progress, cancel=cancel)
    if kind == "docx":
        from .docx_reader import read_docx

        return read_docx(path, progress=progress, cancel=cancel)
    if kind == "epub":
        from .epub_reader import read_epub

        return read_epub(path, progress=progress, cancel=cancel)
    from .txt_reader import read_txt

    return read_txt(path, progress=progress, cancel=cancel)


def import_document(
    path: Path,
    *,
    original_name: str | None = None,
    force_ocr: bool = False,
    progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> Document:
    """Read a PDF/DOCX/EPUB/TXT file into a :class:`Document`.

    ``progress(fraction, text)`` receives values 0..1 (monotonic) with a
    Norwegian status text; ``cancel`` is checked regularly and raises
    :class:`~medleser.errors.CancelledError`. All other problems raise
    :class:`~medleser.errors.DocumentImportError` with a Norwegian message.
    """
    path = Path(path)
    report = Progress(progress)
    check_cancel(cancel)
    report(0.0, "Kontrollerer filen …")
    source_name = _source_name(original_name, path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise DocumentImportError(MSG_NOT_FOUND, code="not_found", details=str(exc)) from exc
    if not path.is_file():
        raise DocumentImportError(MSG_NOT_FOUND, code="not_found", details="not a regular file")
    size = stat.st_size
    if size == 0:
        raise DocumentImportError(MSG_EMPTY, code="empty_file")
    if size > config.MAX_UPLOAD_BYTES:
        raise DocumentImportError(
            f"Filen er for stor. Maksimal størrelse er {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            code="file_too_large",
            details=f"{size} bytes",
        )
    kind = detect_type(path, source_name)
    check_cancel(cancel)
    result = _read(kind, path, force_ocr, report.sub(0.02, 0.95), cancel)
    check_cancel(cancel)

    report(0.96, "Ordner teksten …")
    blocks = list(result.blocks)
    mark_reference_sections(blocks)
    blocks = finalize_blocks(blocks)
    if not blocks:
        raise DocumentImportError(MSG_NO_TEXT, code="no_text", details=f"{kind} without text blocks")
    language, confidence = detect_language(language_sample(blocks))
    title = choose_title(result.title, blocks, Path(source_name).stem)

    warnings: list[DocWarning] = []
    if result.ocr_used:
        warnings.append(DocWarning("ocr_used", WARN_OCR_USED))
    for w in result.warnings:
        if not any(x.code == w.code for x in warnings):
            warnings.append(w)
    metadata = {str(k): str(v) for k, v in result.metadata.items() if v not in (None, "")}

    document = Document(
        title=title,
        source_name=source_name,
        source_type=kind,
        size_bytes=size,
        blocks=blocks,
        page_count=result.page_count,
        language=language,
        language_confidence=confidence,
        warnings=warnings,
        ocr_used=result.ocr_used,
        metadata=metadata,
    )
    report(1.0, "Ferdig")
    return document
