# -*- coding: utf-8 -*-
"""Подэтап 2.8: тесты утилиты визуализации детекции таблиц.

Роли ячеек (merged/шапка/ось матрицы), парсер спецификации страниц,
HTML-отчёт и сквозной прогон на синтетическом PDF (fitz).
"""
import base64

import fitz
import pytest

from app.preset import Cell, Table
from scripts.visualize_table_detection import (
    ROLE_LEGEND,
    build_html,
    cell_role,
    detect_page_tables,
    parse_pages,
    table_summary,
    visualize_page,
)


def _table(**kw) -> Table:
    rows = kw.pop("rows", [])
    return Table(id=kw.pop("id", "p1-t1"), rows=rows, bbox=kw.pop("bbox", [0, 0, 100, 100]), **kw)


# --- роли ячеек ---------------------------------------------------------------


def test_cell_role_merged_anchor_and_continuation():
    """Якорь rowspan — «merged», клетка-продолжение не рисуется (None)."""
    anchor = Cell(text="подача", row=1, col=2, rowspan=18, colspan=1,
                  x0=10, y0=10, x1=20, y1=100)
    cont = Cell(text="", row=5, col=2, is_merged=True, merged_with=(1, 2),
                x0=10, y0=30, x1=20, y1=40)
    t = _table(rows=[[anchor], [cont]])
    assert cell_role(anchor, t) == "merged"
    assert cell_role(cont, t) is None


def test_cell_role_header_matrix_axis_normal():
    """Порядок приоритета: merged > шапка > ось матрицы > обычная."""
    hdr = Cell(text="Обозначение", row=0, col=0)
    model = Cell(text="НС 13-70", row=1, col=0)
    value = Cell(text="70", row=1, col=1)
    t = _table(rows=[[hdr], [model], [value]],
               header_rows=1, is_matrix=True, orientation="rows")
    assert cell_role(hdr, t) == "header"
    assert cell_role(model, t) == "matrix_axis"
    assert cell_role(value, t) == "normal"


def test_cell_role_no_matrix_no_header():
    c = Cell(text="x", row=0, col=0)
    t = _table(rows=[[c]])
    assert cell_role(c, t) == "normal"


# --- парсер спецификации страниц ----------------------------------------------


def test_parse_pages_single_and_range():
    assert parse_pages("6", 10) == [6]
    assert parse_pages("6,9-11", 20) == [6, 9, 10, 11]
    assert parse_pages(" 3 , 1 ", 5) == [1, 3]  # сортировка + пробелы


def test_parse_pages_reversed_range():
    assert parse_pages("11-9", 20) == [9, 10, 11]


def test_parse_pages_out_of_range():
    with pytest.raises(ValueError):
        parse_pages("6,42", 10)
    with pytest.raises(ValueError):
        parse_pages("0", 10)


def test_parse_pages_garbage():
    with pytest.raises(ValueError):
        parse_pages("6,x", 10)


# --- сводка и HTML-отчёт ------------------------------------------------------


def test_table_summary():
    t = _table(rows=[[Cell(text="a", row=0, col=0)]], source="grid",
               header_rows=2, has_merged_cells=True,
               is_matrix=True, orientation="columns")
    t.grid_rows, t.grid_cols = 5, 4
    s = table_summary(t)
    assert s == {"id": "p1-t1", "source": "grid", "size": "5x4",
                 "header_rows": 2, "merged": "да", "matrix": "columns"}


def test_build_html_legend_and_images(tmp_path):
    png = tmp_path / "page-006.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    t = _table(rows=[[Cell(text="a", row=0, col=0)]], source="word", header_rows=1)
    html = build_html([{"page": 6, "png": str(png), "tables": [t]}])
    assert "Страница 6" in html
    assert base64.b64encode(png.read_bytes()).decode("ascii") in html
    for legend in ROLE_LEGEND.values():
        assert legend in html
    assert "p1-t1" in html and "word" in html


def test_build_html_empty_page():
    html = build_html([{"page": 1, "png": "x.png", "tables": []}])
    assert "Таблицы не детектированы" in html


# --- сквозной прогон на синтетическом PDF -------------------------------------


@pytest.fixture()
def synth_pdf(tmp_path):
    """Мини-таблица 4x3 (12 слов) — словесный детектор обязан её найти."""
    path = tmp_path / "synth.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for r, y in enumerate((60, 90, 120, 150)):
        for c, x in enumerate((50, 150, 250)):
            page.insert_text((x, y), f"c{r}{c}")
    doc.save(str(path))
    doc.close()
    return str(path)


def test_visualize_page_smoke(synth_pdf, tmp_path):
    tables = detect_page_tables(synth_pdf, 0)
    assert len(tables) >= 1
    out = tmp_path / "page-001.png"
    visualize_page(synth_pdf, 0, tables, str(out), dpi=72)
    assert out.read_bytes().startswith(b"\x89PNG")


def test_visualize_page_no_tables(synth_pdf, tmp_path):
    """Страница без таблиц рендерится как есть (пустая детекция — тоже отладка)."""
    out = tmp_path / "page-001.png"
    visualize_page(synth_pdf, 0, [], str(out), dpi=72)
    assert out.stat().st_size > 0
