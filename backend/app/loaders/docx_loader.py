"""Загрузчик DOCX: таблицы документа + текст абзацев для key:value."""
from __future__ import annotations

import docx

from app.loaders._base import LoadResult, Table
from app.normalize import collapse_ws
from app.preset import Cell


def load(path: str) -> LoadResult:
    document = docx.Document(path)
    tables: list[Table] = []
    for idx, tbl in enumerate(document.tables):
        rows = []
        for i, row in enumerate(tbl.rows):
            cells = []
            for j, cell in enumerate(row.cells):
                text = collapse_ws(cell.text)
                cells.append(Cell(text=text, row=i, col=j))
            rows.append(cells)
        if any(any(c.text for c in r) for r in rows):
            tables.append(Table(id=f"docx{idx}", rows=rows))
    lines = [collapse_ws(p.text) for p in document.paragraphs]
    lines = [ln for ln in lines if ln]
    return LoadResult(fmt="docx", loader="docx", pages=1, tables=tables, text_lines=lines)