"""Загрузчик CSV: автоопределение разделителя и кодировки."""
from __future__ import annotations

import csv

from app.loaders._base import LoadResult, Table
from app.preset import Cell


def _read_bytes(path: str) -> str:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1251", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _sniff_delimiter(sample: str) -> str:
    counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
    return max(counts, key=counts.get) if max(counts.values()) else ","


def load(path: str) -> LoadResult:
    text = _read_bytes(path)
    delimiter = _sniff_delimiter(text[:4000])
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = [[c.strip() for c in row] for row in reader]
    while rows and not any(rows[-1]):
        rows.pop()
    if not rows:
        return LoadResult(fmt="csv", loader="csv", pages=1)
    max_cols = max(len(r) for r in rows)
    padded = [r + [""] * (max_cols - len(r)) for r in rows]
    table_rows = [[Cell(text=t, row=i, col=j) for j, t in enumerate(r)] for i, r in enumerate(padded)]
    return LoadResult(fmt="csv", loader="csv", pages=1, tables=[Table(id="csv", rows=table_rows)])