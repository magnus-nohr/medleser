"""Zip container checks (DOCX/EPUB) with zip-bomb guards.

Archives are only ever read into memory, member by member, with size
limits; nothing is extracted to disk and member names are never used as
file-system paths.

Guards (applied before any member is read):
* at most ``MAX_ENTRIES`` members, no duplicate names, no encrypted members;
* the sum of declared uncompressed sizes must not exceed
  ``config.MAX_UNCOMPRESSED_BYTES``;
* members larger than 1 MiB may not be compressed more than ``MAX_RATIO``
  times (normal XML compresses 5–30×, zip bombs 1000×), and the archive as a
  whole may not expand more than ``MAX_TOTAL_RATIO`` times.
Python's zipfile never returns more bytes than a member declares, so the
declared sizes really bound memory use.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .. import config
from ..errors import DocumentImportError

MAX_ENTRIES = 20_000
MAX_RATIO = 200
MAX_TOTAL_RATIO = 150
RATIO_MIN_SIZE = 1 << 20

MSG_DAMAGED = "Filen ser ut til å være skadet og kan ikke åpnes."
MSG_SUSPICIOUS = (
    "Filen er pakket på en uvanlig måte og kan være skadelig. "
    "Medleser åpner den derfor ikke."
)
MSG_ENCRYPTED = "Filen er kryptert eller passordbeskyttet og kan ikke åpnes."


def open_zip(path: Path) -> zipfile.ZipFile:
    """Open ``path`` as a zip archive and run all guards. Caller closes it."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"zip: {exc}") from exc
    try:
        check_zip(zf, path.stat().st_size)
    except BaseException:
        zf.close()
        raise
    return zf


def check_zip(zf: zipfile.ZipFile, archive_size: int) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise DocumentImportError(
            MSG_SUSPICIOUS, code="zip_suspicious", details=f"too many zip entries: {len(infos)}"
        )
    names: set[str] = set()
    total = 0
    for info in infos:
        if info.filename in names:
            raise DocumentImportError(
                MSG_DAMAGED, code="damaged", details=f"duplicate zip entry: {info.filename!r}"
            )
        names.add(info.filename)
        if info.flag_bits & 0x1:
            raise DocumentImportError(MSG_ENCRYPTED, code="encrypted", details="encrypted zip entry")
        size = info.file_size
        comp = info.compress_size
        if size < 0 or comp < 0:
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details="negative zip sizes")
        total += size
        if size > RATIO_MIN_SIZE:
            if comp <= 0 or size / comp > MAX_RATIO:
                raise DocumentImportError(
                    MSG_SUSPICIOUS,
                    code="zip_suspicious",
                    details=f"compression ratio too high for {info.filename!r}: {size}/{comp}",
                )
    if total > config.MAX_UNCOMPRESSED_BYTES:
        raise DocumentImportError(
            MSG_SUSPICIOUS,
            code="zip_suspicious",
            details=f"uncompressed size {total} exceeds {config.MAX_UNCOMPRESSED_BYTES}",
        )
    if total > 50 * RATIO_MIN_SIZE and total / max(1, archive_size) > MAX_TOTAL_RATIO:
        raise DocumentImportError(
            MSG_SUSPICIOUS, code="zip_suspicious", details=f"archive expands {total}/{archive_size}"
        )


def read_member(zf: zipfile.ZipFile, name: str, *, max_bytes: int | None = None) -> bytes:
    """Read one member into memory, never more than ``max_bytes``."""
    limit = config.MAX_UNCOMPRESSED_BYTES if max_bytes is None else max_bytes
    try:
        info = zf.getinfo(name)
    except KeyError as exc:
        raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"missing entry {name!r}") from exc
    if info.file_size > limit:
        raise DocumentImportError(
            MSG_SUSPICIOUS, code="zip_suspicious", details=f"entry too large: {name!r}"
        )
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError, EOFError) as exc:
        raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"zip read {name!r}: {exc}") from exc
    if len(data) > limit:
        raise DocumentImportError(
            MSG_SUSPICIOUS, code="zip_suspicious", details=f"entry larger than declared: {name!r}"
        )
    return data
