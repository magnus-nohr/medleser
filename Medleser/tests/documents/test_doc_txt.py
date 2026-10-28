import pytest

from medleser.documents.importer import import_document

TEXT = (
    "# Lesing med lyd\n"
    "\n"
    "Studenter med dysleksi bruker ofte lengre tid\n"
    "på å lese faglitteratur. Det er derfor viktig at de får hjelpemidler.\n"
    "\n"
    "## Bakgrunn\n"
    "Lærerne vet hvordan slike verktøy fungerer, og både æ, ø og å\n"
    "må uttales riktig av stemmen.\n"
    "\n"
    "- første punkt\n"
    "- andre punkt\n"
)


@pytest.mark.parametrize(
    "encoding, prefix",
    [("utf-8", b""), ("utf-8", b"\xef\xbb\xbf"), ("cp1252", b""), ("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")],
)
def test_txt_encodings_and_structure(tmp_path, encoding, prefix):
    path = tmp_path / "notat.txt"
    path.write_bytes(prefix + TEXT.replace("\n", "\r\n").encode(encoding))
    doc = import_document(path)
    assert doc.source_type == "txt"
    summary = [(b.kind, b.level, b.text) for b in doc.blocks]
    assert summary == [
        ("heading", 1, "Lesing med lyd"),
        ("paragraph", 0, "Studenter med dysleksi bruker ofte lengre tid på å lese faglitteratur. "
                         "Det er derfor viktig at de får hjelpemidler."),
        ("heading", 2, "Bakgrunn"),
        ("paragraph", 0, "Lærerne vet hvordan slike verktøy fungerer, og både æ, ø og å må uttales riktig av stemmen."),
        ("list_item", 0, "første punkt"),
        ("list_item", 0, "andre punkt"),
    ]
    assert doc.title == "Lesing med lyd"
    assert doc.language == "nb"
    assert doc.page_count is None


def test_txt_without_headings_uses_file_name_as_title(tmp_path):
    path = tmp_path / "upload-123.txt"
    path.write_text("Dette er en kort tekst om lesing.\nDen har to linjer.", encoding="utf-8")
    doc = import_document(path, original_name="C:\\Brukere\\kari\\Mine_notater.txt")
    assert doc.source_name == "Mine_notater.txt"
    assert doc.title == "Mine notater"
    assert doc.blocks[0].text == "Dette er en kort tekst om lesing. Den har to linjer."


def test_txt_spans_are_detected(tmp_path):
    path = tmp_path / "lenker.txt"
    path.write_text("Les mer på https://www.uio.no/ eller skriv til post@hiof.no.", encoding="utf-8")
    block = import_document(path).blocks[0]
    assert [(s.kind, block.text[s.start:s.end]) for s in block.spans] == [
        ("url", "https://www.uio.no/"),
        ("email", "post@hiof.no"),
    ]
