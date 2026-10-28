"""Format-independent finishing steps applied to every reader's blocks."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from .cleanup import detect_spans, is_reference_heading, merge_spans, normalize_text
from .model import Block, TableData, make_block_id

_GENERIC_HEADINGS = {
    "abstract", "sammendrag", "samandrag", "summary", "resymé", "resume", "innhold",
    "innholdsfortegnelse", "innhaldsliste", "contents", "table of contents", "forord",
    "føreord", "preface", "foreword", "innledning", "introduksjon", "introduction",
    "acknowledgements", "acknowledgments", "takk", "takksigelser", "kapittel 1", "chapter 1",
    "del 1", "part 1", "bakgrunn", "background", "metode", "method", "methods",
    "keywords", "nøkkelord", "emneord",
}
_BAD_TITLE_RE = re.compile(
    r"^(untitled|ikke navngitt|uten tittel|no title|title|tittel|document\d*|dokument\d*|"
    r"presentation\d*|presentasjon\d*|powerpoint presentation|slide \d+|layout \d+|"
    r"template|mal|microsoft word|unknown|ukjent|none|null|default|word)$",
    re.IGNORECASE,
)
_FILE_LIKE_RE = re.compile(
    r"\.(docx?|pdf|tex|dvi|ps|indd|qxd|qxp|rtf|odt|pages|txt|epub|html?|xml|wpd|pub|ppt|pptx)$",
    re.IGNORECASE,
)


def meaningful_title(raw: str | None) -> str | None:
    """Return a cleaned metadata title, or None when it is not a real title.

    Rejects empty/very short strings, placeholders («Untitled», «Dokument1»,
    «Microsoft Word - fil.docx», «Layout 1»), file names, paths, DOIs/PII
    codes and template instructions («(use style: paper title)»).
    """
    if not raw:
        return None
    t = normalize_text(str(raw))
    t = re.sub(r"^microsoft\s+(word|powerpoint)\s*-\s*", "", t, flags=re.IGNORECASE)
    t = t.strip(" \"'«»")
    if len(t) < 3 or not re.search(r"[^\W\d_]{2,}", t):
        return None
    if _BAD_TITLE_RE.match(t) or _FILE_LIKE_RE.search(t):
        return None
    if re.search(r"[\\/]", t) and " " not in t:
        return None
    low = t.lower()
    if low.startswith(("doi:", "pii:", "http://", "https://", "www.")) or "use style" in low:
        return None
    if re.match(r"^10\.\d{4,9}/", t):
        return None
    return t[:300]


def _title_level(blocks: Sequence[Block]) -> int:
    levels = [b.level for b in blocks if b.kind == "heading" and b.level > 0]
    return min(levels) if levels else 0


def choose_title(meta_title: str | None, blocks: Sequence[Block], fallback: str) -> str:
    """Metadata title if meaningful, else the first top-level heading, else ``fallback``."""
    t = meaningful_title(meta_title)
    if t:
        return t
    top = _title_level(blocks)
    if top:
        for b in blocks:
            if b.kind == "heading" and b.level == top and b.section != "references":
                text = normalize_text(b.text)
                key = re.sub(r"^[\d.]+\s*", "", text).strip(" :.").lower()
                if text and key not in _GENERIC_HEADINGS and not is_reference_heading(text):
                    return text[:200]
                break
    stem = normalize_text(fallback.replace("_", " "))
    return stem or "Uten tittel"


def _chapter_level(blocks: Sequence[Block]) -> int:
    counts = Counter(b.level for b in blocks if b.kind == "heading" and b.level > 0)
    if not counts:
        return 1
    for lvl in sorted(counts):
        if counts[lvl] >= 2:
            return lvl
    return min(counts)


def mark_reference_sections(blocks: list[Block]) -> None:
    """Classify reference lists.

    A heading matching :func:`is_reference_heading` (a short standalone
    paragraph matching it is promoted to a heading first) gets
    ``section="references"``, and so does every following block until the
    next heading of the same or a higher level that is not itself a
    reference heading. Paragraphs/list items inside become ``"reference"``.
    """
    chapter_level = _chapter_level(blocks)
    for b in blocks:
        if (
            b.kind == "paragraph"
            and b.section is None
            and len(b.text) <= 40
            and is_reference_heading(b.text)
        ):
            b.kind = "heading"
            b.level = chapter_level

    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if b.kind == "heading" and is_reference_heading(b.text):
            level = b.level or 1
            b.section = "references"
            j = i + 1
            while j < n:
                c = blocks[j]
                if c.kind == "heading" and (c.level or 1) <= level and not is_reference_heading(c.text):
                    break
                if c.kind in ("paragraph", "list_item", "reference"):
                    c.kind = "reference"
                    c.section = "references"
                elif c.kind == "heading":
                    c.section = "references"
                j += 1
            i = j
        else:
            i += 1


def table_text(table: TableData) -> str:
    rows = ([table.header] if table.header else []) + table.rows
    return "\n".join(" | ".join(cell for cell in row) for row in rows)


def build_table_data(
    rows: list[list[str]],
    *,
    first_row_is_header: bool,
    merged: bool = False,
    nested: bool = False,
    extra_problem: str | None = None,
) -> TableData | None:
    """Make a :class:`TableData` and judge its reliability.

    Rows are padded to equal width. The table is unreliable (with a
    Norwegian note) when no header row was identified, when cells are
    merged, when a cell contains another table, when rows had different
    numbers of cells, or when the reader adds its own reason.
    Returns None for tables without any text.
    """
    rows = [[normalize_text(c) for c in r] for r in rows if r]
    rows = [r for r in rows if any(r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    ragged = any(len(r) != width for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header = None
    if first_row_is_header and len(rows) > 1:
        header, rows = rows[0], rows[1:]
    problems = []
    if header is None:
        problems.append("Fant ingen tydelig overskriftsrad i tabellen.")
    if merged:
        problems.append("Tabellen har sammenslåtte celler.")
    if nested:
        problems.append("Tabellen inneholder en tabell inni en celle.")
    if ragged:
        problems.append("Radene i tabellen har ulikt antall celler.")
    if extra_problem:
        problems.append(extra_problem)
    note = None
    if problems:
        note = " ".join(problems) + " Kontroller tabellen før den leses opp."
    return TableData(rows=rows, header=header, reliable=not problems, note=note)


def finalize_blocks(blocks: Sequence[Block]) -> list[Block]:
    """Drop empty blocks, assign sequential ids and add url/doi/email spans.

    Reader-provided spans (footnote markers) win over detected ones on overlap.
    """
    out: list[Block] = []
    for b in blocks:
        if b.kind == "table" and b.table is not None and not b.text.strip():
            b.text = table_text(b.table)
        if not b.text or not b.text.strip():
            continue
        if b.level and b.kind != "heading":
            b.level = 0
        if b.kind == "heading":
            b.level = max(1, min(6, b.level or 1))
        b.spans = merge_spans(
            [s for s in b.spans if 0 <= s.start < s.end <= len(b.text)], detect_spans(b.text)
        )
        b.id = make_block_id(len(out))
        out.append(b)
    return out


_LANG_KINDS = {"paragraph", "heading", "list_item", "quote", "caption"}


def language_sample(blocks: Sequence[Block], limit: int = 200_000) -> str:
    """Body text used for language detection (no references/footnotes/headers)."""
    parts: list[str] = []
    size = 0
    for b in blocks:
        if b.kind in _LANG_KINDS and b.section != "references":
            parts.append(b.text)
            size += len(b.text)
            if size > limit:
                break
    text = "\n".join(parts)
    if len(text) < 200:
        text = "\n".join(b.text for b in blocks)[:limit]
    return text
