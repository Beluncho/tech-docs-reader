# -*- coding: utf-8 -*-
"""Подэтап 2.6: интеграция header_rows / merged cells в извлечение и API."""
from app.api.routes import _table_out
from app.extract import extract_deterministic, extract_from_table_free
from app.loaders._base import LoadResult
from app.preset import Cell, Table


def _mk_table(rows_text, tid="t1", **kw):
    rows = [
        [Cell(text=a, row=i, col=0), Cell(text=b, row=i, col=1)]
        for i, (a, b) in enumerate(rows_text)
    ]
    return Table(id=tid, rows=rows, **kw)


PRESET_2COL = {
    "columns": {"count": 2, "roles": {"label": 0, "value": 1}},
    "labels": ["подача"],
    "units": ["м3/ч", "мм"],
}


# --- header_rows: автошапка пропускается при извлечении ----------------------


def test_extract_skips_auto_header_rows():
    # матрица: в шапке «Подача | 100 мм» — ложная пара; данные со строки 1
    t = _mk_table([("Подача", "100 мм"), ("Подача", "12,5 м3/ч")],
                  header_rows=1, is_matrix=True)
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[t])
    params, _w, _tid = extract_deterministic(lr, PRESET_2COL, "doc.pdf", include_all=True)
    assert [p.value for p in params] == ["12,5"]


def test_extract_keyvalue_word_table_ignores_auto_header():
    # key-value word-таблица: автошапка ненадёжна (граница «метка + 1 значение»),
    # строки не пропускаем — иначе теряются реальные параметры
    t = _mk_table(
        [("Диапазон температур, °С", "-30 ... +80"),
         ("Вязкость, сСт", "25 ... 2320"),
         ("Вес, кг", "2,2")],
        header_rows=2,  # ложное срабатывание эвристики 2.5
    )
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[t])
    params, _w, _tid = extract_deterministic(
        lr, {**PRESET_2COL, "labels": ["диапазон температур", "вязкость", "вес"],
             "include_all": True},
        "doc.pdf", include_all=True,
    )
    assert len(params) == 3


def test_free_skips_auto_header_rows():
    t = _mk_table([("Габариты", "800 мм"), ("Масса", "120 кг")],
                  header_rows=1, has_merged_cells=True)
    params = extract_from_table_free(t, {}, "doc.pdf")
    assert [p.name for p in params] == ["Масса"]
    assert params[0].value == "120"


# --- merged cells: rowspan-метка действует на все накрытые строки -----------


def _rowspan_table():
    """«Подача» (rowspan=2) покрывает две строки со своими значениями."""
    anchor = Cell(text="Подача", row=0, col=0, rowspan=2)
    cont = Cell(text="", row=1, col=0, is_merged=True, merged_with=(0, 0))
    return Table(
        id="t1",
        rows=[
            [anchor, Cell(text="12,5 м3/ч", row=0, col=1)],
            [cont, Cell(text="16 м3/ч", row=1, col=1)],
        ],
    )


def test_extract_rowspan_label_covers_rows():
    lr = LoadResult(fmt="pdf", loader="pdf", pages=1, tables=[_rowspan_table()])
    params, _w, _tid = extract_deterministic(lr, PRESET_2COL, "doc.pdf")
    assert [(p.name, p.value) for p in params] == [("Подача", "12,5"), ("Подача", "16")]


def test_free_rowspan_not_resolved():
    """В свободном режиме merged НЕ раскрываются: якорь rowspan-блока (например
    «1 шт.» на весь блок формы) не должен добавлять лишние значения в строку
    (иначе строка отбрасывается как матричная) — grid-таблицы обрабатывает
    вариант Б (matrix.py)."""
    params = extract_from_table_free(_rowspan_table(), {}, "doc.pdf")
    # строка-продолжение без своей метки не извлекается
    assert [(p.name, p.value) for p in params] == [("Подача", "12,5")]


# --- API: метаданные таблицы/ячеек в TableOut/CellOut ------------------------


def test_table_out_metadata():
    t = _rowspan_table()
    t.header_rows = 1
    t.is_matrix = True
    t.orientation = "rows"
    t.has_merged_cells = True
    out = _table_out(t)
    assert out.header_rows == 1
    assert out.is_matrix is True
    assert out.orientation == "rows"
    assert out.has_merged_cells is True
    anchor_out = next(c for c in out.cells if c.row == 0 and c.col == 0)
    cont_out = next(c for c in out.cells if c.row == 1 and c.col == 0)
    assert anchor_out.rowspan == 2
    assert cont_out.is_merged is True
    assert cont_out.merged_with == [0, 0]
    # round-trip через from_dict: merged_with возвращается кортежем
    t2 = Table.from_dict(t.to_dict())
    assert t2.rows[1][0].merged_with == (0, 0)
    cont2 = next(c for c in _table_out(t2).cells if c.row == 1 and c.col == 0)
    assert cont2.merged_with == [0, 0]
