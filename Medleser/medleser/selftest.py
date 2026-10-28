"""Self-test used to verify a packaged build: ``Medleser.exe --selftest result.json``.

Checks that every bundled component loads and works on this machine. It never
contacts paid services and does not change user data.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable


def _check(results: dict[str, Any], name: str, fn: Callable[[], Any], critical: bool = True) -> None:
    t0 = time.time()
    try:
        detail = fn()
        results[name] = {"ok": True, "detail": detail, "seconds": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        results[name] = {"ok": False, "critical": critical, "error": repr(exc),
                         "trace": traceback.format_exc(limit=4)}


def run(output: Path) -> int:
    from .runtime_fix import preload_cpp_runtime
    from .winrt_support import ensure_mta

    results: dict[str, Any] = {"python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
                               "cpp_runtime": preload_cpp_runtime(), "mta_pinned": ensure_mta()}

    def web_ui():
        from .config import resource_dir

        index = resource_dir() / "web" / "index.html"
        assert index.exists(), index
        return str(index.parent)

    def pdf():
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Æøå test")
        return {"pymupdf": pymupdf.VersionBind, "text": page.get_text().strip()}

    def docx():
        import docx as _docx

        return _docx.__name__

    def document_import():
        import pymupdf

        from .documents.importer import import_document

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "Innledning", fontsize=18)
            page.insert_text((72, 110), "Dette er en norsk testside for Medleser med æøå.", fontsize=11)
            doc.save(path)
            result = import_document(path)
            return {"blocks": len(result.blocks), "language": result.language, "title": result.title}

    def piper_engine():
        import onnxruntime
        from piper.phonemize_espeak import EspeakPhonemizer

        phon = EspeakPhonemizer().phonemize("nb", "Hei verden.")
        return {"onnxruntime": onnxruntime.__version__, "phonemes": "".join(phon[0])}

    def piper_voice():
        from .config import AppPaths
        from .speech.providers import piper_catalog
        from .speech.providers.base import SynthesisRequest
        from .speech.providers.piper_provider import PiperProvider

        root = AppPaths.from_env().piper_voices
        keys = piper_catalog.installed_keys(root)
        if not keys:
            return "ingen stemme installert (hoppet over)"
        provider = PiperProvider(root)
        voice = provider.list_voices()[0]
        result = provider.synthesize(SynthesisRequest(text="Dette er en test.", voice_id=voice.id, language=voice.language))
        return {"voice": voice.id, "seconds": round(result.duration, 2), "precision": result.precision}

    def windows_voice():
        from .speech.providers.base import SynthesisRequest
        from .speech.providers.windows_provider import WindowsVoiceProvider

        provider = WindowsVoiceProvider()
        voices = provider.list_voices()
        if not voices:
            return "ingen Windows-stemmer"
        result = provider.synthesize(SynthesisRequest(text="Dette er en test.", voice_id=voices[0].id, language=voices[0].language))
        return {"voices": len(voices), "seconds": round(result.duration, 2), "precision": result.precision}

    def mp3():
        import numpy as np

        from .speech.mp3 import Mp3Tags, Mp3Writer, probe_mp3

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.mp3"
            writer = Mp3Writer(path, 22050, tags=Mp3Tags(title="Test"))
            writer.write((np.sin(np.arange(22050) / 8) * 4000).astype(np.int16))
            writer.close()
            info = probe_mp3(path)
            assert info.ok, info
            return round(info.duration, 2)

    def ocr():
        from winrt.windows.media.ocr import OcrEngine

        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]

    def keyring_backend():
        import keyring

        return type(keyring.get_keyring()).__name__

    def webview():
        import webview as _webview

        return getattr(_webview, "__version__", "ok")

    _check(results, "web_ui", web_ui)
    _check(results, "pdf", pdf)
    _check(results, "docx", docx)
    _check(results, "document_import", document_import)
    _check(results, "piper_engine", piper_engine)
    _check(results, "piper_voice", piper_voice, critical=False)
    _check(results, "windows_voice", windows_voice, critical=False)
    _check(results, "mp3", mp3)
    _check(results, "ocr", ocr, critical=False)
    _check(results, "keyring", keyring_backend)
    _check(results, "webview", webview)

    failed = [k for k, v in results.items() if isinstance(v, dict) and v.get("ok") is False and v.get("critical", True)]
    results["passed"] = not failed
    results["failed"] = failed
    output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if not failed else 1
