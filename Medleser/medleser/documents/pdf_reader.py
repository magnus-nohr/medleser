"""PDF reader (PyMuPDF) with layout analysis for academic articles.

Pipeline
--------
1. **Extraction** per page with ``page.get_text("dict")``: one
   :class:`~layout.TextLine` per PDF line with its dominant font size, bold
   share and superscript footnote markers (spans with the superscript flag
   whose text is a number/symbol and that follow a word). Pages whose text is
   mostly rotated are turned into a reading frame first. Image boxes, dense
   vector-drawing regions and tables (``page.find_tables()``, only on pages
   with ruling lines; a clipped text-strategy pass for «booktabs» tables
   that only have horizontal rules) are collected as well.
2. **OCR** (Windows OCR) for pages without a usable text layer that contain
   a page-covering image or drawings, for all pages when the whole document
   lacks text, and for every page with ``force_ocr``.
3. **Page furniture**: lines in the top/bottom 9 % of the page whose text
   (digits normalised) repeats on several pages become ``header``/``footer``;
   page-number-only lines there become ``page_number``. Rotated text in the
   margin (e.g. repository stamps) is treated as a header.
4. **Figures/tables**: text inside image boxes, or short/small text inside
   dense drawing regions, becomes one ``figure_text`` block per figure; text
   inside a detected table is replaced by one ``table`` block.
5. **Reading order** per page with the column-aware ordering in
   :mod:`layout` (tables and figures take part as boxes).
6. **Footnotes**: a trailing run of small-font lines (≤ ~90 % of the body
   size) in the lower half of a column that starts with a number/symbol.
7. **Paragraphs** (:func:`layout.group_paragraphs`) and classification:
   headings by font size (≥ 115 % of the body median) or short bold lines,
   captions (Figur/Table …), list items (bullet/enumeration).
   Heading levels follow the distinct heading styles (largest = 1) and are
   corrected with the PDF outline (``doc.get_toc()``) when present.
8. **Continuation**: a paragraph cut by a column/page break or a figure is
   rejoined when it does not end a sentence and the next part starts in
   lowercase.
9. **References**: after a reference heading, lines are re-split into one
   block per entry (numbered «[12]»/«12.», hanging indent, or «Surname, X.»
   after a finished entry).
Text of every paragraph is joined with :func:`cleanup.join_lines`
(hyphenation, broken URLs); footnote markers become ``footnote_ref`` spans.
"""

from __future__ import annotations

import itertools
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

import pymupdf

from ..errors import DocumentImportError
from . import ocr as ocr_engine
from .cleanup import (
    bullet_prefix_length,
    ends_sentence,
    hyphenated_vocabulary,
    is_caption,
    is_page_number_line,
    is_reference_heading,
    join_lines,
    normalize_repeat_key,
    normalize_text,
    normalize_with_spans,
    starts_with_enumeration,
    text_quality,
)
from .common import (
    MSG_NO_TEXT,
    MSG_NO_TEXT_AFTER_OCR,
    MSG_NO_TEXT_NO_OCR,
    WARN_OCR_USED,
    Progress,
    ReadResult,
    check_cancel,
)
from .layout import (
    Box,
    TextLine,
    center_inside,
    estimate_line_gaps,
    group_paragraphs,
    median,
    reading_order,
    size_bucket,
    sort_rows,
    to_reading_frame,
    union_box,
    vertical_overlap,
    weighted_median,
)
from .model import Block, Span, TableData
from .postprocess import build_table_data, table_text

if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()

# MuPDF is not thread-safe; all PDF work in this process is serialised.
PDF_LOCK = threading.RLock()

TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP

MSG_DAMAGED = "PDF-filen ser ut til å være skadet og kan ikke åpnes."
MSG_PASSWORD = (
    "PDF-filen er passordbeskyttet. Åpne den i et PDF-program, lagre en kopi uten passord "
    "og prøv igjen."
)
MSG_COPY_PROTECTED = (
    "PDF-filen er kopibeskyttet, og utgiveren tillater ikke at teksten hentes ut – heller ikke "
    "for tilgjengelighet. Medleser kan derfor ikke lese den."
)
WARN_COPY_RESTRICTED = (
    "Utgiveren har sperret kopiering fra PDF-filen, men tillater at teksten hentes ut for "
    "tilgjengelighet. Medleser bruker teksten bare til opplesning."
)
WARN_OCR_FAILED = "Tekstgjenkjenning (OCR) mislyktes på noen sider. Disse sidene kan mangle tekst."
WARN_OCR_UNAVAILABLE = (
    "Noen sider ser ut til å være skannet, men tekstgjenkjenning (OCR) er ikke tilgjengelig på "
    "denne maskinen. Disse sidene mangler tekst."
)
WARN_TABLE = "Noen tabeller kan være feiltolket. Kontroller tabellene før de leses opp."
WARN_LITTLE_TEXT = (
    "Dokumentet inneholder svært lite tekst. Hvis det er skannet, kan du prøve "
    "tekstgjenkjenning (OCR)."
)
NOTE_TEXT_TABLE = "Tabellen mangler rutenett, så kolonnene er tolket ut fra plasseringen av teksten."

_MARKER_RE = re.compile(r"^(?:\d{1,3}(?:[,–\-]\d{1,3})*|[*†‡§¶]{1,3})$")
_BOLD_FONT_RE = re.compile(r"bold|black|heavy|semibold|demibold|extrabold|ultrabold|[-,]bd?$", re.IGNORECASE)
_ITALIC_FONT_RE = re.compile(r"italic|oblique|[-,]it?$", re.IGNORECASE)
_FOOTNOTE_START_RE = re.compile(r"^(?:\d{1,3}|[*†‡§¶]{1,3})(?=\s|[^\W\d_]|[(«\"“])")
_REF_START_RE = re.compile(r"^\s*(?:\[\d{1,4}\]|\d{1,3}\.\s|\(\d{1,4}\)\s)")
_AUTHOR_START_RE = re.compile(
    r"^[A-ZÆØÅÄÖÜÉ][\w'’\-]+(?:\s(?:[A-ZÆØÅÄÖÜ][\w'’\-]+|de|van|von|der|den|la|le|da|di|du))*,\s+"
    r"(?:[A-ZÆØÅÄÖÜ]\.|[A-ZÆØÅÄÖÜ][a-zæøåäöü]+)"
)
_KEY_RE = re.compile(r"[\W_]+")
_FURNITURE = ("header", "footer", "page_number")
_NON_FLOW = {"header", "footer", "page_number", "footnote", "figure_text", "table", "caption"}


# ---------------------------------------------------------------------------
# Data holders
# ---------------------------------------------------------------------------


@dataclass
class _Table:
    bbox: Box
    data: TableData
    consumed: list[Box]


@dataclass
class _Page:
    index: int
    frame: Box
    lines: list[TextLine] = field(default_factory=list)
    images: list[Box] = field(default_factory=list)
    regions: list[Box] = field(default_factory=list)
    tables: list[_Table] = field(default_factory=list)
    chars: int = 0
    quality: float = 1.0
    big_image: bool = False
    n_paths: int = 0
    rotation: int = 0
    rotated_frame: bool = False
    ocr: bool = False


@dataclass
class _Item:
    """A table or figure taking part in the reading order as one box."""

    x0: float
    y0: float
    x1: float
    y1: float
    block: Block


@dataclass
class _Proto:
    kind: str
    page: int
    lines: list[TextLine] = field(default_factory=list)
    level: int = 0
    block: Block | None = None
    segment: int = -1
    style: tuple[float, bool] = (0.0, False)
    toc: bool = False

    def text(self) -> str:
        if self.block is not None:
            return self.block.text
        return " ".join(ln.text for ln in self.lines)


Entry = Union[TextLine, _Item]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _make_line(line: dict[str, Any], page_index: int) -> TextLine | None:
    parts: list[str] = []
    length = 0
    markers: list[Span] = []
    sizes: list[float] = []
    weights: list[float] = []
    bold_w = italic_w = total_w = 0
    marker_sizes: list[float] = []
    fonts: Counter[str] = Counter()
    spans_in = [s for s in line.get("spans", []) if s.get("text")]
    # A small raised number/symbol that starts a fragment is a marker even
    # without the superscript flag (MuPDF measures «raised» against the first
    # character of the line, which is the marker itself).
    lead = None
    if len(spans_in) >= 2:
        s0, s1 = spans_in[0], spans_in[1]
        t0 = s0["text"].strip()
        try:
            raised = float(s0["origin"][1]) < float(s1["origin"][1]) - 0.15 * float(s1["size"])
        except (KeyError, IndexError, TypeError, ValueError):
            raised = False
        if t0 and _MARKER_RE.match(t0) and float(s0["size"]) <= 0.85 * float(s1["size"]) and raised:
            lead = s0
    for s in spans_in:
        t = s.get("text") or ""
        flags = int(s.get("flags", 0))
        size = float(s.get("size", 0.0))
        font = str(s.get("font", ""))
        stripped = t.strip()
        if stripped and (flags & 1 or s is lead) and _MARKER_RE.match(stripped):
            prev = "".join(parts).rstrip()
            if not prev or prev[-1].isalpha() or prev[-1] in ".,;:!?)»”\"'’":
                start = length + t.index(stripped)
                markers.append(Span(start, start + len(stripped), "footnote_ref"))
                marker_sizes.append(size)
                parts.append(t)
                length += len(t)
                continue
        n = len(stripped)
        if n:
            sizes.append(size)
            weights.append(n)
            total_w += n
            if flags & 16 or _BOLD_FONT_RE.search(font):
                bold_w += n
            if flags & 2 or _ITALIC_FONT_RE.search(font):
                italic_w += n
            fonts[font] += n
        parts.append(t)
        length += len(t)
    raw = "".join(parts)
    if not raw.strip():
        return None
    r = raw.rstrip()
    if r.endswith("­"):
        raw = r[:-1] + "-"  # keep a line-final soft hyphen visible for join_lines
    text, spans = normalize_with_spans(raw, markers)
    if not text:
        return None
    marker_only = total_w == 0
    if marker_only:
        size = median(marker_sizes, 8.0)
    else:
        size = weighted_median(sizes, weights, 10.0)
    x0, y0, x1, y1 = line.get("bbox", (0, 0, 0, 0))
    tl = TextLine(
        x0=float(x0),
        y0=float(y0),
        x1=float(x1),
        y1=float(y1),
        text=text,
        size=round(size, 2),
        bold=total_w > 0 and bold_w >= 0.8 * total_w,
        italic=total_w > 0 and italic_w >= 0.8 * total_w,
        page=page_index,
        markers=[(sp.start, sp.end) for sp in spans if not marker_only],
        font=fonts.most_common(1)[0][0] if fonts else "",
    )
    if _MARKER_RE.match(text) and (marker_only or len(text) <= 3):
        tl.kind = "_marker"  # candidate; attached to its line or turned back into a normal line
    return tl


def _attach_markers(lines: list[TextLine]) -> list[TextLine]:
    """Join footnote markers that MuPDF put on a line of their own to the line they belong to.

    A short number/symbol line counts as a marker when it is clearly smaller
    than an adjacent line, sits raised against it and starts at (or within)
    that line's horizontal extent. Other candidates stay normal lines.
    """
    markers = [ln for ln in lines if ln.kind == "_marker"]
    if not markers:
        return lines
    others = [ln for ln in lines if ln.kind != "_marker"]
    for m in markers:
        best: TextLine | None = None
        best_dist = 0.0
        best_mode = ""
        mh = max(0.5, m.height)
        for ln in others:
            if m.size > 0.85 * ln.size or vertical_overlap(m, ln) < 0.2 * mh:
                continue
            if (m.y0 + m.y1) / 2 >= (ln.y0 + ln.y1) / 2:
                continue  # not raised: an ordinary small number
            if m.x1 <= ln.x0 + 1 and ln.x0 - m.x1 < ln.size:
                mode, dist = "prefix", ln.x0 - m.x1  # the note's own number before its text
            elif ln.x0 - 1 <= m.x0 <= ln.x1 + 2 * ln.size:
                mode, dist = "ref", max(0.0, m.x0 - ln.x1)
            else:
                continue
            if best is None or dist < best_dist:
                best, best_dist, best_mode = ln, dist, mode
        if best is not None and best_mode == "prefix":
            shift = len(m.text) + 1
            best.text = f"{m.text} {best.text}"
            best.markers = [(a + shift, b + shift) for a, b in best.markers]
        elif best is not None:
            text = best.text
            if m.x0 >= best.x1 - 1:
                pos = len(text)
            else:
                pos = int(round(len(text) * (m.x0 - best.x0) / max(1.0, best.width)))
                pos = max(0, min(len(text), pos))
                while pos < len(text) and text[pos].isalnum():
                    pos += 1
            before = text[pos - 1] if pos > 0 else ""
            if not (before.isalpha() or before in ".,;:!?)»”\"'’"):
                best = None  # e.g. an exponent after a digit: keep it as ordinary text
            else:
                best.text = text[:pos] + m.text + text[pos:]
                shifted = [(a + len(m.text), b + len(m.text)) if a >= pos else (a, b) for a, b in best.markers]
                shifted.append((pos, pos + len(m.text)))
                best.markers = sorted(shifted)
        if best is None:
            m.kind = None
            others.append(m)
            continue
        best.x0, best.y0 = min(best.x0, m.x0), min(best.y0, m.y0)
        best.x1, best.y1 = max(best.x1, m.x1), max(best.y1, m.y1)
    return others


def _merge_fragments(
    raw: list[tuple[TextLine, tuple[float, float]]],
) -> list[tuple[TextLine, tuple[float, float]]]:
    """Re-join pieces of one visual line that MuPDF split (raised markers, sub/superscripts).

    Horizontal fragments on the same row separated by at most a hairline gap
    (< 0.25 em) are merged; a space is inserted unless the gap is tiny.
    """
    if len(raw) < 2:
        return raw
    order = sort_rows([tl for tl, _ in raw])
    out: list[tuple[TextLine, tuple[float, float]]] = []
    for i in order:
        tl, key = raw[i]
        if out:
            prev, pkey = out[-1]
            h = min(prev.height, tl.height)
            big = max(prev.size, tl.size)
            gap = tl.x0 - prev.x1
            if (
                key == pkey == (1.0, 0.0)
                and prev.kind != "_marker"
                and tl.kind != "_marker"
                and h > 0
                and vertical_overlap(prev, tl) >= 0.5 * h
                and -0.5 <= gap < 0.25 * big
            ):
                sep = "" if gap < 0.15 * big else " "
                shift = len(prev.text) + len(sep)
                n_prev, n_new = len(prev.text), len(tl.text)
                prev.text = prev.text + sep + tl.text
                prev.markers = prev.markers + [(a + shift, b + shift) for a, b in tl.markers]
                if n_new > n_prev:
                    prev.size, prev.font = tl.size, tl.font
                prev.bold = (n_prev * prev.bold + n_new * tl.bold) >= 0.8 * (n_prev + n_new)
                prev.italic = (n_prev * prev.italic + n_new * tl.italic) >= 0.8 * (n_prev + n_new)
                prev.x1 = max(prev.x1, tl.x1)
                prev.y0, prev.y1 = min(prev.y0, tl.y0), max(prev.y1, tl.y1)
                continue
        out.append((tl, key))
    return out


def _dedupe(lines: list[TextLine]) -> list[TextLine]:
    """Drop exact duplicates (fake bold/shadow text drawn twice at the same spot)."""
    seen: set[tuple[str, int, int]] = set()
    out = []
    for ln in lines:
        key = (ln.text, int(round(ln.x0 / 2)), int(round(ln.y0 / 2)))
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def _count_rules(paths: list[dict[str, Any]], page_area: float) -> tuple[int, int, list[tuple[float, float, float]]]:
    """Count horizontal/vertical ruling segments (also from thin rectangles)."""
    h = v = 0
    hsegs: list[tuple[float, float, float]] = []
    for p in paths:
        for it in p.get("items", []):
            op = it[0]
            if op == "l":
                p1, p2 = it[1], it[2]
                dx, dy = abs(p1.x - p2.x), abs(p1.y - p2.y)
                if dy < 1.5 and dx > 8:
                    h += 1
                    hsegs.append((min(p1.x, p2.x), max(p1.x, p2.x), (p1.y + p2.y) / 2))
                elif dx < 1.5 and dy > 8:
                    v += 1
            elif op in ("re", "qu"):
                r = it[1] if op == "re" else it[1].rect
                if r.width * r.height > 0.5 * page_area:
                    continue  # page background
                if r.height < 3 and r.width > 8:
                    h += 1
                    hsegs.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width < 3 and r.height > 8:
                    v += 1
                elif r.width > 8 and r.height > 8:
                    h += 2
                    v += 2
    return h, v, hsegs


def _safe_find_tables(page: Any, **kwargs: Any) -> list[Any]:
    try:
        finder = page.find_tables(**kwargs)
    except Exception:  # noqa: BLE001 - table detection is best effort
        return []
    if finder is None:
        return []
    return list(getattr(finder, "tables", []) or [])


def _derot(page: Any, bbox: Any) -> Box:
    r = pymupdf.Rect(bbox) * page.derotation_matrix
    r.normalize()
    return Box(r.x0, r.y0, r.x1, r.y1)


def _looks_numeric(s: str) -> bool:
    t = s.replace(" ", "").replace("−", "-")
    return bool(re.fullmatch(r"[-+±]?[\d.,]+%?|[-–—]|n\.?s\.?|\(?[\d.,]+\)?\*{0,3}", t))


def _table_from_finder(page: Any, tab: Any, lines: list[TextLine], text_strategy: bool) -> _Table | None:
    try:
        rows = tab.extract()
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    merged = any(c is None for r in rows for c in r)
    cells = [[normalize_text(c or "") for c in r] for r in rows]
    cells = [r for r in cells if any(r)]
    if len(cells) < 2 or max(len(r) for r in cells) < 2:
        return None
    if text_strategy:
        nonempty = [c for r in cells for c in r if c]
        if not nonempty:
            return None
        avg_words = sum(len(c.split()) for c in nonempty) / len(nonempty)
        multi = sum(1 for r in cells if sum(1 for c in r if c) >= 2)
        if avg_words > 6 or multi < 0.6 * len(cells):
            return None
    bbox = _derot(page, tab.bbox)
    consumed = [bbox]
    header_external = False
    try:
        hdr = tab.header
        if hdr is not None and hdr.external and hdr.names and any(hdr.names):
            names = [normalize_text(x or "") for x in hdr.names]
            cells = [names] + cells
            consumed.append(_derot(page, hdr.bbox))
            header_external = True
    except Exception:  # noqa: BLE001
        pass
    first_bold = False
    try:
        row_box = _derot(page, tab.rows[0].bbox) if not header_external else None
    except Exception:  # noqa: BLE001
        row_box = None
    if row_box is not None:
        inside = [ln for ln in lines if center_inside(ln, row_box, 1.0)]
        total = sum(len(ln.text) for ln in inside)
        bold = sum(len(ln.text) for ln in inside if ln.bold)
        first_bold = total > 0 and bold >= 0.6 * total
    body_cells = [c for r in cells[1:] for c in r if c]
    numeric_body = (
        bool(body_cells)
        and sum(1 for c in body_cells if _looks_numeric(c)) >= 0.5 * len(body_cells)
        and all(c and not _looks_numeric(c) for c in cells[0])
    )
    data = build_table_data(
        cells,
        first_row_is_header=header_external or first_bold or numeric_body,
        merged=merged,
        extra_problem=NOTE_TEXT_TABLE if text_strategy else None,
    )
    if data is None:
        return None
    return _Table(bbox=bbox, data=data, consumed=consumed)


def _booktabs_regions(hsegs: list[tuple[float, float, float]], frame_h: float) -> list[pymupdf.Rect]:
    """Regions framed by top rule, mid rule (close below) and bottom rule of equal width."""
    groups: list[list[tuple[float, float, float]]] = []
    for seg in sorted(hsegs, key=lambda s: s[2]):
        if seg[1] - seg[0] < 120:
            continue
        for g in groups:
            if abs(g[0][0] - seg[0]) < 4 and abs(g[0][1] - seg[1]) < 4:
                if abs(g[-1][2] - seg[2]) > 1.5:
                    g.append(seg)
                break
        else:
            groups.append([seg])
    regions = []
    for g in groups:
        ys = [s[2] for s in g]
        k = 0
        while k + 2 < len(ys) + 0:
            top, mid = ys[k], ys[k + 1]
            if mid - top > 60:
                k += 1
                continue
            bottom_idx = k + 2
            if ys[bottom_idx] - top > 0.7 * frame_h:
                k += 1
                continue
            regions.append(pymupdf.Rect(g[0][0] - 2, top - 2, g[0][1] + 2, ys[bottom_idx] + 2))
            k = bottom_idx + 1
    return regions


def _find_tables(page: Any, lines: list[TextLine], paths: list[dict[str, Any]], frame_h: float, area: float) -> list[_Table]:
    if len(paths) > 3000:
        return []
    h, v, hsegs = _count_rules(paths, area)
    tables: list[_Table] = []
    if h >= 3 and v >= 3:
        for tab in _safe_find_tables(page):
            t = _table_from_finder(page, tab, lines, text_strategy=False)
            if t is not None:
                tables.append(t)
    if h >= 3:
        for region in _booktabs_regions(hsegs, frame_h):
            rb = _derot(page, region)
            if any(
                min(rb.x1, t.bbox.x1) > max(rb.x0, t.bbox.x0) and min(rb.y1, t.bbox.y1) > max(rb.y0, t.bbox.y0)
                for t in tables
            ):
                continue
            for tab in _safe_find_tables(page, clip=region, strategy="text"):
                t = _table_from_finder(page, tab, lines, text_strategy=True)
                if t is not None:
                    tables.append(t)
    return tables


def _dense_regions(page: Any, paths: list[dict[str, Any]], area: float) -> list[Box]:
    """Regions with many vector drawing items (charts, diagrams).

    Drawings closer than 10 pt are clustered; a cluster counts when it holds
    at least 12 drawing items and covers 1–90 % of the page. The region is
    widened by 10 pt so axis and tick labels just outside the plot belong to
    the figure.
    """
    if not 8 <= len(paths) <= 1500:
        return []
    try:
        clusters = page.cluster_drawings(drawings=paths, x_tolerance=10, y_tolerance=10)
    except Exception:  # noqa: BLE001
        return []
    regions = []
    # (x0, y0, x1, y1, item count); lines have zero-height rects, so compare coordinates
    items = [
        (*tuple(pymupdf.Rect(p["rect"])), len(p.get("items", [])) or 1) for p in paths if p.get("rect") is not None
    ]
    for c in clusters:
        a = c.width * c.height
        if a < 0.01 * area or a > 0.9 * area:
            continue
        count = sum(n for x0, y0, x1, y1, n in items if x0 <= c.x1 and x1 >= c.x0 and y0 <= c.y1 and y1 >= c.y0)
        if count >= 12:
            regions.append(Box(c.x0 - 10, c.y0 - 10, c.x1 + 10, c.y1 + 10))
    return regions


def _extract_page(page: Any, index: int) -> _Page:
    rect = page.rect
    rot = int(page.rotation or 0) % 360
    uw, uh = (rect.height, rect.width) if rot in (90, 270) else (rect.width, rect.height)
    area = max(1.0, uw * uh)
    d = page.get_text("dict", flags=TEXT_FLAGS)
    raw: list[tuple[TextLine, tuple[float, float]]] = []
    weights: Counter[tuple[float, float]] = Counter()
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:
            continue
        for ln in b.get("lines", []):
            tl = _make_line(ln, index)
            if tl is None:
                continue
            dx, dy = ln.get("dir", (1.0, 0.0))
            key = (round(float(dx), 1), round(float(dy), 1))
            weights[key] += len(tl.text)
            raw.append((tl, key))
    raw = _merge_fragments(raw)
    all_text = " ".join(tl.text for tl, _ in raw)
    chars = sum(1 for c in all_text if not c.isspace())
    quality = text_quality(all_text) if chars else 0.0

    total = sum(weights.values())
    dominant = max(weights, key=lambda k: weights[k]) if weights else (1.0, 0.0)
    rotate = dominant != (1.0, 0.0) and weights[dominant] >= 0.6 * total
    direction = dominant if rotate else (1.0, 0.0)

    images: list[Box] = []
    big_image = False
    try:
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001
        infos = []
    for info in infos:
        x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
        bw, bh = x1 - x0, y1 - y0
        if bw < 15 or bh < 15:
            continue
        if bw * bh >= 0.5 * area:
            big_image = True
            continue
        images.append(Box(x0, y0, x1, y1))

    try:
        paths = page.get_drawings()
    except Exception:  # noqa: BLE001
        paths = []
    lines_unrot = [tl for tl, _ in raw]
    tables = _find_tables(page, lines_unrot, paths, uh, area)
    regions = _dense_regions(page, paths, area)

    def tf(bx: Box) -> Box:
        if not rotate:
            return bx
        return Box(*to_reading_frame((bx.x0, bx.y0, bx.x1, bx.y1), direction))

    lines: list[TextLine] = []
    for tl, key in raw:
        if rotate:
            tl.x0, tl.y0, tl.x1, tl.y1 = to_reading_frame((tl.x0, tl.y0, tl.x1, tl.y1), direction)
        tl.upright = abs(key[0] - direction[0]) < 0.15 and abs(key[1] - direction[1]) < 0.15
        lines.append(tl)
    lines = _attach_markers(_dedupe(lines))
    for t in tables:
        t.bbox = tf(t.bbox)
        t.consumed = [tf(c) for c in t.consumed]
    return _Page(
        index=index,
        frame=tf(Box(0.0, 0.0, uw, uh)),
        lines=lines,
        images=[tf(b) for b in images],
        regions=[tf(r) for r in regions],
        tables=tables,
        chars=chars,
        quality=quality,
        big_image=big_image,
        n_paths=len(paths),
        rotation=rot,
        rotated_frame=rotate,
    )


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def _ocr_lines(res: ocr_engine.OcrPage, page_index: int) -> list[TextLine]:
    out: list[TextLine] = []
    for ol in res.lines:
        words = sorted(ol.words, key=lambda w: w.x0)
        if not words:
            continue
        h = median([w.y1 - w.y0 for w in words], 10.0)
        segments = [[words[0]]]
        for a, b in zip(words, words[1:]):
            if b.x0 - a.x1 > max(1.8 * h, 6.0):
                segments.append([b])
            else:
                segments[-1].append(b)
        for seg in segments:
            text = normalize_text(" ".join(w.text for w in seg))
            if not text:
                continue
            seg_h = median([w.y1 - w.y0 for w in seg], h)
            out.append(
                TextLine(
                    x0=min(w.x0 for w in seg),
                    y0=min(w.y0 for w in seg),
                    x1=max(w.x1 for w in seg),
                    y1=max(w.y1 for w in seg),
                    text=text,
                    size=round(seg_h / 0.75, 2),
                    page=page_index,
                    ocr=True,
                )
            )
    return out


def _lacks_text(pg: _Page) -> bool:
    return pg.chars < 20 or pg.quality < 0.6


def _run_ocr(
    doc: Any,
    pages: list[_Page],
    force_ocr: bool,
    result: ReadResult,
    report: Progress,
    cancel: threading.Event | None,
) -> bool:
    """OCR the pages that need it. Returns True when OCR was attempted."""
    lacking = [pg.index for pg in pages if _lacks_text(pg)]
    all_lacking = len(lacking) == len(pages)
    if force_ocr:
        targets = [pg.index for pg in pages]
    elif all_lacking:
        targets = lacking
    else:
        targets = [i for i in lacking if pages[i].big_image or pages[i].n_paths >= 50]
        # scanned page with a small text stamp (e.g. «Downloaded from …»)
        targets += [
            pg.index for pg in pages
            if pg.index not in lacking and pg.big_image and pg.chars < 150 and pg.rotation == 0
            and not pg.rotated_frame
        ]
        targets.sort()
    if not targets:
        return False
    if not ocr_engine.ocr_available():
        if force_ocr:
            raise ocr_engine.OcrUnavailableError("force_ocr requested")
        if all_lacking:
            raise DocumentImportError(MSG_NO_TEXT_NO_OCR, code="ocr_unavailable", details="no text layer, no OCR")
        result.warn("ocr_unavailable", WARN_OCR_UNAVAILABLE)
        return False
    used = failed = 0
    n = len(pages)
    for k, i in enumerate(targets):
        check_cancel(cancel)
        report(0.55 + 0.35 * k / len(targets), f"Tekstgjenkjenning (OCR) på side {i + 1} av {n} …")
        try:
            res = ocr_engine.ocr_pdf_page(doc.load_page(i))
        except ocr_engine.OcrUnavailableError:
            failed += len(targets) - k
            break
        except Exception:  # noqa: BLE001 - OCR of one page failed
            failed += 1
            continue
        lines = _ocr_lines(res, i)
        if not lines:
            continue
        old = pages[i]
        if force_ocr or _lacks_text(old):
            pages[i] = _Page(
                index=i,
                frame=Box(0.0, 0.0, res.width, res.height),
                lines=lines,
                chars=sum(len(ln.text) for ln in lines),
                quality=1.0,
                ocr=True,
            )
        else:
            # keep the text layer, add recognised lines that it does not cover
            extra = [
                ln for ln in lines
                if not any(vertical_overlap(ln, o) > 0.5 * ln.height and min(ln.x1, o.x1) > max(ln.x0, o.x0)
                           for o in old.lines)
            ]
            if not extra:
                continue
            old.lines.extend(extra)
            old.chars += sum(len(ln.text) for ln in extra)
        used += 1
    if used:
        result.ocr_used = True
        result.warn("ocr_used", WARN_OCR_USED)
    if failed:
        result.warn("ocr_failed", WARN_OCR_FAILED)
    return True


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


class _Analyzer:
    def __init__(self, pages: list[_Page], toc: list[list[Any]], cancel: threading.Event | None) -> None:
        self.pages = pages
        self.toc = toc
        self.cancel = cancel
        text_sizes = [(ln.size, len(ln.text)) for pg in pages for ln in pg.lines if ln.upright and not ln.ocr]
        ocr_sizes = [(ln.size, len(ln.text)) for pg in pages for ln in pg.lines if ln.ocr]
        self.body = weighted_median([s for s, _ in text_sizes], [w for _, w in text_sizes], 10.0)
        self.body_ocr = weighted_median([s for s, _ in ocr_sizes], [w for _, w in ocr_sizes], self.body)
        self.vocab = hyphenated_vocabulary([ln.text for pg in pages for ln in pg.lines])
        self.fig_of: dict[int, tuple[str, int]] = {}
        self.gaps: dict[float, float] = {}
        self.unreliable_tables = sum(1 for pg in pages for t in pg.tables if not t.data.reliable)
        self._seg = itertools.count()

    # -- line predicates ------------------------------------------------------
    def body_for(self, ln: TextLine) -> float:
        return self.body_ocr if ln.ocr else self.body

    def is_large(self, ln: TextLine) -> bool:
        body = self.body_for(ln)
        if ln.ocr:
            return ln.size >= body * 1.35
        return ln.size >= body * 1.15 and ln.size - body >= 0.9

    def is_heading_line(self, ln: TextLine) -> bool:
        if ln.kind is not None or not ln.upright:
            return False
        words = len(ln.text.split())
        if words == 0 or words > 30 or not re.search(r"[^\W\d_]", ln.text):
            return False
        if self.is_large(ln):
            return True
        return (not ln.ocr) and ln.bold and ln.size >= self.body * 0.88 and words <= 20

    def is_small(self, ln: TextLine) -> bool:
        body = self.body_for(ln)
        if ln.ocr:
            return ln.size <= body * 0.8
        return ln.size <= min(body * 0.92, body - 0.6)

    # -- page furniture -------------------------------------------------------
    def classify_margins(self) -> None:
        n = len(self.pages)
        counts: Counter[tuple[str, str]] = Counter()
        per_page: list[list[tuple[TextLine, str, str]]] = []
        for pg in self.pages:
            H = max(1.0, pg.frame.height)
            top = pg.frame.y0 + 0.09 * H
            bottom = pg.frame.y1 - 0.09 * H
            entries = []
            seen = set()
            for ln in pg.lines:
                if ln.kind is not None:
                    continue
                if not ln.upright:
                    zone = "side"
                elif ln.y1 <= top:
                    zone = "top"
                elif ln.y0 >= bottom:
                    zone = "bottom"
                else:
                    continue
                key = normalize_repeat_key(ln.text)
                entries.append((ln, zone, key))
                if (zone, key) not in seen:
                    seen.add((zone, key))
                    counts[(zone, key)] += 1
            per_page.append(entries)
        min_rep = 2 if n <= 4 else 3
        for pg, entries in zip(self.pages, per_page):
            body_lines = [ln for ln in pg.lines if ln.upright and ln.kind is None]
            tx0 = min((ln.x0 for ln in body_lines), default=pg.frame.x0)
            tx1 = max((ln.x1 for ln in body_lines), default=pg.frame.x1)
            for ln, zone, key in entries:
                if zone == "side":
                    repeated = n >= 2 and counts[(zone, key)] >= min_rep
                    ln.kind = "header" if repeated or ln.x1 < tx0 - 2 or ln.x0 > tx1 + 2 else "figure_text"
                    if ln.kind == "figure_text":
                        self.fig_of[id(ln)] = ("rot", 0)
                    continue
                if is_page_number_line(ln.text):
                    ln.kind = "page_number"
                elif (
                    n >= 2
                    and key
                    and counts[(zone, key)] >= min_rep
                    and len(ln.text.split()) <= 25
                    and not is_caption(ln.text)
                    and ln.size <= self.body_for(ln) * 1.5
                ):
                    ln.kind = "header" if zone == "top" else "footer"

    def classify_figures(self, pg: _Page) -> None:
        if pg.ocr:
            return
        for ln in pg.lines:
            if ln.kind is not None or not ln.upright:
                continue
            for t in pg.tables:
                if any(center_inside(ln, c, 2.0) for c in t.consumed):
                    ln.kind = "table"
                    break
            if ln.kind or is_caption(ln.text):
                continue
            for k, img in enumerate(pg.images):
                if center_inside(ln, img, 1.0):
                    ln.kind = "figure_text"
                    self.fig_of[id(ln)] = ("img", k)
                    break
        for k, reg in enumerate(pg.regions):
            if any(t.bbox.x0 <= reg.x0 + 12 and t.bbox.y0 <= reg.y0 + 12 and t.bbox.x1 >= reg.x1 - 12
                   and t.bbox.y1 >= reg.y1 - 12 for t in pg.tables):
                continue  # the drawings are the ruling of a detected table
            inside = [
                ln for ln in pg.lines
                if ln.kind is None and ln.upright and center_inside(ln, reg, 1.0) and not is_caption(ln.text)
            ]
            total = sum(len(ln.text) for ln in inside)
            sentences = sum(len(ln.text) for ln in inside if len(ln.text.split()) >= 8)
            if not inside or sentences >= 0.5 * total:
                continue  # mostly running text: a decorated text box, not a chart
            for ln in inside:
                if len(ln.text.split()) <= 6 or ln.size < self.body * 0.9:
                    ln.kind = "figure_text"
                    self.fig_of[id(ln)] = ("reg", k)

    # -- ordering -------------------------------------------------------------
    def order_page(self, pg: _Page) -> list[Entry]:
        flow: list[TextLine] = []
        figures: dict[tuple[str, int], list[TextLine]] = defaultdict(list)
        for ln in pg.lines:
            if ln.kind == "figure_text":
                figures[self.fig_of.get(id(ln), ("rot", 0))].append(ln)
            elif ln.kind is None and ln.upright:
                flow.append(ln)
        items: list[_Item] = []
        for t in pg.tables:
            blk = Block(id="", kind="table", text=table_text(t.data), page=pg.index + 1, table=t.data)
            items.append(_Item(t.bbox.x0, t.bbox.y0, t.bbox.x1, t.bbox.y1, blk))
        late: list[_Item] = []
        for key, lines in figures.items():
            ordered = [lines[i] for i in sort_rows(lines)]
            text = normalize_text(" ".join(ln.text for ln in ordered))
            if not text:
                continue
            ub = union_box(ordered)
            blk = Block(id="", kind="figure_text", text=text, page=pg.index + 1, ocr=any(ln.ocr for ln in ordered))
            item = _Item(ub.x0, ub.y0, ub.x1, ub.y1, blk)
            (late if key[0] == "rot" else items).append(item)
        boxes: list[Entry] = [*flow, *items]
        entries: list[Entry] = []
        for p in reading_order(boxes):
            obj = boxes[p.index]
            if isinstance(obj, TextLine):
                obj.band = p.band
                obj.column = p.column
            entries.append(obj)
        entries.extend(late)
        return entries

    def mark_footnotes(self, pg: _Page, entries: list[Entry]) -> None:
        flows: dict[tuple[int, int], list[TextLine]] = defaultdict(list)
        for e in entries:
            if isinstance(e, TextLine) and e.kind is None:
                flows[(e.band, e.column)].append(e)
        lower = pg.frame.y0 + 0.45 * pg.frame.height
        for lst in flows.values():
            run: list[TextLine] = []
            for ln in reversed(lst):
                if self.is_small(ln) and ln.y0 >= lower:
                    run.append(ln)
                else:
                    break
            if not run or len(run) == len(lst) and len(lst) > 6:
                continue  # a whole column in small print is not a footnote area
            run.reverse()
            if not _FOOTNOTE_START_RE.match(run[0].text) or is_caption(run[0].text):
                continue
            for ln in run:
                ln.kind = "footnote"

    # -- paragraphs -----------------------------------------------------------
    def make_proto(self, lines: list[TextLine], page: int, segment: int) -> _Proto:
        first = lines[0]
        text0 = first.text
        words = sum(len(ln.text.split()) for ln in lines)
        sizes = [ln.size for ln in lines]
        bold = sum(1 for ln in lines if ln.bold) * 2 >= len(lines)
        style = (size_bucket(median(sizes, first.size)), bold)
        kind = "paragraph"
        if all(self.is_heading_line(ln) for ln in lines) and not bullet_prefix_length(text0):
            large = all(self.is_large(ln) for ln in lines)
            last = lines[-1].text.rstrip()
            if large and len(lines) <= 4 and words <= 40:
                kind = "heading"
            elif not large and len(lines) <= 2 and words <= 20 and not last.endswith((".", ",", ";")):
                kind = "heading"
        if kind == "paragraph":
            if is_caption(text0):
                kind = "caption"
            elif bullet_prefix_length(text0) or starts_with_enumeration(text0):
                kind = "list_item"
        return _Proto(kind=kind, page=page, lines=lines, segment=segment, style=style)

    def footnote_protos(self, lines: list[TextLine], page: int) -> list[_Proto]:
        if not lines:
            return []
        left: dict[tuple[int, int, int], float] = {}
        for ln in lines:
            left[ln.flow_key] = min(left.get(ln.flow_key, ln.x0), ln.x0)
        notes: list[list[TextLine]] = []
        for ln in lines:
            starts = bool(_FOOTNOTE_START_RE.match(ln.text))
            at_left = ln.x0 <= left[ln.flow_key] + 0.6 * ln.size
            if not notes or (starts and (at_left or ends_sentence(notes[-1][-1].text))) or (
                notes[-1][-1].flow_key != ln.flow_key and starts
            ):
                notes.append([ln])
            else:
                notes[-1].append(ln)
        return [_Proto(kind="footnote", page=page, lines=n) for n in notes]

    def group_page(self, pg: _Page, entries: list[Entry]) -> list[_Proto]:
        protos: list[_Proto] = []
        seg_lines: list[TextLine] = []
        footnotes: list[TextLine] = []

        def close() -> None:
            if not seg_lines:
                return
            sid = next(self._seg)
            for grp in group_paragraphs(seg_lines, gaps=self.gaps, is_heading_line=self.is_heading_line):
                protos.append(self.make_proto([seg_lines[k] for k in grp], pg.index, sid))
            seg_lines.clear()

        for e in entries:
            if isinstance(e, _Item):
                close()
                protos.append(_Proto(kind=e.block.kind, page=pg.index, block=e.block))
            elif e.kind == "footnote":
                footnotes.append(e)
            elif e.kind is None:
                seg_lines.append(e)
        close()
        protos.extend(self.footnote_protos(footnotes, pg.index))
        return protos

    def furniture(self, pg: _Page) -> tuple[list[_Proto], list[_Proto]]:
        head = sorted((ln for ln in pg.lines if ln.kind == "header"), key=lambda ln: (ln.y0, ln.x0))
        foot = sorted((ln for ln in pg.lines if ln.kind in ("footer", "page_number")), key=lambda ln: (ln.y0, ln.x0))
        return (
            [_Proto(kind=ln.kind or "header", page=pg.index, lines=[ln]) for ln in head],
            [_Proto(kind=ln.kind or "footer", page=pg.index, lines=[ln]) for ln in foot],
        )

    # -- headings ---------------------------------------------------------------
    def assign_levels(self, seq: list[_Proto]) -> None:
        heads = [p for p in seq if p.kind == "heading"]
        keys = sorted({p.style for p in heads}, key=lambda s: (-s[0], not s[1]))
        rank = {k: i + 1 for i, k in enumerate(keys)}
        for p in heads:
            p.level = min(6, rank[p.style])
        self.apply_toc(seq)

    def apply_toc(self, seq: list[_Proto]) -> None:
        toc = []
        for entry in self.toc or []:
            try:
                lvl, title, pno = int(entry[0]), str(entry[1]), int(entry[2])
            except (TypeError, ValueError, IndexError):
                continue
            key = _KEY_RE.sub("", normalize_text(title).lower())
            if len(key) >= 2 and pno >= 1:
                toc.append((lvl, key, pno - 1))
        if not toc:
            return
        by_page: dict[int, list[int]] = defaultdict(list)
        keys: dict[int, str] = {}
        for idx, p in enumerate(seq):
            if p.block is None and p.kind in ("heading", "paragraph") and len(p.lines) <= 4:
                by_page[p.page].append(idx)
                keys[idx] = _KEY_RE.sub("", p.text().lower())
        used: set[int] = set()
        matched: list[tuple[_Proto, int]] = []
        for lvl, key, page in toc:
            found = None
            for pnum in (page, page - 1, page + 1):
                for idx in by_page.get(pnum, []):
                    if idx in used:
                        continue
                    pk = keys[idx]
                    if not pk:
                        continue
                    if (
                        pk == key
                        or (len(key) >= 4 and pk.endswith(key) and len(pk) - len(key) <= 6)
                        or (len(key) >= 12 and (pk.startswith(key) or key.startswith(pk)) and min(len(pk), len(key)) >= 0.6 * max(len(pk), len(key)))
                    ):
                        found = idx
                        break
                if found is not None:
                    break
            if found is not None:
                used.add(found)
                matched.append((seq[found], lvl))
        if not matched:
            return
        first_idx = min(seq.index(p) for p, _ in matched)
        title_present = any(
            p.kind == "heading" and p.page == 0 and p.level == 1 and not p.toc for p in seq[:first_idx]
        )
        min_lvl = min(lvl for _, lvl in matched)
        offset = 1 - min_lvl + (1 if title_present else 0)
        style_levels: dict[tuple[float, bool], Counter[int]] = defaultdict(Counter)
        for p, lvl in matched:
            p.kind = "heading"
            p.level = max(1, min(6, lvl + offset))
            p.toc = True
            style_levels[p.style][p.level] += 1
        for p in seq:
            if p.kind == "heading" and not p.toc and p.style in style_levels:
                p.level = style_levels[p.style].most_common(1)[0][0]

    # -- continuation / references -------------------------------------------------
    def merge_continuations(self, seq: list[_Proto]) -> list[_Proto]:
        out: list[_Proto] = []
        last: _Proto | None = None
        for p in seq:
            if p.block is None and p.kind == "paragraph":
                if last is not None and self._continues(last, p):
                    last.lines.extend(p.lines)
                    continue
                out.append(p)
                last = p
                continue
            out.append(p)
            if p.kind not in _NON_FLOW:
                last = None
        return out

    def _continues(self, prev: _Proto, nxt: _Proto) -> bool:
        a, b = prev.lines[-1], nxt.lines[0]
        if prev.segment == nxt.segment and a.flow_key == b.flow_key:
            return False
        if a.ocr != b.ocr or abs(prev.style[0] - nxt.style[0]) > 0.6:
            return False
        if ends_sentence(a.text):
            return False
        first = b.text[:1]
        hyphenated = len(a.text) > 1 and a.text[-1] == "-" and a.text[-2].isalpha()
        return first.islower() or hyphenated

    def chapter_level(self, seq: list[_Proto]) -> int:
        counts = Counter(p.level for p in seq if p.kind == "heading" and p.level > 0)
        for lvl in sorted(counts):
            if counts[lvl] >= 2:
                return lvl
        return min(counts) if counts else 1

    def split_references(self, seq: list[_Proto]) -> list[_Proto]:
        out: list[_Proto] = []
        i = 0
        n = len(seq)
        while i < n:
            p = seq[i]
            if not (
                p.block is None
                and p.kind in ("heading", "paragraph")
                and len(p.lines) <= 2
                and is_reference_heading(p.text())
            ):
                out.append(p)
                i += 1
                continue
            if p.kind != "heading":
                p.kind = "heading"
                p.level = self.chapter_level(seq)
            out.append(p)
            chunk: list[TextLine] = []
            deferred: list[_Proto] = []

            def flush() -> None:
                if chunk:
                    for entry in self.reference_entries(chunk):
                        out.append(_Proto(kind="paragraph", page=entry[0].page, lines=entry))
                    chunk.clear()
                out.extend(deferred)
                deferred.clear()

            j = i + 1
            while j < n:
                q = seq[j]
                if q.kind == "heading" and q.level <= p.level and not is_reference_heading(q.text()):
                    break
                if q.block is None and q.kind in ("paragraph", "list_item"):
                    chunk.extend(q.lines)
                elif q.kind in _FURNITURE or q.kind == "footnote":
                    deferred.append(q)
                else:
                    flush()
                    out.append(q)
                j += 1
            flush()
            i = j
        return out

    def reference_entries(self, lines: list[TextLine]) -> list[list[TextLine]]:
        left: dict[tuple[int, int, int], float] = {}
        for ln in lines:
            left[ln.flow_key] = min(left.get(ln.flow_key, ln.x0), ln.x0)

        def indent(ln: TextLine) -> float:
            return ln.x0 - left[ln.flow_key]

        numbered = sum(1 for ln in lines if _REF_START_RE.match(ln.text))
        indented = sum(1 for ln in lines if indent(ln) >= 0.6 * ln.size)
        first_indent = indent(lines[0])
        if numbered >= 2:
            mode = "numbered"
        elif indented and first_indent < 0.3 * lines[0].size:
            mode = "hanging"
        elif indented and first_indent >= 0.6 * lines[0].size:
            mode = "first_indent"
        else:
            mode = "plain"
        entries: list[list[TextLine]] = []
        for k, ln in enumerate(lines):
            if k == 0:
                new = True
            elif mode == "numbered":
                new = bool(_REF_START_RE.match(ln.text))
            elif mode == "hanging":
                new = indent(ln) < 0.3 * ln.size
            elif mode == "first_indent":
                new = indent(ln) >= 0.6 * ln.size
            else:
                prev = lines[k - 1]
                gap = ln.y0 - prev.y1
                typical = self.gaps.get(size_bucket(prev.size), 0.2 * prev.size)
                new = (prev.flow_key == ln.flow_key and gap > typical + max(1.5, 0.45 * prev.size)) or (
                    (ends_sentence(prev.text) or re.search(r"(?:https?://|doi\.org/|doi:)\S+$", prev.text) is not None)
                    and _AUTHOR_START_RE.match(ln.text) is not None
                )
            if new or not entries:
                entries.append([ln])
            else:
                entries[-1].append(ln)
        return entries

    # -- blocks -----------------------------------------------------------------
    def to_block(self, p: _Proto) -> Block | None:
        if p.block is not None:
            return p.block
        lines = p.lines
        text, starts = join_lines([ln.text for ln in lines], hyphenated_words=self.vocab)
        spans: list[Span] = []
        for k, ln in enumerate(lines):
            for a, b in ln.markers:
                s, e = starts[k] + a, starts[k] + b
                if 0 <= s < e <= len(text) and text[s:e] == ln.text[a:b]:
                    spans.append(Span(s, e, "footnote_ref"))
        if p.kind == "footnote":
            spans = [s for s in spans if s.start > 0]  # the note's own number is not a reference
        if p.kind == "list_item":
            cut = bullet_prefix_length(text)
            if cut:
                text = text[cut:]
                spans = [Span(s.start - cut, s.end - cut, s.kind) for s in spans if s.start >= cut]
        if not text.strip():
            return None
        return Block(
            id="",
            kind=p.kind,
            text=text,
            level=p.level if p.kind == "heading" else 0,
            page=lines[0].page + 1,
            spans=spans,
            ocr=any(ln.ocr for ln in lines),
        )

    def run(self) -> list[Block]:
        self.classify_margins()
        ordered: list[tuple[_Page, list[Entry]]] = []
        for pg in self.pages:
            check_cancel(self.cancel)
            self.classify_figures(pg)
            entries = self.order_page(pg)
            self.mark_footnotes(pg, entries)
            ordered.append((pg, entries))
        flow_lines = [e for _, entries in ordered for e in entries if isinstance(e, TextLine) and e.kind is None]
        self.gaps = estimate_line_gaps(flow_lines)
        seq: list[_Proto] = []
        for pg, entries in ordered:
            check_cancel(self.cancel)
            head, foot = self.furniture(pg)
            seq.extend(head)
            seq.extend(self.group_page(pg, entries))
            seq.extend(foot)
        self.assign_levels(seq)
        seq = self.merge_continuations(seq)
        seq = self.split_references(seq)
        blocks = []
        for p in seq:
            b = self.to_block(p)
            if b is not None:
                blocks.append(b)
        return blocks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _pdf_date(value: str) -> str:
    m = re.match(r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value or "")
    if not m:
        return value
    y, mo, d, hh, mm, ss = (g or "" for g in m.groups())
    out = y
    if mo:
        out += f"-{mo}"
        if d:
            out += f"-{d}"
            if hh:
                out += f"T{hh}:{mm or '00'}:{ss or '00'}"
    return out


def _metadata(raw: dict[str, Any] | None) -> tuple[str | None, dict[str, str]]:
    meta: dict[str, str] = {}
    if not raw:
        return None, meta
    for src, key in (
        ("author", "author"),
        ("subject", "subject"),
        ("keywords", "keywords"),
        ("creator", "creator"),
        ("producer", "producer"),
        ("creationDate", "created"),
        ("modDate", "modified"),
    ):
        value = raw.get(src)
        if not value:
            continue
        s = normalize_text(str(value))
        if key in ("created", "modified"):
            s = _pdf_date(s)
        if s:
            meta[key] = s[:500]
    title = raw.get("title")
    return (str(title) if title else None), meta


def read_pdf(
    path: Path,
    *,
    force_ocr: bool = False,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> ReadResult:
    report = progress or Progress(None)
    report(0.0, "Åpner PDF-filen …")
    with PDF_LOCK:
        try:
            doc = pymupdf.open(str(path), filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise DocumentImportError(MSG_DAMAGED, code="damaged", details=f"pymupdf: {exc!r}") from exc
        try:
            return _read(doc, force_ocr, report, cancel)
        finally:
            doc.close()


def _read(doc: Any, force_ocr: bool, report: Progress, cancel: threading.Event | None) -> ReadResult:
    if not getattr(doc, "is_pdf", True):
        raise DocumentImportError(MSG_DAMAGED, code="damaged", details="not a PDF")
    if doc.needs_pass or doc.is_encrypted:
        raise DocumentImportError(MSG_PASSWORD, code="pdf_password", details="password required")
    perms = int(doc.permissions)
    copy_ok = bool(perms & pymupdf.PDF_PERM_COPY)
    accessibility_ok = bool(perms & pymupdf.PDF_PERM_ACCESSIBILITY)
    if not copy_ok and not accessibility_ok:
        raise DocumentImportError(MSG_COPY_PROTECTED, code="pdf_copy_protected", details=f"permissions={perms}")
    n = doc.page_count
    if n <= 0:
        raise DocumentImportError(MSG_DAMAGED, code="damaged", details="no pages")
    result = ReadResult(blocks=[], page_count=n)
    if not copy_ok:
        result.warn("copy_restricted", WARN_COPY_RESTRICTED)

    pages: list[_Page] = []
    for i in range(n):
        check_cancel(cancel)
        report(0.55 * i / n, f"Leser side {i + 1} av {n} …")
        try:
            pages.append(_extract_page(doc.load_page(i), i))
        except Exception:  # noqa: BLE001 - one damaged page must not stop the import
            pages.append(_Page(index=i, frame=Box(0.0, 0.0, 595.0, 842.0), quality=0.0))

    ocr_attempted = _run_ocr(doc, pages, force_ocr, result, report, cancel)
    check_cancel(cancel)
    report(0.9, "Analyserer oppsettet …")
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # noqa: BLE001
        toc = []
    analyzer = _Analyzer(pages, toc, cancel)
    blocks = analyzer.run()
    if not any(b.text.strip() for b in blocks):
        if ocr_attempted:
            raise DocumentImportError(MSG_NO_TEXT_AFTER_OCR, code="no_text", details="no text found, OCR attempted")
        if ocr_engine.ocr_available():
            raise DocumentImportError(MSG_NO_TEXT, code="no_text", details="no text layer")
        raise DocumentImportError(MSG_NO_TEXT_NO_OCR, code="ocr_unavailable", details="no text, OCR unavailable")
    if analyzer.unreliable_tables:
        result.warn("table_uncertain", WARN_TABLE)
    total_chars = sum(pg.chars for pg in pages)
    if not result.ocr_used and n >= 3 and total_chars < 100 * n:
        result.warn("little_text", WARN_LITTLE_TEXT)
    result.blocks = blocks
    result.title, result.metadata = _metadata(doc.metadata)
    report(1.0, "PDF-filen er lest.")
    return result
