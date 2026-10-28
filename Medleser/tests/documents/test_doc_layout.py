"""Reading order and paragraph grouping on synthetic geometry (no PDF involved)."""

from medleser.documents.layout import (
    Box,
    TextLine,
    find_gutters,
    group_paragraphs,
    reading_order,
    sort_rows,
    to_reading_frame,
)


def column_lines(x0, x1, y0, n, step=14, h=11, tag="L"):
    return [(f"{tag}{k}", Box(x0, y0 + k * step, x1, y0 + k * step + h)) for k in range(n)]


def order_tags(items):
    boxes = [b for _, b in items]
    return [items[p.index][0] for p in reading_order(boxes)]


def test_two_columns_with_full_width_title_and_footer():
    items = [("TITLE", Box(100, 40, 500, 60)), ("ABSTRACT", Box(60, 70, 535, 82)),
             ("ABSTRACT_END", Box(60, 84, 350, 96))]
    right = column_lines(310, 535, 120, 20, tag="R")
    left = column_lines(60, 285, 120, 20, tag="L")
    items += right + left  # input order must not matter
    items.append(("FIGURE", Box(60, 420, 535, 600)))
    items += column_lines(60, 285, 610, 5, tag="l") + column_lines(310, 535, 610, 5, tag="r")
    order = order_tags(items)
    assert order[:3] == ["TITLE", "ABSTRACT", "ABSTRACT_END"]
    body = order[3:23 + 20]
    assert body == [f"L{k}" for k in range(20)] + [f"R{k}" for k in range(20)]
    assert order[43] == "FIGURE"
    assert order[44:] == [f"l{k}" for k in range(5)] + [f"r{k}" for k in range(5)]


def test_shorter_right_column_is_not_interleaved():
    items = column_lines(60, 285, 100, 30, tag="L") + column_lines(310, 535, 100, 8, tag="R")
    order = order_tags(items)
    assert order == [f"L{k}" for k in range(30)] + [f"R{k}" for k in range(8)]


def test_single_column_is_top_to_bottom():
    items = [(f"P{k}", Box(72, 100 + k * 14, 520 if k % 4 else 300, 111 + k * 14)) for k in range(25)]
    assert find_gutters([b for _, b in items]) == []
    assert order_tags(items) == [f"P{k}" for k in range(25)]


def test_right_aligned_page_numbers_are_not_a_column():
    # table of contents: entries on the left, page numbers far right
    boxes = []
    for k in range(10):
        boxes.append(Box(72, 100 + k * 14, 300, 111 + k * 14))
        boxes.append(Box(510, 100 + k * 14, 522, 111 + k * 14))
    assert find_gutters(boxes) == []
    order = [p.index for p in reading_order(boxes)]
    assert order == list(range(20))  # entry, number, entry, number …


def test_three_columns():
    items = column_lines(40, 190, 100, 6, tag="A") + column_lines(215, 365, 100, 6, tag="B") + \
        column_lines(390, 540, 100, 6, tag="C")
    assert len(find_gutters([b for _, b in items])) == 2
    assert order_tags(items) == [f"A{k}" for k in range(6)] + [f"B{k}" for k in range(6)] + [f"C{k}" for k in range(6)]


def test_sort_rows_orders_same_row_left_to_right():
    boxes = [Box(300, 101, 400, 112), Box(72, 100, 200, 111), Box(72, 120, 200, 131)]
    assert sort_rows(boxes) == [1, 0, 2]


def test_reading_frame_rotation():
    # text running downwards (dir = (0, 1)) becomes horizontal in the reading frame
    x0, y0, x1, y1 = to_reading_frame((100, 50, 112, 300), (0.0, 1.0))
    assert x1 - x0 > y1 - y0


def tl(x0, y0, x1, text, size=10.0, **kw):
    return TextLine(x0, y0, x1, y0 + size * 1.15, text, size=size, **kw)


def test_paragraphs_by_first_line_indent():
    lines = [
        tl(72, 100, 520, "Første avsnitt starter her og fortsetter over"),
        tl(72, 112, 520, "flere linjer i en rettjustert spalte med tekst"),
        tl(72, 124, 300, "og slutter her."),
        tl(90, 136, 520, "Andre avsnitt har innrykk på første linje og"),
        tl(72, 148, 400, "fortsetter uten innrykk."),
    ]
    assert group_paragraphs(lines) == [[0, 1, 2], [3, 4]]


def test_paragraphs_by_gap_size_and_bullets():
    lines = [
        tl(72, 100, 520, "Et avsnitt med vanlig linjeavstand som fortsetter"),
        tl(72, 112, 520, "på neste linje uten stor avstand mellom linjene"),
        tl(72, 124, 520, "og en tredje linje til slutt i avsnittet her"),
        tl(72, 150, 520, "Nytt avsnitt etter tydelig luft mellom avsnittene"),
        tl(72, 170, 300, "Overskrift", size=14, bold=True),
        tl(72, 190, 520, "• Et punkt i en liste"),
        tl(72, 202, 520, "• Et annet punkt"),
    ]
    assert group_paragraphs(lines) == [[0, 1, 2], [3], [4], [5], [6]]


def test_column_change_breaks_paragraph():
    a = tl(60, 700, 285, "slutten av venstre spalte")
    b = tl(310, 100, 535, "toppen av høyre spalte")
    a.column, b.column = 0, 1
    assert group_paragraphs([a, b]) == [[0], [1]]
