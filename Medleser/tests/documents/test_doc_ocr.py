import asyncio
import threading

import pytest

from medleser.documents import ocr
from medleser.documents.importer import import_document
from medleser.errors import DocumentImportError

from . import _builders as B

needs_ocr = pytest.mark.skipif(not ocr.ocr_available(), reason="Windows OCR er ikke tilgjengelig")


def _run_in_thread(fn):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


@pytest.mark.windows
@needs_ocr
def test_scanned_pdf_is_recognised_in_worker_thread(tmp_path):
    path = B.scanned_pdf(tmp_path / "skannet.pdf")
    doc = _run_in_thread(lambda: import_document(path))
    text = doc.plain_text().lower()
    assert "skannet" in text and "norsk" in text
    assert doc.ocr_used
    assert doc.warnings[0].code == "ocr_used"
    assert "tekstgjenkjenning (OCR)" in doc.warnings[0].message
    assert all(b.ocr for b in doc.blocks)
    assert doc.language == "nb"


@pytest.mark.windows
@needs_ocr
def test_force_ocr_replaces_text_layer(tmp_path):
    doc = import_document(B.norwegian_pdf(tmp_path / "tekst.pdf"), force_ocr=True)
    assert doc.ocr_used and all(b.ocr for b in doc.blocks)
    assert "dysleksi" in doc.plain_text()


@pytest.mark.windows
@needs_ocr
def test_ocr_works_inside_running_event_loop(tmp_path):
    path = B.scanned_pdf(tmp_path / "skannet.pdf", lines=["Hei verden fra en skannet side."])

    async def main():
        return import_document(path)

    doc = asyncio.run(main())
    assert "verden" in doc.plain_text().lower()


@pytest.mark.windows
@needs_ocr
def test_blank_scan_reports_that_ocr_found_nothing(tmp_path):
    with pytest.raises(DocumentImportError) as exc:
        import_document(B.scanned_pdf(tmp_path / "blank.pdf", lines=[]))
    assert exc.value.code == "no_text"
    assert "heller ikke med tekstgjenkjenning" in exc.value.message


def test_scanned_pdf_without_ocr_gives_helpful_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    with pytest.raises(DocumentImportError) as exc:
        import_document(B.scanned_pdf(tmp_path / "skannet.pdf"))
    assert exc.value.code == "ocr_unavailable"
    assert "tekstgjenkjenning" in exc.value.message.lower()


def test_force_ocr_without_ocr_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "ocr_available", lambda: False)
    with pytest.raises(DocumentImportError) as exc:
        import_document(B.norwegian_pdf(tmp_path / "tekst.pdf"), force_ocr=True)
    assert exc.value.code == "ocr_unavailable"


def test_ocr_unavailable_without_winrt(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("winrt"):
            raise ImportError("no winrt")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ocr.OcrUnavailableError) as exc:
        ocr.recognize_rgb(b"\xff" * 30, 10, 1, 3)
    assert exc.value.code == "ocr_unavailable"
    assert ocr.available_languages() == []


def test_dpi_choice_respects_engine_limit():
    assert ocr.choose_dpi(595, 842) == 200
    assert ocr.choose_dpi(300, 400) == 300
    big = ocr.choose_dpi(3370, 4768)  # A0 poster
    assert big * 4768 / 72 <= 9000
