"""Загрузчик XLSX: каждый непустой лист — таблица."""
from __future__ import annotations

from openpyxl import load_workbook

from app.loaders._base import LoadResult, Table
from app.preset import Cell


def _sheet_table(ws) -> Table:
    rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        rows.append(["" if v is None else str(v).strip() for v in row])
    while rows and not any(rows[-1]):
        rows.pop()
    while rows and not any(rows[0]):
        rows.pop(0)
    if not rows:
        return Table(id="sheet", sheet=ws.title, rows=[])
    max_cols = max(len(r) for r in rows)
    padded = [r + [""] * (max_cols - len(r)) for r in rows]
    table_rows = [[Cell(text=t, row=i, col=j) for j, t in enumerate(r)] for i, r in enumerate(padded)]
    return Table(id="sheet", sheet=ws.title, rows=table_rows)


def load(path: str, fmt: str) -> LoadResult:
    wb = load_workbook(path, read_only=True, data_only=True)
    tables: list[Table] = []
    sheet_idx = 0
    n_pages = len(wb.worksheets)
    for ws in wb.worksheets:
        t = _sheet_table(ws)
        if not t.rows:
            continue
        t.id = f"sheet{sheet_idx}"
        tables.append(t)
        sheet_idx += 1
    wb.close()
    return LoadResult(fmt="xlsx", loader="xlsx", pages=n_pages, tables=tables)