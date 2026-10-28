"""Build the traceable reading text from a document.

The original document (``Document.blocks``) is never modified here. We
derive a separate *reading representation*: for every block that should be
read, a reading text plus a list of :class:`Piece` objects that map each
reading-text range back to the exact characters on screen (block, part,
display offsets). Skipped spans (URL's, DOI's, footnote markers) and
expanded abbreviations keep a correct mapping, so word timestamps from the
TTS engine can be turned into highlights of the visible text.

Display "parts": a normal block has one part, ``"t"`` (``Block.text``).
A table has ``"h{c}"`` (header cell c), ``"r{r}c{c}"`` (body cell) and
``"r{r}"`` (whole row, used for spoken row labels).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from ..documents.model import Block, Document

# --------------------------------------------------------------------------
# Options


@dataclass
class ReadingOptions:
    skip_urls: bool = True
    skip_doi: bool = True
    skip_emails: bool = False
    skip_headers_footers: bool = True
    skip_page_numbers: bool = True
    skip_footnote_markers: bool = True
    references: str = "skip"  # "read" | "skip"
    footnotes: str = "skip"  # "read" | "skip"
    tables: str = "show"  # "read" (row by row) | "skip" | "show" (on screen, not read)
    captions: bool = True
    figure_text: bool = False
    expand_abbreviations: bool = True
    block_overrides: dict[str, bool] = field(default_factory=dict)  # block id -> read?
    table_overrides: dict[str, str] = field(default_factory=dict)  # block id -> read|skip|show

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict[str, Any]]) -> "ReadingOptions":
        opts = ReadingOptions()
        if not d:
            return opts
        for key, value in d.items():
            if not hasattr(opts, key):
                continue
            default = getattr(opts, key)
            if isinstance(default, bool):
                setattr(opts, key, bool(value))
            elif isinstance(default, dict):
                setattr(opts, key, dict(value or {}))
            elif isinstance(default, str):
                setattr(opts, key, str(value))
        if opts.references not in ("read", "skip"):
            opts.references = "skip"
        if opts.footnotes not in ("read", "skip"):
            opts.footnotes = "skip"
        if opts.tables not in ("read", "skip", "show"):
            opts.tables = "show"
        opts.table_overrides = {
            k: v for k, v in opts.table_overrides.items() if v in ("read", "skip", "show")
        }
        opts.block_overrides = {k: bool(v) for k, v in opts.block_overrides.items()}
        return opts


# --------------------------------------------------------------------------
# Inclusion decisions (also shown to the user)

_KIND_REASONS = {
    "header": "Topptekst",
    "footer": "Bunntekst",
    "page_number": "Sidetall",
    "footnote": "Fotnote",
    "reference": "Referanseliste",
    "figure_text": "Tekst i figur",
}


@dataclass
class BlockDecision:
    include: bool
    reason: Optional[str] = None  # Norwegian, why it is (not) read
    table_mode: Optional[str] = None  # for tables: read | skip | show
    skipped_spans: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_block(block: Block, options: ReadingOptions) -> BlockDecision:
    skipped = [
        s.to_dict()
        for s in block.spans
        if (s.kind == "url" and options.skip_urls)
        or (s.kind == "doi" and options.skip_doi)
        or (s.kind == "email" and options.skip_emails)
        or (s.kind == "footnote_ref" and options.skip_footnote_markers)
    ]
    if block.kind == "table":
        mode = options.table_overrides.get(block.id, options.tables)
        override = options.block_overrides.get(block.id)
        if override is False:
            mode = "show" if mode == "read" else mode
        elif override is True and mode != "read":
            mode = "read"
        reason = {
            "read": "Tabellen leses rad for rad",
            "skip": "Tabellen hoppes over",
            "show": "Tabellen vises, men leses ikke",
        }[mode]
        return BlockDecision(include=mode == "read", reason=reason, table_mode=mode)

    include = True
    reason: Optional[str] = None
    if block.kind in ("header", "footer"):
        include = not options.skip_headers_footers
        reason = _KIND_REASONS[block.kind]
    elif block.kind == "page_number":
        include = not options.skip_page_numbers
        reason = "Sidetall"
    elif block.section == "references" or block.kind == "reference":
        include = options.references == "read"
        reason = "Referanseliste"
    elif block.kind == "footnote":
        include = options.footnotes == "read"
        reason = "Fotnote"
    elif block.kind == "caption":
        include = options.captions
        reason = "Bildetekst"
    elif block.kind == "figure_text":
        include = options.figure_text
        reason = "Tekst i figur"

    override = options.block_overrides.get(block.id)
    if override is not None:
        include = override
        reason = "Ditt valg"
    if include and not block.text.strip():
        include = False
        reason = "Tom"
    return BlockDecision(include=include, reason=None if include and reason != "Ditt valg" else reason,
                         skipped_spans=skipped)


# --------------------------------------------------------------------------
# Pieces and builder


@dataclass
class Piece:
    rstart: int
    rend: int
    block_id: str
    part: str
    dstart: int
    dend: int
    exact: bool  # True: char-by-char copy; False: replacement/generated

    def shifted(self, delta: int) -> "Piece":
        return Piece(self.rstart + delta, self.rend + delta, self.block_id, self.part,
                     self.dstart, self.dend, self.exact)


class _Builder:
    def __init__(self) -> None:
        self.chars: list[str] = []
        self.pieces: list[Piece] = []

    @property
    def length(self) -> int:
        return len(self.chars)

    def copy(self, text: str, block_id: str, part: str, dstart: int) -> None:
        if not text:
            return
        start = self.length
        self.chars.extend(text)
        self.pieces.append(Piece(start, self.length, block_id, part, dstart, dstart + len(text), True))

    def replace(self, spoken: str, block_id: str, part: str, dstart: int, dend: int) -> None:
        if not spoken:
            return
        start = self.length
        self.chars.extend(spoken)
        self.pieces.append(Piece(start, self.length, block_id, part, dstart, dend, False))

    def separator(self, text: str = " ") -> None:
        if self.chars and not self.chars[-1].isspace():
            self.chars.extend(text)

    @property
    def text(self) -> str:
        return "".join(self.chars)


# --------------------------------------------------------------------------
# Abbreviations (spoken forms). Only unambiguous, common academic ones.

_ABBREVIATIONS: dict[str, dict[str, str]] = {
    "nb": {
        "f.eks.": "for eksempel",
        "bl.a.": "blant annet",
        "dvs.": "det vil si",
        "osv.": "og så videre",
        "m.m.": "med mer",
        "mv.": "med videre",
        "m.fl.": "med flere",
        "o.l.": "og lignende",
        "e.l.": "eller lignende",
        "jf.": "jamfør",
        "pga.": "på grunn av",
        "mht.": "med hensyn til",
        "iht.": "i henhold til",
        "evt.": "eventuelt",
        "ev.": "eventuelt",
        "ca.": "cirka",
        "t.o.m.": "til og med",
        "f.o.m.": "fra og med",
        "bl.annet": "blant annet",
        "mill.": "millioner",
        "mrd.": "milliarder",
        "vs.": "versus",
        "inkl.": "inkludert",
        "ekskl.": "eksklusive",
        "kap.": "kapittel",
        "avd.": "avdeling",
    },
    "nn": {
        "t.d.": "til dømes",
        "m.a.": "mellom anna",
        "dvs.": "det vil seie",
        "osb.": "og så bortetter",
        "o.l.": "og liknande",
        "m.m.": "med meir",
        "jf.": "jamfør",
        "pga.": "på grunn av",
        "ca.": "cirka",
        "evt.": "eventuelt",
        "ev.": "eventuelt",
        "kap.": "kapittel",
    },
    "en": {
        "e.g.": "for example",
        "i.e.": "that is",
        "etc.": "et cetera",
        "approx.": "approximately",
        "vs.": "versus",
        "cf.": "compare",
        "incl.": "including",
    },
}
# Abbreviations that only expand before a number: "s. 12", "nr. 3", "p. 4".
_NUMBERED: dict[str, dict[str, str]] = {
    "nb": {"s.": "side", "nr.": "nummer", "kap.": "kapittel", "fig.": "figur", "tab.": "tabell"},
    "nn": {"s.": "side", "nr.": "nummer", "kap.": "kapittel", "fig.": "figur"},
    "en": {"p.": "page", "pp.": "pages", "no.": "number", "fig.": "figure", "ch.": "chapter", "vol.": "volume"},
}
_SYMBOLS: dict[str, dict[str, str]] = {
    "nb": {"§": "paragraf", "&": "og"},
    "nn": {"§": "paragraf", "&": "og"},
    "en": {"§": "section", "&": "and"},
}


def _lang_family(language: str) -> str:
    base = (language or "nb").split("-")[0].lower()
    if base in ("nb", "no", "und", ""):
        return "nb"
    return base


def _compile_patterns(language: str):
    fam = _lang_family(language)
    abbr = _ABBREVIATIONS.get(fam, {})
    numbered = _NUMBERED.get(fam, {})
    symbols = _SYMBOLS.get(fam, {})
    alternatives = []
    for key in sorted(abbr, key=len, reverse=True):
        alternatives.append((re.escape(key), abbr[key], False))
    for key in sorted(numbered, key=len, reverse=True):
        alternatives.append((re.escape(key), numbered[key], True))
    compiled = []
    for pattern, spoken, needs_number in alternatives:
        # Case-insensitive match on word start; for numbered forms require a following number.
        tail = r"(?=\s*\d)" if needs_number else r"(?=\s|$|[,;:)\]])"
        compiled.append((re.compile(r"(?<![\w.])" + pattern + tail, re.IGNORECASE), spoken))
    for sym, spoken in symbols.items():
        compiled.append((re.compile(r"(?<=\s)" + re.escape(sym) + r"(?=\s)"), spoken))
    return compiled


_PATTERN_CACHE: dict[str, list] = {}


def _replacements(text: str, language: str) -> list[tuple[int, int, str]]:
    fam = _lang_family(language)
    if fam not in _PATTERN_CACHE:
        _PATTERN_CACHE[fam] = _compile_patterns(fam)
    found: list[tuple[int, int, str]] = []
    taken = [False] * (len(text) + 1)
    for regex, spoken in _PATTERN_CACHE[fam]:
        for m in regex.finditer(text):
            if any(taken[m.start():m.end()]):
                continue
            original = m.group(0)
            out = spoken
            if original[:1].isupper() and spoken[:1].islower():
                out = spoken[0].upper() + spoken[1:]
            found.append((m.start(), m.end(), out))
            for i in range(m.start(), m.end()):
                taken[i] = True
    found.sort()
    return found


def _emit_text(builder: _Builder, text: str, block_id: str, part: str, base: int,
               skip: list[tuple[int, int]], language: str, expand: bool) -> None:
    """Append ``text`` (display offsets start at ``base``) honouring skips/expansions."""
    skip = sorted(skip)
    reps = _replacements(text, language) if expand else []
    events: list[tuple[int, int, Optional[str]]] = [(s, e, None) for s, e in skip]
    for s, e, spoken in reps:
        if any(s < se and ss < e for ss, se in skip):
            continue
        events.append((s, e, spoken))
    events.sort()
    pos = 0
    for s, e, spoken in events:
        if s < pos:
            continue
        if s > pos:
            _copy_collapsed(builder, text[pos:s], block_id, part, base + pos)
        if spoken is None:
            builder.separator()
        else:
            builder.replace(spoken, block_id, part, base + s, base + e)
        pos = e
    if pos < len(text):
        _copy_collapsed(builder, text[pos:], block_id, part, base + pos)


_WS = re.compile(r"\s+")


def _copy_collapsed(builder: _Builder, text: str, block_id: str, part: str, dbase: int) -> None:
    """Copy text but collapse whitespace runs (keeps exact char mapping)."""
    pos = 0
    for m in _WS.finditer(text):
        if m.start() > pos:
            builder.copy(text[pos:m.start()], block_id, part, dbase + pos)
        if builder.chars and not builder.chars[-1].isspace():
            builder.copy(" ", block_id, part, dbase + m.start())
        pos = m.end()
    if pos < len(text):
        builder.copy(text[pos:], block_id, part, dbase + pos)


def _block_skips(block: Block, decision: BlockDecision) -> list[tuple[int, int]]:
    return [(int(s["start"]), int(s["end"])) for s in decision.skipped_spans]


def _table_reading(builder: _Builder, block: Block, language: str, expand: bool) -> None:
    table = block.table
    if table is None:
        return
    fam = _lang_family(language)
    row_word = "Row" if fam == "en" else "Rad"
    header = table.header or []
    for r, row in enumerate(table.rows):
        builder.replace(f"{row_word} {r + 1}.", block.id, f"r{r}", 0, 0)
        for c, cell in enumerate(row):
            cell_text = cell.strip()
            if not cell_text:
                continue
            builder.separator()
            if c < len(header) and header[c].strip():
                _emit_text(builder, header[c].strip(), block.id, f"h{c}", 0, [], language, expand)
                builder.replace(":", block.id, f"h{c}", len(header[c].strip()), len(header[c].strip()))
                builder.separator()
            _emit_text(builder, cell_text, block.id, f"r{r}c{c}", 0, [], language, expand)
            if not cell_text.endswith((".", "!", "?")):
                builder.replace(".", block.id, f"r{r}c{c}", len(cell_text), len(cell_text))
        builder.separator()


# --------------------------------------------------------------------------
# Sentence splitting

_SENT_END = re.compile(r"[.!?…]+[»”\"')\]]*\s+(?=[«“\"'(\[]?[A-ZÆØÅÄÖÜÉ0-9])")
_NO_SPLIT_BEFORE = re.compile(
    r"(?:\b[A-ZÆØÅ]|\b(?:dr|prof|mr|mrs|ms|st|nr|no|vol|fig|tab|kap|ca|jf|bl|f|s|p|pp|et al|ed|eds))\.\s+$",
    re.IGNORECASE,
)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for m in _SENT_END.finditer(text):
        end = m.end()
        prefix = text[max(start, m.start() - 12): m.end()]
        if _NO_SPLIT_BEFORE.search(prefix):
            continue
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _split_long(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """Split [start, end) of text into chunks <= limit at commas/spaces."""
    out = []
    while end - start > limit:
        window = text[start:start + limit]
        cut = max(window.rfind(", "), window.rfind("; "), window.rfind(" – "), window.rfind(": "))
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit - 1
        out.append((start, start + cut + 1))
        start = start + cut + 1
    out.append((start, end))
    return out


# --------------------------------------------------------------------------
# Plan


@dataclass
class Segment:
    index: int
    text: str
    pieces: list[Piece]
    block_id: str
    pause_after: float
    chapter: Optional[int] = None

    def map_range(self, start: int, end: int) -> Optional[tuple[str, str, int, int]]:
        """Map a segment-text range to (block_id, part, display_start, display_end)."""
        hits = [p for p in self.pieces if p.rstart < end and start < p.rend]
        if not hits:
            return None
        first, last = hits[0], hits[-1]
        if first.exact:
            ds = first.dstart + max(0, start - first.rstart)
        else:
            ds = first.dstart
        if last.exact:
            de = last.dstart + min(last.rend, end) - last.rstart
        else:
            de = last.dend
        if last.part != first.part or last.block_id != first.block_id:
            # Word crosses a part boundary (should not happen) – use the first part only.
            de = first.dend if not first.exact else first.dstart + (first.rend - first.rstart)
        return first.block_id, first.part, ds, max(ds, de)


@dataclass
class ReadingPlan:
    language: str
    segments: list[Segment]
    chapters: list[dict[str, Any]]  # {"title", "block_id", "segment"}
    decisions: dict[str, BlockDecision]
    total_chars: int

    def to_summary(self) -> dict[str, Any]:
        skipped_blocks = sum(1 for d in self.decisions.values() if not d.include)
        return {
            "segments": len(self.segments),
            "characters": self.total_chars,
            "chapters": [{"title": c["title"], "block_id": c["block_id"]} for c in self.chapters],
            "skipped_blocks": skipped_blocks,
        }


def _pause_after(block: Block, next_block: Optional[Block], last_in_block: bool) -> float:
    if not last_in_block:
        return 0.25
    if next_block is not None and next_block.kind == "heading":
        return 1.0
    if block.kind == "heading":
        return 0.7
    if block.kind in ("list_item", "reference"):
        return 0.35
    if block.kind == "table":
        return 0.6
    return 0.55


def build_plan(document: Document, options: ReadingOptions, language: str,
               max_chars: int = 700, hard_max: int = 3000) -> ReadingPlan:
    max_chars = max(80, min(max_chars, hard_max))
    decisions = {b.id: decide_block(b, options) for b in document.blocks}
    included = [b for b in document.blocks if decisions[b.id].include]

    chapter_heads = {c["block_id"]: i for i, c in enumerate(document.chapters())}
    segments: list[Segment] = []
    chapters: list[dict[str, Any]] = []
    current_chapter: Optional[int] = None
    total_chars = 0

    for bi, block in enumerate(included):
        next_block = included[bi + 1] if bi + 1 < len(included) else None
        builder = _Builder()
        if block.kind == "table":
            _table_reading(builder, block, language, options.expand_abbreviations)
        else:
            _emit_text(builder, block.text, block.id, "t", 0, _block_skips(block, decisions[block.id]),
                       language, options.expand_abbreviations)
        text = builder.text
        # Trim surrounding whitespace while keeping offsets consistent.
        lead = len(text) - len(text.lstrip())
        text_stripped = text.strip()
        if not text_stripped:
            continue
        pieces = [p.shifted(-lead) for p in builder.pieces]
        text = text_stripped

        if block.id in chapter_heads:
            current_chapter = len(chapters)
            chapters.append({"title": " ".join(block.text.split())[:200], "block_id": block.id,
                             "segment": len(segments)})

        # Group sentences into segments.
        ranges: list[tuple[int, int]] = []
        for s, e in sentence_spans(text):
            ranges.extend(_split_long(text, s, e, hard_max) if e - s > hard_max else [(s, e)])
        groups: list[tuple[int, int]] = []
        for s, e in ranges:
            if groups and (e - groups[-1][0]) <= max_chars:
                groups[-1] = (groups[-1][0], e)
            else:
                groups.append((s, e))

        for gi, (s, e) in enumerate(groups):
            seg_text_raw = text[s:e]
            lstrip = len(seg_text_raw) - len(seg_text_raw.lstrip())
            seg_text = seg_text_raw.strip()
            if not seg_text:
                continue
            base = s + lstrip
            seg_pieces = []
            for p in pieces:
                if p.rend <= base or p.rstart >= base + len(seg_text):
                    continue
                clipped = Piece(max(p.rstart, base) - base, min(p.rend, base + len(seg_text)) - base,
                                p.block_id, p.part,
                                p.dstart + (max(p.rstart, base) - p.rstart if p.exact else 0),
                                (p.dstart + (min(p.rend, base + len(seg_text)) - p.rstart)) if p.exact else p.dend,
                                p.exact)
                seg_pieces.append(clipped)
            segments.append(Segment(
                index=len(segments),
                text=seg_text,
                pieces=seg_pieces,
                block_id=block.id,
                pause_after=_pause_after(block, next_block, gi == len(groups) - 1),
                chapter=current_chapter,
            ))
            total_chars += len(seg_text)

    return ReadingPlan(language=language, segments=segments, chapters=chapters,
                       decisions=decisions, total_chars=total_chars)


def segment_cache_key(segment_text: str, provider_id: str, voice_id: str, model_id: Optional[str],
                      language: str, speed: float, engine_version: str = "1") -> str:
    payload = json.dumps([engine_version, provider_id, voice_id, model_id or "", language,
                          round(float(speed), 3), segment_text], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def word_ranges(text: str) -> Iterable[tuple[int, int]]:
    from .providers.piper_align import WORD_RE

    for m in WORD_RE.finditer(text):
        yield m.start(), m.end()


def find_piece_index(pieces: list[Piece], pos: int) -> int:
    starts = [p.rstart for p in pieces]
    return max(0, bisect.bisect_right(starts, pos) - 1)
