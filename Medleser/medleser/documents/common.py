"""Shared types and helpers for the format readers."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import CancelledError
from .model import Block, DocWarning

ProgressCallback = Callable[[float, str], None]

# User-facing texts (Norwegian bokmål) used by several readers.
MSG_NO_TEXT = (
    "Vi klarte ikke å hente ut tekst fra dette dokumentet. "
    "Prøv å aktivere tekstgjenkjenning (OCR)."
)
MSG_NO_TEXT_NO_OCR = (
    "Vi klarte ikke å hente ut tekst fra dette dokumentet. Det ser ut til å være skannet "
    "(bilder av tekst), og tekstgjenkjenning (OCR) er ikke tilgjengelig på denne maskinen. "
    "Legg til norsk eller engelsk språk med tekstgjenkjenning i Windows-innstillingene "
    "(Tid og språk > Språk og område) og prøv igjen."
)
MSG_NO_TEXT_AFTER_OCR = (
    "Vi klarte ikke å hente ut tekst fra dette dokumentet, heller ikke med tekstgjenkjenning "
    "(OCR). Dokumentet kan være tomt, eller bildekvaliteten kan være for dårlig."
)
MSG_EMPTY_DOCUMENT = "Dokumentet inneholder ingen tekst som kan leses opp."
WARN_OCR_USED = (
    "Teksten er hentet ut med tekstgjenkjenning (OCR) og kan inneholde feil. "
    "Les gjennom og rett teksten før opplesning."
)


@dataclass
class ReadResult:
    """What a format reader returns; the importer turns it into a Document."""

    blocks: list[Block]
    title: str | None = None  # metadata title (raw, checked by the importer)
    metadata: dict[str, str] = field(default_factory=dict)
    page_count: int | None = None
    warnings: list[DocWarning] = field(default_factory=list)
    ocr_used: bool = False

    def warn(self, code: str, message: str) -> None:
        if not any(w.code == code for w in self.warnings):
            self.warnings.append(DocWarning(code, message))


class Progress:
    """Progress reporter for a sub-range of the overall work (monotonic)."""

    def __init__(
        self,
        callback: ProgressCallback | None,
        start: float = 0.0,
        end: float = 1.0,
        _shared: list[float] | None = None,
    ) -> None:
        self._callback = callback
        self._start = start
        self._end = end
        self._last = _shared if _shared is not None else [0.0]

    def __call__(self, fraction: float, text: str) -> None:
        if self._callback is None:
            return
        f = min(1.0, max(0.0, fraction))
        value = self._start + (self._end - self._start) * f
        value = max(value, self._last[0])
        self._last[0] = value
        self._callback(round(value, 4), text)

    def sub(self, start: float, end: float) -> "Progress":
        """Reporter for ``[start, end]`` of this reporter's range."""
        span = self._end - self._start
        return Progress(
            self._callback, self._start + span * start, self._start + span * end, self._last
        )


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError()
