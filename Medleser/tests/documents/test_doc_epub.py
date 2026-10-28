import pytest

from medleser.documents.importer import import_document
from medleser.errors import DocumentImportError

from . import _builders as B


def test_epub_two_chapters_without_script(tmp_path):
    doc = import_document(B.make_epub(tmp_path / "bok.epub"))
    text = doc.plain_text()
    assert doc.source_type == "epub"
    assert doc.title == "Lydbok for studenter"
    assert doc.language == "nb"
    assert doc.metadata["author"] == "Ola Nordmann"
    assert "SKRIPTTEKST" not in text and "color: red" not in text
    assert "NAVIGASJONSTEKST" not in text
    headings = [b.text for b in doc.blocks if b.kind == "heading"]
    assert headings == ["Kapittel 1: Begynnelsen", "Kapittel 2: Fortsettelsen"]
    assert [c["title"] for c in doc.chapters()] == headings
    assert text.index("Kapittel 1") < text.index("Kapittel 2")
    assert "lese faglitteratur" in text  # &nbsp; handled
    kinds = {b.kind for b in doc.blocks}
    assert {"list_item", "quote", "footnote", "table"} <= kinds
    para = next(b for b in doc.blocks if "Studenter med dysleksi" in b.text)
    assert [para.text[s.start:s.end] for s in para.spans if s.kind == "footnote_ref"] == ["1"]
    table = next(b for b in doc.blocks if b.kind == "table").table
    assert table.header == ["Navn", "Alder"] and table.rows == [["Kari", "21"]]
    assert "12" not in [b.text for b in doc.blocks]  # page-break marker dropped


def test_epub_with_encrypted_content_is_drm(tmp_path):
    path = B.make_epub(tmp_path / "drm.epub", extra={
        "META-INF/encryption.xml": B.encryption_xml("http://www.w3.org/2001/04/xmlenc#aes128-cbc"),
    })
    with pytest.raises(DocumentImportError) as exc:
        import_document(path)
    assert exc.value.code == "epub_drm"
    assert exc.value.message == "Denne e-boken er kopibeskyttet (DRM) og kan ikke åpnes i Medleser."


def test_epub_with_rights_file_is_drm(tmp_path):
    path = B.make_epub(tmp_path / "adobe.epub", extra={"META-INF/rights.xml": "<rights/>"})
    with pytest.raises(DocumentImportError) as exc:
        import_document(path)
    assert exc.value.code == "epub_drm"


def test_epub_font_obfuscation_is_allowed(tmp_path):
    path = B.make_epub(tmp_path / "fonter.epub", extra={
        "META-INF/encryption.xml": B.encryption_xml("http://www.idpf.org/2008/embedding", "OEBPS/font.otf"),
    })
    doc = import_document(path)
    assert "Kapittel 2" in doc.plain_text()


def test_epub_paths_cannot_escape_the_archive(tmp_path):
    chapter = ("../../../../Windows/win.ini", "<html><body><p>Utenfor</p></body></html>")
    path = B.make_epub(tmp_path / "farlig.epub", chapters=[chapter, ("ok.xhtml", B.CHAPTER_2)])
    doc = import_document(path)
    assert "Utenfor" not in doc.plain_text()
    assert "Kapittel 2" in doc.plain_text()


def test_epub_entity_attack_is_not_expanded(tmp_path):
    evil = """<?xml version="1.0"?>
<!DOCTYPE html [<!ENTITY lol "lollollollollollollol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;">]>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>&lol2;</p></body></html>"""
    path = B.make_epub(tmp_path / "lol.epub", chapters=[("evil.xhtml", evil), ("ok.xhtml", B.CHAPTER_2)])
    doc = import_document(path)
    assert "lol" not in doc.plain_text()
    assert "epub_parts_skipped" in [w.code for w in doc.warnings]
