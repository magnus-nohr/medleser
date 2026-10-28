import threading
import zipfile

import pytest

from medleser import config
from medleser.documents.importer import import_document
from medleser.errors import CancelledError, DocumentImportError

from . import _builders as B

PE_HEADER = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + b"\x00" * 200


def expect_error(path, code, **kwargs):
    with pytest.raises(DocumentImportError) as exc:
        import_document(path, **kwargs)
    assert exc.value.code == code, exc.value.details
    assert exc.value.message  # Norwegian, user-facing
    return exc.value


def test_executable_renamed_to_pdf_is_refused(tmp_path):
    path = tmp_path / "faktura.pdf"
    path.write_bytes(PE_HEADER)
    err = expect_error(path, "unsupported_type")
    assert "program" in err.message


def test_text_named_pdf_is_refused(tmp_path):
    path = tmp_path / "rapport.pdf"
    path.write_text("Dette er bare tekst.", encoding="utf-8")
    expect_error(path, "damaged")


def test_legacy_doc_is_refused(tmp_path):
    path = tmp_path / "gammel.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600)
    err = expect_error(path, "legacy_office")
    assert ".docx" in err.message


def test_unknown_zip_is_refused(tmp_path):
    path = tmp_path / "arkiv.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hei.txt", "hei")
    expect_error(path, "unsupported_type")


def test_zip_bomb_is_refused(tmp_path):
    path = tmp_path / "bombe.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", B.DOCX_CONTENT_TYPES.format(main=B.DOCX_MAIN))
        zf.writestr("word/document.xml", b"\x00" * (20 * 1024 * 1024))
    assert path.stat().st_size < 200_000
    expect_error(path, "zip_suspicious")


def test_total_uncompressed_size_limit(tmp_path, monkeypatch):
    path = B.make_epub(tmp_path / "bok.epub")
    monkeypatch.setattr(config, "MAX_UNCOMPRESSED_BYTES", 1000)
    expect_error(path, "zip_suspicious")


def test_file_size_limit(tmp_path, monkeypatch):
    path = tmp_path / "notat.txt"
    path.write_text("Litt tekst " * 10, encoding="utf-8")
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 50)
    err = expect_error(path, "file_too_large")
    assert "for stor" in err.message


def test_empty_and_missing_files(tmp_path):
    empty = tmp_path / "tom.txt"
    empty.write_bytes(b"")
    expect_error(empty, "empty_file")
    expect_error(tmp_path / "finnes-ikke.pdf", "not_found")


def test_binary_named_txt_is_refused(tmp_path):
    path = tmp_path / "bilde.txt"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02" * 100)
    expect_error(path, "unsupported_type")


def test_type_comes_from_content_not_extension(tmp_path):
    path = B.python_docx_document(tmp_path / "egentlig-word.pdf")
    doc = import_document(path)
    assert doc.source_type == "docx"


def test_cancel_before_start(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CancelledError):
        import_document(B.norwegian_pdf(tmp_path / "a.pdf"), cancel=cancel)


def test_cancel_during_import(tmp_path):
    path = B.header_footer_pdf(tmp_path / "lang.pdf", pages=6)
    cancel = threading.Event()
    seen = []

    def progress(fraction, text):
        seen.append((fraction, text))
        if "side 2" in text:
            cancel.set()

    with pytest.raises(CancelledError) as exc:
        import_document(path, progress=progress, cancel=cancel)
    assert exc.value.code == "cancelled"
    assert not any("side 4" in t for _, t in seen)


@pytest.mark.parametrize("builder, name", [
    (B.header_footer_pdf, "a.pdf"), (B.python_docx_document, "b.docx"), (B.make_epub, "c.epub"),
])
def test_progress_is_monotonic_and_norwegian(tmp_path, builder, name):
    calls = []
    import_document(builder(tmp_path / name), progress=lambda f, t: calls.append((f, t)))
    fractions = [f for f, _ in calls]
    assert fractions == sorted(fractions)
    assert fractions[0] == 0.0 and fractions[-1] == 1.0
    assert all(0.0 <= f <= 1.0 for f in fractions)
    assert calls[-1][1] == "Ferdig"
    assert any("Leser" in t for _, t in calls)


def test_document_round_trips_through_dict(tmp_path):
    from medleser.documents.model import Document

    doc = import_document(B.references_pdf(tmp_path / "ref.pdf"))
    again = Document.from_dict(doc.to_dict())
    assert again.to_dict() == doc.to_dict()
    assert again.size_bytes == (tmp_path / "ref.pdf").stat().st_size


def test_package_exports_import_document():
    import medleser.documents as documents

    assert documents.import_document is import_document
