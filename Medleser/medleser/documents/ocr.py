"""Text recognition (OCR) with the built-in Windows OCR engine (Windows.Media.Ocr).

* Language: Norwegian bokmål («nb») when that recogniser is installed,
  otherwise the user's profile languages, otherwise English (en-US), otherwise
  any installed recogniser.
* Thread safety: the COM MTA is pinned for the process
  (:func:`medleser.winrt_support.ensure_mta`) before any WinRT use; every call
  runs its own asyncio event loop (``asyncio.run``); when the calling thread
  already runs a loop, the work is done in a short-lived helper thread.
  Engines are cached per thread.
* Without pywinrt or without any OCR language the functions raise
  :class:`OcrUnavailableError` (a DocumentImportError with a Norwegian
  message); :func:`ocr_available` lets callers check first.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from ..errors import DocumentImportError

MSG_UNAVAILABLE = (
    "Tekstgjenkjenning (OCR) er ikke tilgjengelig på denne maskinen. Legg til norsk eller "
    "engelsk språk med tekstgjenkjenning i Windows-innstillingene (Tid og språk > Språk og "
    "område) og prøv igjen."
)
DEFAULT_DPI = 200
MAX_DPI = 300
_MAX_PIXELS = 9000  # stay below OcrEngine.max_image_dimension (10000)


class OcrUnavailableError(DocumentImportError):
    def __init__(self, details: str | None = None) -> None:
        super().__init__(MSG_UNAVAILABLE, code="ocr_unavailable", details=details)


@dataclass
class OcrWord:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class OcrLine:
    text: str
    words: list[OcrWord] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(w.x0 for w in self.words),
            min(w.y0 for w in self.words),
            max(w.x1 for w in self.words),
            max(w.y1 for w in self.words),
        )


@dataclass
class OcrPage:
    """OCR result; word coordinates are in PDF points of the rendered (upright) page."""

    lines: list[OcrLine]
    width: float
    height: float
    language: str


_thread_state = threading.local()
_probe_lock = threading.Lock()
_probe_result: tuple[bool, str] | None = None


def _winrt() -> dict[str, Any]:
    """Import the WinRT OCR types (every OCR entry point goes through here).

    The COM multi-threaded apartment is pinned first (``ensure_mta``):
    without it, WinRT calls from short-lived worker threads crash the
    process once the last MTA thread has exited.
    """
    try:
        from ..winrt_support import ensure_mta

        ensure_mta()
    except Exception as exc:  # noqa: BLE001 - never risk a crash: no OCR instead
        raise OcrUnavailableError(f"COM MTA setup failed: {exc!r}") from exc
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
    except Exception as exc:  # noqa: BLE001 - not Windows / pywinrt missing
        raise OcrUnavailableError(f"winrt import failed: {exc!r}") from exc
    return {
        "Language": Language,
        "BitmapPixelFormat": BitmapPixelFormat,
        "SoftwareBitmap": SoftwareBitmap,
        "OcrEngine": OcrEngine,
        "DataWriter": DataWriter,
    }


def available_languages() -> list[str]:
    """Language tags of the installed OCR recognisers (empty when OCR is unavailable)."""
    try:
        w = _winrt()
        return [lang.language_tag for lang in w["OcrEngine"].available_recognizer_languages]
    except Exception:  # noqa: BLE001
        return []


def _create_engine(w: dict[str, Any]) -> Any:
    engine_cls = w["OcrEngine"]
    language_cls = w["Language"]
    tags = [t for t in available_languages()]
    preferred = [t for t in tags if t.lower().split("-")[0] in ("nb", "no", "nn")]
    for tag in preferred:
        engine = engine_cls.try_create_from_language(language_cls(tag))
        if engine is not None:
            return engine
    engine = engine_cls.try_create_from_user_profile_languages()
    if engine is not None:
        return engine
    for tag in ["en-US"] + tags:
        try:
            lang = language_cls(tag)
            if engine_cls.is_language_supported(lang):
                engine = engine_cls.try_create_from_language(lang)
                if engine is not None:
                    return engine
        except Exception:  # noqa: BLE001
            continue
    raise OcrUnavailableError("no OCR recogniser language installed")


def _engine(w: dict[str, Any]) -> Any:
    engine = getattr(_thread_state, "engine", None)
    if engine is None:
        engine = _create_engine(w)
        _thread_state.engine = engine
    return engine


def ocr_available() -> bool:
    """True when Windows OCR can be used (result cached for the process)."""
    global _probe_result
    with _probe_lock:
        if _probe_result is None:
            try:
                w = _winrt()
                _create_engine(w)
                _probe_result = (True, "")
            except Exception as exc:  # noqa: BLE001
                _probe_result = (False, repr(exc))
        return _probe_result[0]


def _run(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine to completion from any thread (fresh event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc

    t = threading.Thread(target=target, name="medleser-ocr", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def recognize_rgb(samples: bytes, width: int, height: int, channels: int) -> tuple[list[OcrLine], str]:
    """OCR an RGB/RGBA/gray pixel buffer. Returns (lines in pixel coordinates, language tag)."""
    import numpy as np

    w = _winrt()
    if width <= 0 or height <= 0:
        return [], ""
    arr = np.frombuffer(samples, dtype=np.uint8)
    stride = len(samples) // height
    arr = arr[: stride * height].reshape(height, stride)[:, : width * channels].reshape(height, width, channels)
    bgra = np.empty((height, width, 4), dtype=np.uint8)
    if channels >= 3:
        bgra[..., 0] = arr[..., 2]
        bgra[..., 1] = arr[..., 1]
        bgra[..., 2] = arr[..., 0]
    else:
        bgra[..., 0] = arr[..., 0]
        bgra[..., 1] = arr[..., 0]
        bgra[..., 2] = arr[..., 0]
    bgra[..., 3] = 255
    data = bgra.tobytes()

    async def run() -> tuple[list[OcrLine], str]:
        engine = _engine(w)
        writer = w["DataWriter"]()
        writer.write_bytes(data)
        bitmap = w["SoftwareBitmap"].create_copy_from_buffer(
            writer.detach_buffer(), w["BitmapPixelFormat"].BGRA8, width, height
        )
        result = await engine.recognize_async(bitmap)
        lines: list[OcrLine] = []
        for line in result.lines:
            words = []
            for word in line.words:
                r = word.bounding_rect
                text = word.text or ""
                if text.strip():
                    words.append(OcrWord(text, r.x, r.y, r.x + r.width, r.y + r.height))
            if words:
                lines.append(OcrLine(" ".join(wd.text for wd in words), words))
        return lines, engine.recognizer_language.language_tag

    return _run(run)


def choose_dpi(width_pt: float, height_pt: float, dpi: int | None = None) -> int:
    """Render resolution: 200 dpi (300 for small pages), capped below the engine limit."""
    if dpi is None:
        dpi = MAX_DPI if max(width_pt, height_pt) < 500 else DEFAULT_DPI
    longest = max(1.0, width_pt, height_pt)
    cap = int(_MAX_PIXELS * 72 / longest)
    return max(36, min(dpi, cap))


def ocr_pdf_page(page: Any, *, dpi: int | None = None) -> OcrPage:
    """Render a PyMuPDF page (as displayed, rotation applied) and OCR it."""
    rect = page.rect
    use_dpi = choose_dpi(rect.width, rect.height, dpi)
    pix = page.get_pixmap(dpi=use_dpi, alpha=False)
    lines, lang = recognize_rgb(pix.samples, pix.width, pix.height, pix.n)
    scale = 72.0 / use_dpi
    for line in lines:
        for wd in line.words:
            wd.x0 *= scale
            wd.y0 *= scale
            wd.x1 *= scale
            wd.y1 *= scale
    return OcrPage(lines=lines, width=rect.width, height=rect.height, language=lang)
