"""Pure page-geometry helpers: reading order and paragraph grouping.

Nothing here knows about PDF libraries; all functions work on simple boxes
(anything with ``x0, y0, x1, y1``) or :class:`TextLine` objects, so the
heuristics can be unit-tested with synthetic coordinates. Coordinates follow
PDF/screen convention: x grows to the right, y grows downwards.

Reading order («column-aware XY-cut»)
-------------------------------------
1. :func:`find_gutters` looks for vertical white-space channels between
   columns. Only "narrow" boxes (narrower than ~60 % of the text width) vote,
   so full-width titles/abstracts/figures do not hide the gutter. A channel
   counts as a gutter when it has enough content on both sides, the two
   sides overlap vertically and each side is reasonably wide (a column of
   right-aligned page numbers in a table of contents is not a column).
2. :func:`reading_order` marks boxes that cover a whole gutter as
   *spanning* (title, abstract, full-width figure …). Walking the page top
   to bottom, consecutive spanning boxes form a full-width band; the
   non-spanning boxes between two full-width bands form a column band that is
   read column by column (left column top→bottom, then the right column).
   Lines are therefore never interleaved across columns, and full-width
   content above/below/between column blocks stays in place. The short last
   line of a full-width paragraph (which does not reach across the gutter)
   is kept with its paragraph.

Paragraphs
----------
:func:`group_paragraphs` splits an ordered list of lines (one text flow) into
paragraphs. A new paragraph starts at a column/band change, a font-size or
bold change, a vertical gap clearly larger than the usual line gap for that
font size, a list marker or caption label, a first-line indent after a short
or sentence-final line, or after a clearly short line that ends a sentence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .cleanup import bullet_prefix_length, ends_sentence, is_caption, starts_with_enumeration


class BoxLike(Protocol):
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class TextLine:
    """One line of text with geometry and typography (library independent)."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float = 10.0
    bold: bool = False
    italic: bool = False
    page: int = 0  # 0-based page index
    markers: list[tuple[int, int]] = field(default_factory=list)  # footnote markers in ``text``
    ocr: bool = False
    upright: bool = True  # False for rotated/vertical text
    kind: str | None = None  # pre-classification by the reader
    band: int = 0  # filled by reading_order
    column: int = 0
    font: str = ""

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def flow_key(self) -> tuple[int, int, int]:
        return (self.page, self.band, self.column)


@dataclass(frozen=True)
class Placement:
    index: int  # index into the input sequence
    band: int  # horizontal band (0 = top of the page)
    column: int  # column inside the band (0 = left); 0 for full-width bands
    spanning: bool  # True when the band is full-width


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def median(values: Sequence[float], default: float = 0.0) -> float:
    vals = sorted(values)
    if not vals:
        return default
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def percentile(values: Sequence[float], q: float, default: float = 0.0) -> float:
    vals = sorted(values)
    if not vals:
        return default
    k = max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))
    return float(vals[k])


def weighted_median(values: Sequence[float], weights: Sequence[float], default: float = 0.0) -> float:
    pairs = sorted((v, w) for v, w in zip(values, weights) if w > 0)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return default
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2.0:
            return float(v)
    return float(pairs[-1][0])


def vertical_overlap(a: BoxLike, b: BoxLike) -> float:
    return min(a.y1, b.y1) - max(a.y0, b.y0)


def horizontal_overlap(a: BoxLike, b: BoxLike) -> float:
    return min(a.x1, b.x1) - max(a.x0, b.x0)


def center_inside(inner: BoxLike, outer: BoxLike, tol: float = 0.0) -> bool:
    cx = (inner.x0 + inner.x1) / 2.0
    cy = (inner.y0 + inner.y1) / 2.0
    return outer.x0 - tol <= cx <= outer.x1 + tol and outer.y0 - tol <= cy <= outer.y1 + tol


def overlap_fraction(inner: BoxLike, outer: BoxLike) -> float:
    """Share of ``inner``'s area that lies inside ``outer``."""
    w = horizontal_overlap(inner, outer)
    h = vertical_overlap(inner, outer)
    area = max(1e-6, (inner.x1 - inner.x0) * (inner.y1 - inner.y0))
    if w <= 0 or h <= 0:
        return 0.0
    return min(1.0, (w * h) / area)


def union_box(boxes: Sequence[BoxLike]) -> Box:
    return Box(
        min(b.x0 for b in boxes),
        min(b.y0 for b in boxes),
        max(b.x1 for b in boxes),
        max(b.y1 for b in boxes),
    )


def to_reading_frame(
    bbox: tuple[float, float, float, float], direction: tuple[float, float]
) -> tuple[float, float, float, float]:
    """Rotate ``bbox`` so that text written in ``direction`` runs left→right.

    ``direction`` is the writing direction (cos, sin) as reported by PDF
    text extraction. Used for pages whose text is mostly rotated (landscape
    tables etc.), so the same column logic can be applied.
    """
    dx, dy = direction
    norm = math.hypot(dx, dy) or 1.0
    c, s = dx / norm, dy / norm
    xs, ys = [], []
    for x, y in ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[0], bbox[3]), (bbox[2], bbox[3])):
        xs.append(x * c + y * s)
        ys.append(-x * s + y * c)
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Rows / gutters / reading order
# ---------------------------------------------------------------------------


def sort_rows(boxes: Sequence[BoxLike], indices: Sequence[int] | None = None) -> list[int]:
    """Order boxes top→bottom; boxes on the same visual row left→right."""
    idx = list(range(len(boxes))) if indices is None else list(indices)
    idx.sort(key=lambda i: (boxes[i].y0, boxes[i].x0))
    rows: list[tuple[float, float, list[int]]] = []
    for i in idx:
        b = boxes[i]
        if rows:
            ry0, ry1, members = rows[-1]
            h = min(b.y1 - b.y0, ry1 - ry0)
            if h > 0 and min(ry1, b.y1) - max(ry0, b.y0) >= 0.5 * h:
                members.append(i)
                continue
        rows.append((b.y0, b.y1, [i]))
    out: list[int] = []
    for _, _, members in rows:
        out.extend(sorted(members, key=lambda i: boxes[i].x0))
    return out


def find_gutters(
    boxes: Sequence[BoxLike], *, min_gutter: float | None = None
) -> list[tuple[float, float]]:
    """Return vertical white-space channels ``(x0, x1)`` that separate columns."""
    n = len(boxes)
    if n < 4:
        return []
    rx0 = min(b.x0 for b in boxes)
    rx1 = max(b.x1 for b in boxes)
    width = rx1 - rx0
    if width <= 1:
        return []
    mh = median([b.y1 - b.y0 for b in boxes], 10.0)
    if min_gutter is None:
        min_gutter = max(4.0, 0.45 * mh)
    narrow = [b for b in boxes if (b.x1 - b.x0) <= 0.62 * width]
    if len(narrow) < 4:
        return []

    step = max(0.5, width / 4000.0)
    nbins = int(math.ceil(width / step)) + 2
    diff = [0] * (nbins + 1)
    for b in narrow:
        s = max(0, int((b.x0 - rx0) / step))
        e = min(nbins, int(math.ceil((b.x1 - rx0) / step)))
        diff[s] += 1
        diff[e] -= 1
    cov = []
    acc = 0
    for k in range(nbins):
        acc += diff[k]
        cov.append(acc)
    # A few stray boxes may cross a real gutter (the short last line of a
    # full-width abstract, an equation, an affiliation line …).
    tol = max(1, int(0.08 * max(cov)))

    runs: list[tuple[int, int]] = []
    k = 0
    while k < nbins:
        if cov[k] <= tol:
            s = k
            while k < nbins and cov[k] <= tol:
                k += 1
            runs.append((s, k))
        else:
            k += 1

    gutters: list[tuple[float, float]] = []
    for s, e in runs:
        g0 = rx0 + s * step
        g1 = rx0 + e * step
        if g0 <= rx0 + step or g1 >= rx1 - step:
            continue  # margin, not between columns
        if g1 - g0 < min_gutter:
            continue
        left = [b for b in narrow if (b.x0 + b.x1) / 2 < g0]
        right = [b for b in narrow if (b.x0 + b.x1) / 2 > g1]
        if len(left) < 2 or len(right) < 2:
            continue
        lw = max(b.x1 for b in left) - min(b.x0 for b in left)
        rw = max(b.x1 for b in right) - min(b.x0 for b in right)
        if lw < 0.15 * width or rw < 0.15 * width:
            continue
        ly0, ly1 = min(b.y0 for b in left), max(b.y1 for b in left)
        ry0, ry1 = min(b.y0 for b in right), max(b.y1 for b in right)
        ov = min(ly1, ry1) - max(ly0, ry0)
        if ov < 0.25 * min(ly1 - ly0, ry1 - ry0):
            continue
        gutters.append((g0, g1))
    return gutters


def reading_order(boxes: Sequence[BoxLike], *, min_gutter: float | None = None) -> list[Placement]:
    """Column-aware reading order for the boxes of one page (see module doc)."""
    n = len(boxes)
    if n == 0:
        return []
    gutters = find_gutters(boxes, min_gutter=min_gutter)
    if not gutters:
        return [Placement(i, 0, 0, True) for i in sort_rows(boxes)]

    def covers(b: BoxLike, g: tuple[float, float]) -> bool:
        return b.x0 < g[0] - 1.0 and b.x1 > g[1] + 1.0

    spanning = [any(covers(b, g) for g in gutters) for b in boxes]
    column = [sum(1 for g in gutters if (b.x0 + b.x1) / 2 > (g[0] + g[1]) / 2) for b in boxes]

    # A narrow box directly below a spanning box with nothing beside it in other
    # columns belongs to the full-width paragraph (e.g. its short last line).
    mh = median([b.y1 - b.y0 for b in boxes], 10.0)
    span_idx = [j for j in range(n) if spanning[j]]
    if span_idx:
        promote = []
        for i, b in enumerate(boxes):
            if spanning[i]:
                continue
            above = any(
                boxes[j].y1 <= b.y0 + 0.5 * mh
                and b.y0 - boxes[j].y1 <= 1.2 * mh
                and horizontal_overlap(b, boxes[j]) > 0
                for j in span_idx
            )
            if not above:
                continue
            neighbours = any(
                not spanning[j] and column[j] != column[i] and vertical_overlap(b, boxes[j]) > -mh
                for j in range(n)
            )
            if not neighbours:
                promote.append(i)
        for i in promote:
            spanning[i] = True

    order = sorted(range(n), key=lambda i: (boxes[i].y0, boxes[i].x0))
    result: list[Placement] = []
    band = 0
    run: list[int] = []
    run_spanning: bool | None = None

    def flush() -> None:
        nonlocal band
        if not run:
            return
        if run_spanning:
            for i in sort_rows(boxes, run):
                result.append(Placement(i, band, 0, True))
        else:
            by_col: dict[int, list[int]] = defaultdict(list)
            for i in run:
                by_col[column[i]].append(i)
            for c in sorted(by_col):
                for i in sort_rows(boxes, by_col[c]):
                    result.append(Placement(i, band, c, False))
        band += 1
        run.clear()

    for i in order:
        if run_spanning is None or spanning[i] != run_spanning:
            flush()
            run_spanning = spanning[i]
        run.append(i)
    flush()
    return result


# ---------------------------------------------------------------------------
# Paragraphs
# ---------------------------------------------------------------------------


def size_bucket(size: float) -> float:
    return round(size * 2.0) / 2.0


def estimate_line_gaps(lines: Sequence[TextLine]) -> dict[float, float]:
    """Typical vertical gap between consecutive lines of one paragraph, per font size.

    Uses the 30th percentile of the gaps between consecutive lines in the
    same flow with the same font size, so paragraph spacing does not inflate
    the estimate.
    """
    gaps: dict[float, list[float]] = defaultdict(list)
    for a, b in zip(lines, lines[1:]):
        if a.flow_key != b.flow_key or a.kind or b.kind:
            continue
        if abs(a.size - b.size) > 0.5:
            continue
        g = b.y0 - a.y1
        if -0.6 * a.size <= g <= 2.5 * a.size:
            gaps[size_bucket(a.size)].append(g)
    return {k: percentile(v, 0.3) for k, v in gaps.items() if len(v) >= 3}


def _gap_threshold(size: float, gaps: dict[float, float]) -> float:
    typical = gaps.get(size_bucket(size))
    if typical is None:
        typical = 0.2 * size
    return typical + max(1.5, 0.45 * size)


def group_paragraphs(
    lines: Sequence[TextLine],
    *,
    gaps: dict[float, float] | None = None,
    is_heading_line: Callable[[TextLine], bool] | None = None,
) -> list[list[int]]:
    """Split ``lines`` (in reading order) into paragraphs (lists of indices)."""
    if not lines:
        return []
    if gaps is None:
        gaps = estimate_line_gaps(lines)

    # column bounds per flow
    bounds: dict[tuple[int, int, int], tuple[float, float]] = {}
    for ln in lines:
        key = ln.flow_key
        if key in bounds:
            l0, r0 = bounds[key]
            bounds[key] = (min(l0, ln.x0), max(r0, ln.x1))
        else:
            bounds[key] = (ln.x0, ln.x1)

    groups: list[list[int]] = [[0]]
    for i in range(1, len(lines)):
        a, b = lines[i - 1], lines[i]
        if _is_break(a, b, bounds[b.flow_key], gaps, is_heading_line):
            groups.append([i])
        else:
            groups[-1].append(i)
    return groups


def _is_break(
    a: TextLine,
    b: TextLine,
    bound: tuple[float, float],
    gaps: dict[float, float],
    is_heading_line: Callable[[TextLine], bool] | None,
) -> bool:
    if a.flow_key != b.flow_key:
        return True
    if a.kind != b.kind:
        return True
    h = min(a.height, b.height)
    same_row = h > 0 and vertical_overlap(a, b) >= 0.5 * h
    if same_row:
        # second segment of the same visual line (e.g. split by a wide space)
        return b.x0 < a.x0
    if b.y0 < a.y0:
        return True  # moved up: different region
    big = max(a.size, b.size)
    # OCR sizes are estimated from word boxes and jitter from line to line
    size_tol = max(1.5, 0.25 * big) if (a.ocr and b.ocr) else max(0.6, 0.08 * big)
    if abs(a.size - b.size) > size_tol:
        return True
    if a.bold != b.bold:
        return True
    if is_heading_line is not None and (is_heading_line(a) != is_heading_line(b)):
        return True
    gap = b.y0 - a.y1
    if gap > _gap_threshold(a.size, gaps):
        return True
    if bullet_prefix_length(b.text) or starts_with_enumeration(b.text) or is_caption(b.text):
        return True
    left, right = bound
    width = max(1.0, right - left)
    a_short = a.x1 < right - max(1.5 * a.size, 0.08 * width)
    b_center = (b.x0 + b.x1) / 2.0
    b_centered = abs(b_center - (left + right) / 2.0) < 0.06 * width and b.x0 - left > 2 * b.size
    indent = b.x0 - a.x0
    if not b_centered and 0.6 * b.size <= indent <= 0.4 * width:
        if a_short or ends_sentence(a.text):
            return True
    if ends_sentence(a.text) and a.x1 < right - 0.35 * width:
        return True
    return False
