import time

import pymupdf
import pytest

from medleser.documents.importer import import_document
from medleser.errors import DocumentImportError

from . import _builders as B


def kinds(doc, kind):
    return [b for b in doc.blocks if b.kind == kind]


def test_norwegian_pdf_keeps_letters_and_detects_bokmal(tmp_path):
    doc = import_document(B.norwegian_pdf(tmp_path / "artikkel.pdf"))
    text = doc.plain_text()
    assert "høyere utdanning" in text and "æ, ø og å" in text
    assert doc.source_type == "pdf" and doc.page_count == 1
    assert doc.language == "nb" and doc.language_confidence > 0.5
    assert doc.title == "Lesing med lyd i høyere utdanning"  # empty metadata title → first heading
    assert doc.blocks[0].kind == "heading" and doc.blocks[0].level == 1
    assert doc.metadata.get("author") == "Kari Nordmann"
    assert [b.id for b in doc.blocks] == [f"b{i:05d}" for i in range(len(doc.blocks))]
    assert all(b.page == 1 for b in doc.blocks)


def test_two_column_reading_order(tmp_path):
    doc = import_document(B.two_column_pdf(tmp_path / "paper.pdf"))
    text = doc.plain_text()
    assert text.startswith("Reading Along")
    assert text.find("Abstract.") < text.find("Alpha")
    assert text.rfind("Alpha") < text.find("Omega"), "left column must be read completely before the right"
    assert doc.language == "en"
    for b in doc.blocks:  # no block mixes the two columns
        assert not ("Alpha" in b.text and "Omega" in b.text)


def test_typeset_article_with_paragraphs_flowing_over_columns_and_pages(tmp_path):
    doc = import_document(B.story_article_pdf(tmp_path / "artikkel.pdf"))
    assert doc.page_count == 2
    assert doc.title == "Synkron lydstøtte for studenter med dysleksi"
    headings = [(b.text, b.level) for b in kinds(doc, "heading")]
    assert headings == [("Synkron lydstøtte for studenter med dysleksi", 1), ("Innledning", 2), ("Metode", 2),
                        ("Diskusjon", 2), ("Referanser", 2)]
    assert [c["title"] for c in doc.chapters()] == ["Innledning", "Metode", "Diskusjon"]
    assert [b.text for b in kinds(doc, "paragraph")] == B.STORY_PARAGRAPHS
    refs = kinds(doc, "reference")
    assert len(refs) == 6 and all(b.section == "references" for b in refs)
    assert all([s.kind for s in b.spans] == ["doi"] for b in refs)
    assert [b.text for b in kinds(doc, "page_number")] == ["1", "2"]
    assert len(kinds(doc, "header")) == 2


def test_running_headers_footers_and_page_numbers(tmp_path):
    doc = import_document(B.header_footer_pdf(tmp_path / "hf.pdf", pages=4))
    headers = kinds(doc, "header")
    footers = kinds(doc, "footer")
    numbers = kinds(doc, "page_number")
    assert len(headers) == 4 and all(b.text == "Tidsskrift for lesing og læring 2024" for b in headers)
    assert len(footers) == 4 and all(b.text == "Høgskolen i Østfold" for b in footers)
    assert [b.text for b in numbers] == [f"Side {i} av 4" for i in range(1, 5)]
    assert [b.page for b in numbers] == [1, 2, 3, 4]
    body = " ".join(b.text for b in kinds(doc, "paragraph"))
    assert "Tidsskrift" not in body and "Østfold" not in body


def test_footnote_and_superscript_marker(tmp_path):
    doc = import_document(B.footnote_pdf(tmp_path / "fotnote.pdf"))
    notes = kinds(doc, "footnote")
    assert len(notes) == 1 and notes[0].text.startswith("1 Dette er en fotnote")
    para = next(b for b in doc.blocks if "lydbøker i studiene" in b.text)
    assert para.kind == "paragraph"
    refs = [s for s in para.spans if s.kind == "footnote_ref"]
    assert len(refs) == 1
    assert para.text[refs[0].start:refs[0].end] == "1"
    assert para.text[: refs[0].start].endswith("studiene")
    assert "fotnote om kildene" not in para.text


def test_table_with_header(tmp_path):
    doc = import_document(B.table_pdf(tmp_path / "tabell.pdf"))
    tables = kinds(doc, "table")
    assert len(tables) == 1
    t = tables[0].table
    assert t.header == ["Navn", "Alder", "By"]
    assert t.rows == [["Kari", "21", "Oslo"], ["Ola", "23", "Bergen"]]
    assert t.reliable and t.note is None
    assert tables[0].text.splitlines()[1] == "Kari | 21 | Oslo"
    flow = [b.text for b in doc.blocks if b.kind != "table"]
    assert not any("Kari" in t_ or "Bergen" in t_ for t_ in flow)
    order = [b.kind for b in doc.blocks]
    assert order == ["paragraph", "table", "paragraph"]


def test_text_in_figures_is_classified(tmp_path):
    doc = import_document(B.figure_pdf(tmp_path / "figur.pdf"))
    figures = [b.text for b in kinds(doc, "figure_text")]
    assert "Lesetid Forståelse" in figures  # labels on an image
    assert "G0 G1 G2 G3 G4 G5 G6 G7" in figures  # labels of a vector chart
    caption = kinds(doc, "caption")
    assert [b.text for b in caption] == ["Figur 1: Sammenheng mellom lesetid og forståelse."]
    paragraphs = [b.text for b in kinds(doc, "paragraph")]
    assert len(paragraphs) == 2 and not any("G0" in t or "Lesetid" in t for t in paragraphs)


def test_table_with_horizontal_rules_only(tmp_path):
    doc = import_document(B.booktabs_pdf(tmp_path / "booktabs.pdf"))
    table_block = kinds(doc, "table")[0]
    assert table_block.table.header == ["Gruppe", "Antall", "Snitt"]
    assert table_block.table.rows[0] == ["Kontroll", "24", "3.1"]
    assert not table_block.table.reliable and "rutenett" in table_block.table.note
    assert [b.text for b in kinds(doc, "caption")] == ["Tabell 2. Skårer per gruppe"]
    assert "table_uncertain" in [w.code for w in doc.warnings]


def test_table_without_header_and_merged_cells_is_unreliable(tmp_path):
    doc = import_document(B.merged_table_pdf(tmp_path / "flettet.pdf"))
    t = kinds(doc, "table")[0].table
    assert t.header is None
    assert not t.reliable
    assert "overskriftsrad" in t.note and "sammenslåtte" in t.note
    assert t.rows[0] == ["Kari", "Oslo"]


def test_reference_list_entries_and_spans(tmp_path):
    doc = import_document(B.references_pdf(tmp_path / "ref.pdf"))
    heading = next(b for b in doc.blocks if b.text == "Referanser")
    assert heading.kind == "heading" and heading.section == "references"
    refs = kinds(doc, "reference")
    assert len(refs) == 3
    assert all(b.section == "references" for b in refs)
    assert refs[0].text.startswith("Andersen, K. (2019)")
    assert refs[1].text.startswith("Berg, L., & Hansen, T. (2021)")
    assert [(s.kind, refs[0].text[s.start:s.end]) for s in refs[0].spans] == [
        ("doi", "https://doi.org/10.1234/abcd.2019.001")
    ]
    assert ("url", "https://www.example.no/artikkel") in [(s.kind, refs[1].text[s.start:s.end]) for s in refs[1].spans]
    assert ("url", "www.dysleksi.no") in [(s.kind, refs[2].text[s.start:s.end]) for s in refs[2].spans]
    intro = next(b for b in doc.blocks if b.text == "Innledning")
    assert intro.section is None


def test_hyphenation_is_joined(tmp_path):
    doc = import_document(B.hyphenation_pdf(tmp_path / "bindestrek.pdf"))
    text = doc.plain_text()
    assert "bedre informasjon om" in text
    assert "IKT-basert" in text
    assert "lese- og skrivevansker" in text
    assert "e-post" in text
    assert len(doc.blocks) == 1


def test_marker_split_off_by_mupdf_is_reattached(tmp_path):
    path = tmp_path / "markor.pdf"
    d = pymupdf.open()
    page = d.new_page()
    first = "Dette er en lang setning om lesing"
    page.insert_text((72, 100), first, fontsize=11)
    w = B.text_width(first, 11)
    page.insert_text((72 + w + 0.5, 92), "2", fontsize=6)  # raised so far that MuPDF starts a new line
    page.insert_text((72 + w + 4, 100), " som fortsetter etter markøren.", fontsize=11)
    d.save(path)
    block = import_document(path).blocks[0]
    assert block.text == "Dette er en lang setning om lesing2 som fortsetter etter markøren."
    assert [(s.kind, block.text[s.start:s.end]) for s in block.spans] == [("footnote_ref", "2")]


def test_numbered_reference_list(tmp_path):
    path = tmp_path / "nummerert.pdf"
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 80), "References", fontname="hebo", fontsize=13)
    y = 105
    for k in range(1, 4):
        page.insert_text((72, y), f"[{k}] Smith, J. ({2010 + k}). A long title about reading support in higher", fontsize=10)
        page.insert_text((72, y + 13), f"education. Journal of Reading {k}(2), 1-10. doi:10.1000/jr.{k}", fontsize=10)
        y += 29
    d.save(path)
    refs = [b for b in import_document(path).blocks if b.kind == "reference"]
    assert [b.text[:3] for b in refs] == ["[1]", "[2]", "[3]"]
    assert all(b.text.endswith(f"doi:10.1000/jr.{k}") for k, b in enumerate(refs, 1))
    assert all([s.kind for s in b.spans] == ["doi"] for b in refs)


def test_landscape_page_with_rotated_text(tmp_path):
    path = tmp_path / "liggende.pdf"
    d = pymupdf.open()
    page = d.new_page(width=595, height=842)
    lines = B.wrap(B.NB_PARAGRAPH, 600)
    for k, line in enumerate(lines):
        page.insert_text((100 + k * 16, 780), line, fontsize=11, rotate=90)
    page.set_rotation(90)
    d.save(path)
    doc = import_document(path)
    assert [b.kind for b in doc.blocks] == ["paragraph"]
    assert doc.blocks[0].text == " ".join(lines)


def test_encrypted_pdf_is_refused(tmp_path):
    path = B.simple_pdf(tmp_path / "hemmelig.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
                        user_pw="hemmelig", owner_pw="eier")
    with pytest.raises(DocumentImportError) as exc:
        import_document(path)
    assert exc.value.code == "pdf_password"
    assert "passord" in exc.value.message


def test_copy_protection_without_accessibility_is_refused(tmp_path):
    path = B.simple_pdf(tmp_path / "sperret.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="eier",
                        user_pw="", permissions=pymupdf.PDF_PERM_PRINT)
    with pytest.raises(DocumentImportError) as exc:
        import_document(path)
    assert exc.value.code == "pdf_copy_protected"


def test_copy_protection_with_accessibility_is_allowed(tmp_path):
    path = B.simple_pdf(tmp_path / "tilgjengelig.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="eier",
                        user_pw="", permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_ACCESSIBILITY)
    doc = import_document(path)
    assert "dysleksi" in doc.plain_text()
    assert "copy_restricted" in [w.code for w in doc.warnings]


def test_meaningful_metadata_title_wins_and_placeholders_are_ignored(tmp_path):
    path = tmp_path / "x.pdf"
    d = pymupdf.open()
    B.add_lines(d.new_page(), 72, 100, B.wrap(B.NB_PARAGRAPH, 450))
    d.set_metadata({"title": "Microsoft Word - bachelor_final_v3.docx"})
    d.save(path)
    assert import_document(path, original_name="Min oppgave.pdf").title == "Min oppgave"
    d = pymupdf.open(path)
    d.set_metadata({"title": "Lesestøtte for studenter"})
    d.save(tmp_path / "y.pdf")
    assert import_document(tmp_path / "y.pdf").title == "Lesestøtte for studenter"


def test_pdf_outline_sets_heading_levels(tmp_path):
    path = tmp_path / "toc.pdf"
    d = pymupdf.open()
    page = d.new_page()
    page.insert_text((72, 80), "Hovedtittel for rapporten", fontname="hebo", fontsize=20)
    page.insert_text((72, 120), "Innledning", fontname="hebo", fontsize=12)
    y = B.add_lines(page, 72, 140, B.wrap(B.NB_PARAGRAPH, 450))
    page.insert_text((72, y + 10), "Bakgrunn for studien", fontname="hebo", fontsize=12)
    B.add_lines(page, 72, y + 30, B.wrap(B.NB_PARAGRAPH_2, 450))
    d.set_toc([[1, "Innledning", 1], [2, "Bakgrunn for studien", 1]])
    d.save(path)
    doc = import_document(path)
    levels = {b.text: b.level for b in kinds(doc, "heading")}
    assert levels["Hovedtittel for rapporten"] == 1
    assert levels["Innledning"] == 2
    assert levels["Bakgrunn for studien"] == 3


def test_long_pdf_is_fast(tmp_path):
    path = B.many_pages_pdf(tmp_path / "lang.pdf", 120)
    t0 = time.perf_counter()
    doc = import_document(path)
    elapsed = time.perf_counter() - t0
    assert doc.page_count == 120
    assert elapsed < 15, f"import took {elapsed:.1f} s"
    assert len(kinds(doc, "header")) == 120
    assert len(kinds(doc, "page_number")) == 120
    first_page = [b for b in doc.blocks if b.page == 1 and b.kind == "paragraph"]
    assert first_page[0].text.startswith("Alpha") and first_page[-1].text.startswith("Omega")


def test_damaged_pdf_gives_friendly_error(tmp_path):
    path = tmp_path / "skadet.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"\x00garbage" * 50)
    with pytest.raises(DocumentImportError) as exc:
        import_document(path)
    assert exc.value.code in ("damaged", "no_text", "ocr_unavailable")
