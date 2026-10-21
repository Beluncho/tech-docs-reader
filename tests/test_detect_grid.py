"""Тесты grid-детектора: merged cells (rowspan/colspan) и merge-фильтр (подэтап 2.3)."""
from app.detect import detect_tables_from_grid, merge_page_tables
from app.preset import Cell, Table

# Синтетическая сетка 4 полосы × 3 колонки: вертикали x=100..400, горизонтали y=100..500.
VX = [100, 200, 300, 400]
HY = [100, 200, 300, 400, 500]


def _vseg(x, y0, y1):
    return (x, y0, x + 0.5, y1)


def _hseg(x0, x1, y):
    return (x0, y, x1, y + 0.5)


def _word(x0, y0, text):
    return (x0, y0, x0 + len(text) * 5, y0 + 10, text)


def _grid_rects(skip_v=None, skip_h=None):
    """Все границы сетки, кроме пропущенных.

    skip_v: {(band, x)} — не рисовать вертикаль x в полосе band (colspan).
    skip_h: {(y, (x0, x1))} — не рисовать горизонталь y над колонкой [x0, x1] (rowspan).
    """
    skip_v = skip_v or set()
    skip_h = skip_h or set()
    rects = []
    for band in range(4):
        lo, hi = HY[band], HY[band + 1]
        for x in VX:
            if (band, x) not in skip_v:
                rects.append(_vseg(x, lo, hi))
    for y in HY:
        for c in range(3):
            x0, x1 = VX[c], VX[c + 1]
            if (y, (x0, x1)) not in skip_h:
                rects.append(_hseg(x0, x1, y))
    return rects


# rowspan в колонке 1 (полосы 1-2) + colspan в полосе 3 (колонки 0-1)
WORDS = [
    _word(110, 120, "H0"), _word(210, 120, "H1"), _word(310, 120, "H2"),
    _word(110, 220, "a"), _word(210, 220, "RS1"), _word(310, 220, "b"),
    _word(110, 320, "c"), _word(210, 320, "RS2"), _word(310, 320, "d"),
    _word(110, 420, "wide"), _word(310, 420, "e"),
]


def _merged_grid():
    rects = _grid_rects(skip_v={(3, 200)}, skip_h={(300, (200, 300))})
    tables = detect_tables_from_grid(WORDS, rects, 0)
    assert len(tables) == 1, f"ожидалась 1 таблица, получено {len(tables)}"
    return tables[0]


def test_grid_rowspan_and_colspan():
    t = _merged_grid()
    assert t.source == "grid"
    assert t.has_merged_cells
    assert len(t.rows) == 4
    assert t.n_cols == 3

    # rowspan: якорь (1,1) накрывает полосы 1-2, текст обеих полос объединён
    anchor = t.rows[1][1]
    assert (anchor.rowspan, anchor.colspan) == (2, 1)
    assert anchor.text == "RS1 RS2"
    cont = t.rows[2][1]
    assert cont.is_merged
    assert cont.merged_with == (1, 1)
    assert cont.text == ""

    # colspan: якорь (3,0) накрывает колонки 0-1 полосы 3
    wide = t.rows[3][0]
    assert (wide.rowspan, wide.colspan) == (1, 2)
    assert wide.text == "wide"
    assert t.rows[3][1].is_merged
    assert t.rows[3][1].merged_with == (3, 0)

    # обычные ячейки не затронуты
    assert t.rows[0][0].rowspan == 1 and t.rows[0][0].colspan == 1
    assert not t.rows[0][0].is_merged
    assert t.rows[0][0].text == "H0"
    assert t.rows[2][0].text == "c"


def test_grid_merged_get_cell_resolves_anchor():
    t = _merged_grid()
    anchor = t.rows[1][1]
    assert t.get_cell(2, 1) is anchor  # продолжение раскрывается в якорь
    assert t.get_cell(3, 1) is t.rows[3][0]
    assert t.get_cell(1, 1) is anchor
    assert t.get_cell(9, 9) is None  # вне сетки


def test_grid_cells_have_geometry():
    t = _merged_grid()
    c00 = t.rows[0][0]
    assert abs(c00.x0 - 100) < 0.5 and abs(c00.x1 - 200) < 0.5
    assert abs(c00.y0 - 100) < 0.5 and abs(c00.y1 - 200) < 0.5
    # якорь rowspan геометрически накрывает обе полосы
    anchor = t.rows[1][1]
    assert abs(anchor.y0 - 200) < 0.5 and abs(anchor.y1 - 400) < 0.5
    # продолжение имеет координаты своего слота
    cont = t.rows[2][1]
    assert cont.x0 is not None and cont.y0 is not None


def test_grid_nonrectangular_merge_fallback():
    # L-образное слияние: colspan в полосе 2 + rowspan в колонке 1 пересекаются —
    # компонента не прямоугольник, merge-метаданные не выставляются
    rects = _grid_rects(skip_v={(2, 200)}, skip_h={(300, (200, 300))})
    t = detect_tables_from_grid(WORDS, rects, 0)[0]
    assert not t.has_merged_cells
    assert t.rows[1][1].rowspan == 1 and not t.rows[1][1].is_merged
    assert t.rows[2][0].text == "c"
    assert t.rows[2][1].text == "RS2"  # текст остался в своей ячейке


def _mk_table(text_rows, bbox, source="word", has_merged=False):
    rows = [
        [Cell(text=t, row=i, col=j, page=0) for j, t in enumerate(r)]
        for i, r in enumerate(text_rows)
    ]
    return Table(id="x", rows=rows, bbox=bbox, source=source, has_merged_cells=has_merged)


def test_merge_keeps_grid_with_merged_cells():
    # grid-таблица без кодов моделей в шапке (не transposed), но с merged cells —
    # принимается, словесная таблица рядом сохраняется
    grid = _merged_grid()
    far = _mk_table([["a", "b"], ["c", "d"]], [0.0, 600.0, 200.0, 700.0])
    out = merge_page_tables(0, [grid], [far])
    assert any(t is grid for t in out)
    assert any(t is far for t in out)


def test_merge_drops_word_piece_covered_by_grid():
    grid = _merged_grid()
    piece = _mk_table([["a", "b"], ["c", "d"]], [200.0, 200.0, 300.0, 300.0])
    out = merge_page_tables(0, [grid], [piece])
    assert len(out) == 1 and out[0] is grid


def test_merge_drops_grid_duplicated_by_word():
    grid = _merged_grid()
    big = _mk_table([["a", "b", "c"], ["d", "e", "f"]], [90.0, 90.0, 410.0, 510.0])
    out = merge_page_tables(0, [grid], [big])
    assert len(out) == 1 and out[0] is big


def test_merge_drops_sparse_merged_grid():
    # разреженная сетка (фоновая разлиновка): has_merged_cells=True, но текста
    # почти нет — не принимается (плотность < GRID_MERGE_DENSITY)
    grid = _merged_grid()
    sparse_rows = [["" for _ in range(5)] for _ in range(30)]
    sparse_rows[0][0] = "x"
    sparse_rows[1][1] = "y"
    sparse = _mk_table(sparse_rows, grid.bbox, source="grid", has_merged=True)
    out = merge_page_tables(0, [sparse], [])
    assert out == []


def test_merge_drops_plain_grid_without_content_sign():
    # grid без merged cells и не transposed — как раньше, не принимается
    plain = _mk_table([["a", "b", "c"], ["d", "e", "f"]], [100.0, 100.0, 400.0, 200.0], source="grid")
    out = merge_page_tables(0, [plain], [])
    assert out == []
