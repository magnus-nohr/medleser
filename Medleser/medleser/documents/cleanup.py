"""Pure text helpers shared by the importers.

Everything here works on plain strings (no PDF/DOCX objects), so the same
functions can be reused later, e.g. when a user edits a block and its spans
must be recomputed.

Heuristics in short
-------------------
* ``detect_spans`` finds DOIs (``doi:10.…``, ``DOI 10.…``, ``https://doi.org/10.…``
  and bare ``10.1234/…``), URLs (``http(s)://…``, ``ftp://…``, ``www.…``) and
  e-mail addresses. Trailing sentence punctuation and unbalanced closing
  brackets are not part of a span.
* ``join_lines`` joins the lines of one paragraph with spaces, removes
  end-of-line hyphenation (``infor-`` + ``masjon`` → ``informasjon``) only when
  a lowercase letter precedes the hyphen and the next line starts in
  lowercase, and keeps the hyphen for ``IKT-``/``e-``/digits, for capitalised
  continuations (``Nord-`` + ``Norge``), for suspended compounds
  (``lese-`` + ``og skrivevansker``) and for words that occur hyphenated
  elsewhere in the document. URLs broken over two lines are rejoined.
* ``is_reference_heading``, ``is_caption`` and ``is_page_number_line`` are
  small, anchored regular expressions (see their docstrings).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Container, Sequence

from .model import Span

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)
# Zero-width characters, BOM, soft hyphen and C0/C1 control characters except \t \n.
_INVISIBLE_RE = re.compile(r"[­​‌‍⁠﻿\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Private use area: glyphs from symbol fonts (bullets, ticks …) – not readable text.
_PRIVATE_USE_RE = re.compile(r"[-]")
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Return ``text`` as one clean line.

    NFC-normalises (decomposed «å» from PDFs becomes one character),
    expands typographic ligatures, removes invisible/control characters and
    private-use glyphs and collapses all whitespace (also newlines) to single
    spaces.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text.translate(_LIGATURES))
    text = _INVISIBLE_RE.sub("", text)
    text = _PRIVATE_USE_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def _normalize_piece(seg: str) -> str:
    seg = unicodedata.normalize("NFC", seg.translate(_LIGATURES))
    seg = _INVISIBLE_RE.sub("", seg)
    seg = _PRIVATE_USE_RE.sub("", seg)
    return _WS_RE.sub(" ", seg)


def normalize_with_spans(text: str, spans: Sequence[Span]) -> tuple[str, list[Span]]:
    """Like :func:`normalize_text`, but moves ``spans`` along with the text.

    The text is normalised piecewise between span boundaries, so a span
    still covers the same characters afterwards (empty spans are dropped).
    """
    if not spans:
        return normalize_text(text), []
    cuts = sorted({0, len(text)} | {max(0, min(len(text), p)) for s in spans for p in (s.start, s.end)})
    newpos: dict[int, int] = {}
    out = ""
    for a, b in zip(cuts, cuts[1:]):
        seg = _normalize_piece(text[a:b])
        if (not out or out.endswith(" ")) and seg.startswith(" "):
            seg = seg[1:]
        newpos[a] = len(out)
        out += seg
    newpos[len(text)] = len(out)
    if out.endswith(" "):
        out = out[:-1]
    result: list[Span] = []
    for s in spans:
        start = min(newpos.get(max(0, min(len(text), s.start)), 0), len(out))
        end = min(newpos.get(max(0, min(len(text), s.end)), len(out)), len(out))
        # a span must not start or end on the collapsed space
        while start < end and out[start] == " ":
            start += 1
        while end > start and out[end - 1] == " ":
            end -= 1
        if end > start:
            result.append(Span(start, end, s.kind))
    return out, result


def normalize_repeat_key(text: str) -> str:
    """Key used to recognise repeated running headers/footers across pages.

    Lowercase, digits replaced by ``#`` (page numbers differ per page),
    whitespace collapsed.
    """
    text = normalize_text(text).lower()
    text = re.sub(r"\d+", "#", text)
    return text.strip(" .,:;|-–—")


def text_quality(text: str) -> float:
    """Share (0..1) of characters that look like real text.

    Broken PDF font encodings produce U+FFFD, private-use or control
    characters; such a text layer is treated like a missing one.
    """
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    bad = 0
    for c in chars:
        o = ord(c)
        if c == "�" or 0xE000 <= o <= 0xF8FF or o < 32 or unicodedata.category(c) == "Co":
            bad += 1
    return 1.0 - bad / len(chars)


# ---------------------------------------------------------------------------
# Spans (url / doi / email)
# ---------------------------------------------------------------------------

_DOI_BODY = r"10\.\d{4,9}/[^\s<>\"'«»“”]+"
_DOI_URL_RE = re.compile(r"(?i)\b(?:https?://)?(?:dx\.)?doi\.org/" + _DOI_BODY)
_DOI_PREFIX_RE = re.compile(r"(?i)\bdoi\s*:?\s*" + _DOI_BODY)
_DOI_BARE_RE = re.compile(r"(?<![\w/.])" + _DOI_BODY)
_URL_RE = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)[^\s<>\"'«»“”]+")
_EMAIL_RE = re.compile(
    r"(?i)(?<![\w.+-])[a-z0-9][a-z0-9._%+-]*@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,24}(?![\w-])"
)
_TRAILING_PUNCT = ".,;:!?'\"»”’>"
_BRACKETS = {")": "(", "]": "[", "}": "{"}


def _trim_match(text: str, start: int, end: int) -> int:
    """Return a new end so that trailing punctuation/unbalanced brackets are excluded."""
    while end > start:
        ch = text[end - 1]
        if ch in _TRAILING_PUNCT:
            end -= 1
            continue
        if ch in _BRACKETS:
            frag = text[start:end]
            if frag.count(ch) > frag.count(_BRACKETS[ch]):
                end -= 1
                continue
        break
    return end


def detect_spans(text: str) -> list[Span]:
    """Find URL, DOI and e-mail spans in ``text`` (sorted, non-overlapping).

    Links to ``doi.org`` are classified as ``"doi"``, not ``"url"``.
    """
    if not text:
        return []
    found: list[Span] = []

    def overlaps(s: int, e: int) -> bool:
        return any(s < sp.end and sp.start < e for sp in found)

    def add(regex: re.Pattern[str], kind: str) -> None:
        for m in regex.finditer(text):
            s, e = m.start(), _trim_match(text, m.start(), m.end())
            if e - s < 4 or overlaps(s, e):
                continue
            k = kind
            if kind == "url" and "doi.org/10." in text[s:e].lower():
                k = "doi"
            found.append(Span(s, e, k))

    add(_DOI_URL_RE, "doi")
    add(_DOI_PREFIX_RE, "doi")
    add(_URL_RE, "url")
    add(_DOI_BARE_RE, "doi")
    add(_EMAIL_RE, "email")
    found.sort(key=lambda sp: (sp.start, sp.end))
    return found


def merge_spans(*groups: Sequence[Span]) -> list[Span]:
    """Merge span lists; on overlap the span from the earlier group wins."""
    out: list[Span] = []
    for group in groups:
        for sp in group:
            if sp.end <= sp.start:
                continue
            if any(sp.start < o.end and o.start < sp.end for o in out):
                continue
            out.append(Span(sp.start, sp.end, sp.kind))
    out.sort(key=lambda sp: (sp.start, sp.end))
    return out


# ---------------------------------------------------------------------------
# Line joining / hyphenation
# ---------------------------------------------------------------------------

HYPHENS = "-­‐‑"
# Next word after a "suspended" hyphen: «lese- og skrivevansker», «barne- eller …».
_SUSPENDED_CONJ = {"og", "eller", "samt", "and", "or", "und", "oder", "och", "el", "resp"}
_WORD_BEFORE_HYPHEN_RE = re.compile(r"([^\W\d_]+(?:[-‐‑][^\W\d_]+)*)[-­‐]$")
_FIRST_WORD_RE = re.compile(r"^([^\W\d_]+)")
_URLISH_RE = re.compile(r"(?i)(?:https?://|ftp://|www\.|doi:|doi\.org/|\b10\.\d{4,9}/)\S*$")
_URL_CONT_END = "/-._=?&#%~:"
_CAP_WORD_RE = re.compile(r"^[A-ZÆØÅÄÖÜ][a-zæøåäöüéèáàóòúùíì]+[.,;:!?]?$")


def _hyphen_decision(prev: str, nxt: str, hyphenated_words: Container[str]) -> str | None:
    """Decide how to join ``prev`` (ending in a hyphen) and ``nxt``.

    Returns ``"remove"`` (drop hyphen, no space), ``"keep"`` (keep hyphen, no
    space), ``"keep_space"`` (keep hyphen, add space) or ``None`` when the
    hyphen rules do not apply.
    """
    if not prev or prev[-1] not in HYPHENS or len(prev) < 2:
        return None
    before = prev[-2]
    if not before.isalnum():
        return None  # « -» used as a dash
    if prev[-1] == "­":
        return "remove"  # explicit soft hyphen = discretionary hyphenation
    if prev[-1] == "‑":
        return "keep"  # non-breaking hyphen is always part of the word
    first = nxt[:1]
    if not first:
        return None
    m_word = _FIRST_WORD_RE.match(nxt)
    next_word = m_word.group(1) if m_word else ""
    if next_word.lower() in _SUSPENDED_CONJ:
        return "keep_space"
    if not first.isalnum():
        return None
    if before.isdigit() or first.isdigit():
        return "keep"
    if not before.islower() or not first.islower():
        return "keep"  # «IKT-» + «basert», «Nord-» + «Norge»
    m = _WORD_BEFORE_HYPHEN_RE.search(prev)
    head = m.group(1) if m else ""
    if len(head.split("-")[-1]) <= 1:
        return "keep"  # «e-» + «post», «x-» + «aksen»
    if f"{head}-{next_word}".lower() in hyphenated_words:
        return "keep"
    return "remove"


def _url_continues(prev: str, nxt: str) -> bool:
    """True when ``prev`` ends inside a URL/DOI that continues on the next line."""
    last = prev.rsplit(" ", 1)[-1]
    if not _URLISH_RE.search(last):
        return False
    first = nxt.split(" ", 1)[0]
    if not first or _CAP_WORD_RE.match(first):
        return False
    if last[-1] in _URL_CONT_END:
        return first[0].isalnum() or first[0] in "/._-#?=&%~"
    # URL that does not end in a separator: only join obvious URL fragments
    return bool(re.match(r"^[\w.\-/#?=&%~]*[/=?&#][\w.\-/#?=&%~]*$", first)) and not first[0].isupper()


def join_lines(
    lines: Sequence[str],
    *,
    hyphenated_words: Container[str] = frozenset(),
) -> tuple[str, list[int]]:
    """Join the lines of one paragraph.

    Returns ``(text, starts)`` where ``starts[i]`` is the offset in ``text``
    where (the stripped) ``lines[i]`` begins – used to move character spans
    (e.g. footnote markers) from lines into the joined paragraph.
    ``hyphenated_words`` holds lowercase words that occur with a hyphen
    elsewhere in the document (e.g. ``"sosio-kulturell"``); for those the
    hyphen is kept when a line ends after the hyphen.
    """
    out = ""
    starts: list[int] = []
    for raw in lines:
        line = raw.strip()
        if not out:
            starts.append(len(out))
            out = line
            continue
        if not line:
            starts.append(len(out))
            continue
        if _url_continues(out, line):
            starts.append(len(out))
            out += line
            continue
        decision = _hyphen_decision(out, line, hyphenated_words)
        if decision == "remove":
            out = out[:-1]
            starts.append(len(out))
            out += line
        elif decision == "keep":
            if out[-1] == "­":
                out = out[:-1] + "-"
            starts.append(len(out))
            out += line
        elif decision == "keep_space":
            starts.append(len(out) + 1)
            out += " " + line
        else:
            starts.append(len(out) + 1)
            out += " " + line
    return out, starts


_HYPHENATED_WORD_RE = re.compile(r"\b[^\W\d_]+(?:-[^\W\d_]+)+\b")


def hyphenated_vocabulary(texts: Sequence[str]) -> set[str]:
    """Lowercase hyphenated words occurring *inside* lines (not at a line end)."""
    vocab: set[str] = set()
    for t in texts:
        if "-" not in t:
            continue
        stripped = t.rstrip()
        for m in _HYPHENATED_WORD_RE.finditer(stripped):
            if stripped[m.end():] in HYPHENS and m.end() < len(stripped):
                continue  # «sosio-kultu-» at a line end is not a complete word
            vocab.add(m.group(0).lower())
    return vocab


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

_REFERENCE_HEADINGS = {
    "referanser",
    "referanseliste",
    "referansar",
    "litteratur",
    "litteraturliste",
    "litteraturhenvisninger",
    "litteraturtilvisingar",
    "kilder",
    "kildeliste",
    "kildehenvisninger",
    "kjelder",
    "kjeldeliste",
    "bibliografi",
    "litteratur og kilder",
    "kilder og litteratur",
    "referanser og litteratur",
    "litteratur og referanser",
    "references",
    "reference list",
    "list of references",
    "references and notes",
    "bibliography",
    "works cited",
    "literature",
    "literature cited",
    "cited literature",
    "referenser",
    "källor",
    "källförteckning",
    "litteraturförteckning",
    "litteraturliste og kilder",
    "literaturverzeichnis",
    "literatur",
    "quellen",
    "bibliographie",
    "références",
    "références bibliographiques",
    "referencias",
    "bibliografía",
}
_HEADING_NUMBER_RE = re.compile(r"^(?:(?:kapittel|chapter|del|part)\s+)?(?:\d+(?:\.\d+)*\.?|[ivxlc]+\.|[a-z]\.|[a-z]\))\s+", re.IGNORECASE)


def is_reference_heading(text: str) -> bool:
    """True for headings that start a reference list.

    Matches (case-insensitive, optional numbering such as «7 Referanser» or
    «VII. References», optional trailing colon): Referanser, Referanseliste,
    Litteratur, Litteraturliste, Kilder, Kildeliste, Bibliografi, References,
    Bibliography, Works cited, Literature, Reference list and a few Nordic/
    European variants. The whole heading must match – «Litteratur om
    lesing» is not a reference heading.
    """
    t = normalize_text(text)
    if not t or len(t) > 60:
        return False
    t = _HEADING_NUMBER_RE.sub("", t)
    t = t.strip(" .:;–—-").lower()
    t = re.sub(r"\s+", " ", t)
    return t in _REFERENCE_HEADINGS


_CAPTION_RE = re.compile(
    r"^(?:figur|figure|fig\.?|tabell|tabeller|table|tab\.|diagram|illustrasjon|bilde|graf|kart|"
    r"abbildung|abb\.|tabelle|bild|tabla|figura|tableau|chart|exhibit)"
    r"\s*(?:\d+(?:[.\-–]\d+)*[a-z]?|[IVXL]+)(?![\w])\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def is_caption(text: str) -> bool:
    """True for figure/table captions: «Figur 1: …», «Table 2. …», «Fig. 3 …».

    A label (Figur/Figure/Fig./Tabell/Table/Diagram/…) and a number must be
    followed by end of text, a separator (``: . – - | )``) or a capitalised
    word («Tabell 1 Deltakernes alder»). «Figur 1 viser at …» (lowercase
    continuation) is body text, not a caption.
    """
    t = normalize_text(text)
    m = _CAPTION_RE.match(t)
    if not m:
        return False
    rest = m.group("rest")
    if not rest:
        return True
    if rest[0] in ":.–—-|)":
        return True
    return rest[0].isupper() and len(t.split()) <= 60


_ROMAN_RE = re.compile(r"^m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_PAGE_PATTERNS = [
    re.compile(r"^\d{1,4}$"),
    re.compile(r"^[-–—]\s*\d{1,4}\s*[-–—]$"),
    re.compile(r"^[\[(|]\s*\d{1,4}\s*[\])|]$"),
    re.compile(
        r"^(?:side|sida|sidan|s\.|s|page|pages|pg\.|p\.|seite|página|pagina)\s*\d{1,4}"
        r"(?:\s*(?:av|of|/|von|de|af|di)\s*\d{1,4})?$",
        re.IGNORECASE,
    ),
    re.compile(r"^\d{1,4}\s*(?:/|av|of|von|af)\s*\d{1,4}$", re.IGNORECASE),
]


def _roman_value(s: str) -> int:
    total = 0
    prev = 0
    for ch in reversed(s):
        v = _ROMAN_VALUES[ch]
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


def is_page_number_line(text: str) -> bool:
    """True for lines that are only a page number.

    «12», «- 12 -», «[12]», «Side 3 av 10», «Page 3», «s. 4», «3/10» and
    roman numerals up to 100 in one consistent case («iv», «XII»).
    """
    t = normalize_text(text)
    if not t or len(t) > 24:
        return False
    for pat in _PAGE_PATTERNS:
        if pat.match(t):
            return True
    if t.isalpha() and (t.islower() or t.isupper()) and len(t) <= 7:
        low = t.lower()
        if _ROMAN_RE.match(low) and 0 < _roman_value(low) <= 100:
            return True
    return False


_BULLET_RE = re.compile(
    r"^\s*([•◦▪▫●○■□►▶▸‣⁃∙·➢➤➔→✓✔✗◆◇❖\-–—*])\s+(?=\S)"
)
_ENUM_RE = re.compile(r"^\s*(?:\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?(?:i{1,3}|iv|vi{0,3}|ix|x)[.)])\s+(?=\S)")


def bullet_prefix_length(text: str) -> int:
    """Length of a leading bullet symbol plus spaces («• », «– »), else 0."""
    m = _BULLET_RE.match(text)
    return m.end() if m else 0


def starts_with_enumeration(text: str) -> bool:
    """True for «1. », «2) », «(a) », «b. », «iv) » at the start of ``text``."""
    return bool(_ENUM_RE.match(text))


def ends_sentence(text: str) -> bool:
    """True when ``text`` ends with sentence-final punctuation (also before quotes/brackets)."""
    t = text.rstrip().rstrip("\"'»”’)]")
    return bool(t) and t[-1] in ".!?:;…"
