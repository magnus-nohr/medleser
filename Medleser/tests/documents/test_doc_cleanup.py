import pytest

from medleser.documents.cleanup import (
    detect_spans,
    hyphenated_vocabulary,
    is_caption,
    is_page_number_line,
    is_reference_heading,
    join_lines,
    normalize_text,
    normalize_with_spans,
)
from medleser.documents.model import Span


def spans_of(text):
    return [(s.kind, text[s.start:s.end]) for s in detect_spans(text)]


def test_detects_url_doi_email_with_trailing_punctuation():
    text = (
        "Se https://www.udir.no/laring-og-trivsel/. Les også (www.example.org/side) og "
        "doi:10.1016/j.compedu.2020.103844, samt https://doi.org/10.1080/00313831.2019.1234567. "
        "Kontakt kari.nordmann@hiof.no for spørsmål."
    )
    assert spans_of(text) == [
        ("url", "https://www.udir.no/laring-og-trivsel/"),
        ("url", "www.example.org/side"),
        ("doi", "doi:10.1016/j.compedu.2020.103844"),
        ("doi", "https://doi.org/10.1080/00313831.2019.1234567"),
        ("email", "kari.nordmann@hiof.no"),
    ]


def test_doi_variants():
    assert spans_of("DOI: 10.1234/abc-123") == [("doi", "DOI: 10.1234/abc-123")]
    assert spans_of("http://dx.doi.org/10.1000/xyz") == [("doi", "http://dx.doi.org/10.1000/xyz")]
    assert spans_of("bare 10.1016/S0022-4405(02)00151-X.") == [("doi", "10.1016/S0022-4405(02)00151-X")]


def test_balanced_parentheses_stay_in_url():
    text = "Se https://en.wikipedia.org/wiki/Dyslexia_(disorder)."
    assert spans_of(text) == [("url", "https://en.wikipedia.org/wiki/Dyslexia_(disorder)")]


def test_spans_are_sorted_and_do_not_overlap():
    spans = detect_spans("a https://doi.org/10.1/x b www.x.no c x@y.no")
    assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))


@pytest.mark.parametrize(
    "lines, expected",
    [
        (["bedre infor-", "masjon her"], "bedre informasjon her"),
        (["IKT-", "basert støtte"], "IKT-basert støtte"),
        (["send en e-", "post"], "send en e-post"),
        (["lese-", "og skrivevansker"], "lese- og skrivevansker"),
        (["Nord-", "Norge er stort"], "Nord-Norge er stort"),
        (["perioden 2019-", "2020 var"], "perioden 2019-2020 var"),
        (["et ord –", "og mer"], "et ord – og mer"),
        (["vanlig linje", "neste linje"], "vanlig linje neste linje"),
        (["https://doi.org/10.1016/j.compedu.2020.", "103844 er lenken"], "https://doi.org/10.1016/j.compedu.2020.103844 er lenken"),
        (["https://www.udir.no/laring-og-", "trivsel/ er nyttig"], "https://www.udir.no/laring-og-trivsel/ er nyttig"),
        (["Se https://www.udir.no.", "Neste setning."], "Se https://www.udir.no. Neste setning."),
        (["ven-", "til"], "ventil"),
    ],
)
def test_join_lines(lines, expected):
    assert join_lines(lines)[0] == expected


def test_join_lines_keeps_known_hyphenated_words_and_reports_offsets():
    vocab = hyphenated_vocabulary(["en sosio-kulturell modell", "mer tekst"])
    assert "sosio-kulturell" in vocab
    text, starts = join_lines(["en sosio-", "kulturell modell"], hyphenated_words=vocab)
    assert text == "en sosio-kulturell modell"
    text, starts = join_lines(["abc infor-", "masjon x", "slutt"])
    assert text == "abc informasjon x slutt"
    assert text[starts[1]:].startswith("masjon")
    assert text[starts[2]:] == "slutt"


@pytest.mark.parametrize(
    "text",
    ["Referanser", "REFERANSER", "7 Referanser", "7. Litteratur", "Litteraturliste:", "Kilder",
     "Kildeliste", "Bibliografi", "References", "Bibliography", "Works cited", "Literature",
     "Reference list", "VII. References", "Referanseliste"],
)
def test_reference_headings(text):
    assert is_reference_heading(text)


@pytest.mark.parametrize("text", ["Litteratur om lesing", "Innledning", "Referansegruppen møttes", ""])
def test_not_reference_headings(text):
    assert not is_reference_heading(text)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Figur 1: Oversikt over deltakerne", True),
        ("Figure 2. Mean scores", True),
        ("Fig. 3 Results", True),
        ("Tabell 4.1 Deltakernes alder", True),
        ("Table 1", True),
        ("Diagram 2 – fordeling", True),
        ("Figur 1 viser at elevene leste bedre.", False),
        ("Tabellen viser resultatene", False),
        ("Figurativ språkbruk er vanlig", False),
    ],
)
def test_captions(text, expected):
    assert is_caption(text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12", True), ("- 12 -", True), ("– 7 –", True), ("Side 3 av 10", True), ("Page 3", True),
        ("s. 4", True), ("iv", True), ("XII", True), ("3/10", True), ("[5]", True),
        ("2024 er et år", False), ("Innledning", False), ("mix", False), ("civil", False), ("Vi", False),
    ],
)
def test_page_number_lines(text, expected):
    assert is_page_number_line(text) is expected


def test_normalize_text():
    assert normalize_text("  a b\n c​ ﬁne å ") == "a b c fine å"


def test_normalize_with_spans_moves_spans():
    raw = "  Tekst  med\n fotnote1   og mer "
    start = raw.index("1")
    text, spans = normalize_with_spans(raw, [Span(start, start + 1, "footnote_ref")])
    assert text == "Tekst med fotnote1 og mer"
    assert [(s.kind, text[s.start:s.end]) for s in spans] == [("footnote_ref", "1")]
