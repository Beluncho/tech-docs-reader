"""Модель «набора» (пресета), фингерпринты и подбор набора под документ."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.normalize import looks_numeric, normalize_header, normalize_text

# Порог частичного совпадения (Jaccard), как в фазе 2
PARTIAL_MATCH_THRESHOLD = 0.45


@dataclass
class Cell:
    text: str
    row: int
    col: int
    page: int | None = None
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None
    # Merged cells: якорная ячейка имеет rowspan/colspan > 1,
    # клетки-продолжения — is_merged=True со ссылкой на якорь.
    rowspan: int = 1
    colspan: int = 1
    is_merged: bool = False
    merged_with: tuple[int, int] | None = None  # (row, col) якорной ячейки

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "row": self.row,
            "col": self.col,
            "page": self.page,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "is_merged": self.is_merged,
            "merged_with": list(self.merged_with) if self.merged_with else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cell":
        merged_with = d.get("merged_with")
        return cls(
            text=d["text"],
            row=d["row"],
            col=d["col"],
            page=d.get("page"),
            x0=d.get("x0"),
            y0=d.get("y0"),
            x1=d.get("x1"),
            y1=d.get("y1"),
            rowspan=d.get("rowspan", 1) or 1,
            colspan=d.get("colspan", 1) or 1,
            is_merged=bool(d.get("is_merged", False)),
            merged_with=tuple(merged_with) if merged_with else None,
        )


@dataclass
class Table:
    id: str
    rows: list[list[Cell]]
    sheet: str | None = None
    page: int | None = None
    header_row: int | None = None
    bbox: list[float | None] = field(default_factory=list)
    source: str | None = None  # "word" / "grid" / None
    # Метаинформация о структуре (этап 2)
    header_rows: int = 0  # количество строк заголовка (0 = не определено)
    has_merged_cells: bool = False
    is_matrix: bool = False  # матрица модель×параметр
    orientation: str | None = None  # "rows" (модели в строках) / "columns" (в столбцах)
    grid_rows: int = 0
    grid_cols: int = 0

    def __post_init__(self) -> None:
        if not self.grid_rows:
            self.grid_rows = len(self.rows)
        if not self.grid_cols:
            self.grid_cols = self.n_cols

    @property
    def n_cols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    @property
    def has_header(self) -> bool:
        return self.header_rows > 0 or self.header_row is not None

    def get_cell(self, row: int, col: int) -> Cell | None:
        """Ячейка с раскрытием merged: клетка-продолжение возвращает якорь."""
        seen: set[tuple[int, int]] = set()
        while True:
            if (row, col) in seen:
                return None  # защита от циклических ссылок
            seen.add((row, col))
            if not (0 <= row < len(self.rows)):
                return None
            r = self.rows[row]
            if not (0 <= col < len(r)):
                return None
            cell = r[col]
            if not (cell.is_merged and cell.merged_with is not None):
                return cell
            row, col = cell.merged_with

    def iter_data_rows(self):
        """Итерация по строкам данных (после заголовка)."""
        start = self.header_rows if self.header_rows > 0 else 0
        for row in self.rows[start:]:
            if any(c.text.strip() for c in row):
                yield row

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sheet": self.sheet,
            "page": self.page,
            "header_row": self.header_row,
            "bbox": self.bbox,
            "source": self.source,
            "header_rows": self.header_rows,
            "has_merged_cells": self.has_merged_cells,
            "is_matrix": self.is_matrix,
            "orientation": self.orientation,
            "grid_rows": self.grid_rows,
            "grid_cols": self.grid_cols,
            "rows": [[c.to_dict() for c in row] for row in self.rows],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Table":
        rows = [[Cell.from_dict(c) for c in row] for row in d.get("rows", [])]
        return cls(
            id=d["id"],
            sheet=d.get("sheet"),
            page=d.get("page"),
            header_row=d.get("header_row"),
            bbox=d.get("bbox", []),
            rows=rows,
            source=d.get("source"),
            header_rows=d.get("header_rows", 0) or 0,
            has_merged_cells=bool(d.get("has_merged_cells", False)),
            is_matrix=bool(d.get("is_matrix", False)),
            orientation=d.get("orientation"),
            grid_rows=d.get("grid_rows", 0) or 0,
            grid_cols=d.get("grid_cols", 0) or 0,
        )


# --- сигнатура документа ---------------------------------------------------

HEADER_VOCAB = {
    "наименование", "показатель", "характеристика", "параметр", "значение", "величина",
    "ед. изм.", "единица измерения", "единица", "кол-во", "количество", "номер", "п/п",
    "норма", "допуск", "тип", "марка", "модель",
}


def _has_header_vocab(texts: list[str]) -> bool:
    norm = {normalize_header(t) for t in texts}
    for v in HEADER_VOCAB:
        for t in norm:
            if t == v or (v and (v in t or t in v)):
                return True
    return False


def candidate_header_row(table: Table, max_scan: int = 8) -> int | None:
    """Строка-шапка: предпочтение строке со словарными заголовками, иначе первая текстовая."""
    for i, row in enumerate(table.rows[:max_scan]):
        texts = [c.text.strip() for c in row]
        if not any(texts):
            continue
        if any(looks_numeric(t) for t in texts):
            continue
        if _has_header_vocab(texts) and all(0 < len(t) <= 60 for t in texts if t):
            return i
    for i, row in enumerate(table.rows[:4]):
        texts = [c.text.strip() for c in row]
        if not any(texts):
            continue
        if any(looks_numeric(t) for t in texts):
            continue
        if all(0 < len(t) <= 60 for t in texts if t):
            return i
    return None


def first_col_labels(table: Table, limit: int = 12) -> list[str]:
    out: list[str] = []
    for row in table.rows:
        if not row:
            continue
        t = row[0].text.strip()
        if not t or looks_numeric(t) or len(t) > 90:
            continue
        out.append(normalize_header(t))
        if len(out) >= limit:
            break
    return out


def document_signature(loader: str, tables: list[Table]) -> dict:
    sigs = []
    for t in tables:
        hr = candidate_header_row(t)
        sigs.append(
            {
                "id": t.id,
                "cols": t.n_cols,
                "headers": [normalize_header(c.text) for c in t.rows[hr]] if hr is not None else [],
                "first_col_labels": first_col_labels(t),
            }
        )
    return {"loader": loader, "tables": sigs}


# --- фингерпринты ----------------------------------------------------------

def compute_exact_fingerprint(loader: str, signature: dict) -> str:
    parts = []
    for sig in sorted(signature["tables"], key=lambda s: s["id"]):
        parts.append(f"{sig['cols']}|" + "\x1f".join(sig["headers"]))
    canonical = loader + "\x1e" + "\x1d".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _table_score(preset: dict, sig: dict) -> float:
    pcols = preset.get("columns", {}).get("count")
    col_factor = 1.0 if (pcols is None or pcols == sig["cols"]) else 0.3
    header_set = {normalize_header(h) for h in preset.get("header_keywords", [])}
    header_set |= {normalize_header(h) for h in preset.get("columns", {}).get("header", {})}
    label_set = {normalize_header(l) for l in preset.get("labels", [])}
    h = _jaccard(header_set, set(sig["headers"]))
    l = _jaccard(label_set, set(sig["first_col_labels"]))
    return col_factor * (0.35 * h + 0.65 * l)


def match_presets(
    presets: list[dict], loader: str, signature: dict
) -> tuple[dict | None, float, str, str | None]:
    """Вернуть (пресет, score, способ, table_id) — точный фингерпринт или частичное совпадение."""
    exact_fp = compute_exact_fingerprint(loader, signature)
    best: tuple[dict | None, float, str, str | None] = (None, 0.0, "none", None)
    for p in presets:
        if p.get("fingerprint") == exact_fp:
            return p, 1.0, "exact", None
        score, method = 0.0, "partial"
        best_table: str | None = None
        for sig in signature["tables"]:
            s = _table_score(p, sig)
            if s > score:
                score = s
                best_table = sig["id"]
        if score > best[1]:
            best = (p, score, method, best_table)
    preset, score, method, table_id = best
    if preset is None or score < PARTIAL_MATCH_THRESHOLD:
        return None, score, "none", None
    return preset, score, method, table_id