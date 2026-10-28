from medleser.documents.model import Block, Document, Span, TableData
from medleser.speech.text_prep import ReadingOptions, build_plan, decide_block, sentence_spans


def make_doc(blocks):
    return Document(title="Test", source_name="t.pdf", source_type="pdf", size_bytes=1, blocks=blocks,
                    language="nb")


def words_of(segment):
    from medleser.speech.text_prep import word_ranges

    return [(segment.text[s:e], segment.map_range(s, e)) for s, e in word_ranges(segment.text)]


def test_plain_paragraph_maps_one_to_one():
    text = "Dette er en enkel setning. Her er en til."
    doc = make_doc([Block("b00000", "paragraph", text)])
    plan = build_plan(doc, ReadingOptions(), "nb")
    assert len(plan.segments) == 1
    for word, (block_id, part, ds, de) in words_of(plan.segments[0]):
        assert block_id == "b00000" and part == "t"
        assert text[ds:de] == word


def test_url_doi_and_footnote_marker_are_skipped_but_mapping_stays_exact():
    text = "Se https://example.com/side for mer2 informasjon (doi:10.1000/xyz123) i dag."
    url = text.index("https://")
    doi = text.index("doi:")
    marker = text.index("mer2") + 3
    spans = [Span(url, url + len("https://example.com/side"), "url"),
             Span(doi, doi + len("doi:10.1000/xyz123"), "doi"),
             Span(marker, marker + 1, "footnote_ref")]
    doc = make_doc([Block("b00000", "paragraph", text, spans=spans)])
    plan = build_plan(doc, ReadingOptions(), "nb")
    reading = plan.segments[0].text
    assert "https" not in reading and "doi" not in reading and "mer2" not in reading
    assert "mer informasjon" in reading
    for word, (block_id, part, ds, de) in words_of(plan.segments[0]):
        assert text[ds:de] == word


def test_abbreviation_expansion_maps_to_original_span():
    text = "Mange bruker f.eks. lydbøker, dvs. tekst som leses opp, se s. 12."
    doc = make_doc([Block("b00000", "paragraph", text)])
    plan = build_plan(doc, ReadingOptions(), "nb")
    reading = plan.segments[0].text
    assert "for eksempel" in reading and "det vil si" in reading and "side 12" in reading
    mapped = dict((w, m) for w, m in words_of(plan.segments[0]))
    _, _, ds, de = mapped["eksempel"]
    assert text[ds:de] == "f.eks."
    _, _, ds, de = mapped["side"]
    assert text[ds:de] == "s."


def test_expansion_can_be_disabled():
    doc = make_doc([Block("b00000", "paragraph", "Mange bruker f.eks. lydbøker.")])
    plan = build_plan(doc, ReadingOptions(expand_abbreviations=False), "nb")
    assert "f.eks." in plan.segments[0].text


def test_default_skips_headers_page_numbers_references_and_footnotes():
    blocks = [
        Block("b00000", "header", "Tidsskrift for test 2026"),
        Block("b00001", "heading", "Innledning", level=1),
        Block("b00002", "paragraph", "Brødtekst som skal leses."),
        Block("b00003", "page_number", "12"),
        Block("b00004", "footnote", "1 En fotnote."),
        Block("b00005", "heading", "Referanser", level=1, section="references"),
        Block("b00006", "reference", "Hansen, K. (2020). En bok.", section="references"),
    ]
    plan = build_plan(make_doc(blocks), ReadingOptions(), "nb")
    read_blocks = {s.block_id for s in plan.segments}
    assert read_blocks == {"b00001", "b00002"}
    assert plan.decisions["b00006"].reason == "Referanseliste"

    plan2 = build_plan(make_doc(blocks), ReadingOptions(references="read", footnotes="read"), "nb")
    assert {"b00004", "b00005", "b00006"} <= {s.block_id for s in plan2.segments}


def test_user_override_excludes_block():
    blocks = [Block("b00000", "paragraph", "Første."), Block("b00001", "paragraph", "Andre.")]
    opts = ReadingOptions(block_overrides={"b00001": False})
    plan = build_plan(make_doc(blocks), opts, "nb")
    assert [s.block_id for s in plan.segments] == ["b00000"]
    assert plan.decisions["b00001"].reason == "Ditt valg"


def test_table_modes_and_row_reading_maps_cells():
    table = TableData(header=["Navn", "Alder"], rows=[["Kari", "23"], ["Ola", "31"]])
    block = Block("b00000", "table", "Navn | Alder\nKari | 23\nOla | 31", table=table)
    assert decide_block(block, ReadingOptions()).table_mode == "show"
    plan = build_plan(make_doc([block]), ReadingOptions(tables="read"), "nb")
    text = " ".join(s.text for s in plan.segments)
    assert "Rad 1." in text and "Navn: Kari." in text and "Alder: 31." in text
    parts = {m[1] for s in plan.segments for _, m in words_of(s) if m}
    assert {"r0", "h0", "r0c0", "h1", "r1c1"} <= parts
    plan_skip = build_plan(make_doc([block]), ReadingOptions(tables="skip"), "nb")
    assert plan_skip.segments == []


def test_long_paragraph_is_split_at_sentences_within_limit():
    sentence = "Dette er en setning som er ganske lang og handler om lesing. "
    text = (sentence * 30).strip()
    plan = build_plan(make_doc([Block("b00000", "paragraph", text)]), ReadingOptions(), "nb", max_chars=200)
    assert len(plan.segments) > 5
    assert all(len(s.text) <= 200 for s in plan.segments)
    # Every word in every segment maps back to the right place.
    for seg in plan.segments:
        for word, (_, _, ds, de) in words_of(seg):
            assert text[ds:de] == word


def test_sentence_splitter_respects_abbreviations_and_initials():
    text = "Studien av K. Hansen viste mye. Se kap. 3 for detaljer. Neste setning!"
    spans = [text[s:e].strip() for s, e in sentence_spans(text)]
    assert spans == ["Studien av K. Hansen viste mye.", "Se kap. 3 for detaljer.", "Neste setning!"]


def test_chapters_from_headings():
    blocks = [
        Block("b00000", "heading", "Tittel", level=1),
        Block("b00001", "heading", "Kapittel 1", level=2),
        Block("b00002", "paragraph", "Tekst en."),
        Block("b00003", "heading", "Kapittel 2", level=2),
        Block("b00004", "paragraph", "Tekst to."),
    ]
    plan = build_plan(make_doc(blocks), ReadingOptions(), "nb")
    assert [c["title"] for c in plan.chapters] == ["Kapittel 1", "Kapittel 2"]
