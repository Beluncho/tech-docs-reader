"""Шаблоны маппинга из Visual Mapper: сигнатура таблицы, фингерпринт, подбор.

Маппинг = аннотации ролей (в финальных координатах) + ops-рецепт структурных
правок (merge/split/transpose/del_rows/del_cols из этапа 3.5). Шаблон
применим к таблицам с такой же структурой: сигнатура — число колонок и
нормализованные тексты шапки (header_rows, иначе первая непустая строка).
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.normalize import normalize_header
from app.preset import Table

# Порог частичного совпадения шапки (Jaccard) при равном числе колонок
TABLE_MATCH_THRESHOLD = 0.75


def _header_texts(table: Table) -> list[str]:
    """Тексты ячеек шапки: строки header_rows, иначе первая непустая строка."""
    rows = table.rows[: table.header_rows] if table.header_rows > 0 else table.rows
    texts: list[str] = []
    for row in rows:
        for cell in row:
            t = cell.text.strip()
            if t:
                texts.append(t)
        if table.header_rows <= 0 and texts:
            break  # fallback: только первая непустая строка
    return texts


def table_signature(table: Table) -> dict:
    """Сигнатура структуры таблицы (без текстов данных)."""
    return {
        "cols": table.n_cols,
        "headers": [normalize_header(t) for t in _header_texts(table) if normalize_header(t)],
    }


def compute_table_fingerprint(sig: dict) -> str:
    canonical = f"{sig['cols']}\x1f" + "\x1f".join(sig["headers"])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_templates(templates: list[dict], sig: dict) -> list[dict]:
    """Шаблоны, подходящие таблице, по убыванию сходства.

    Каждый матч: {template, score, method} — «exact» по фингерпринту
    или «partial» при равном числе колонок и Jaccard шапки ≥ порога.
    """
    fp = compute_table_fingerprint(sig)
    out: list[dict] = []
    for t in templates:
        if t.get("table_fingerprint") == fp:
            out.append({"template": t, "score": 1.0, "method": "exact"})
            continue
        t_sig = t.get("table_signature") or {}
        if t_sig.get("cols") != sig["cols"]:
            continue
        score = _jaccard(set(t_sig.get("headers") or []), set(sig["headers"]))
        if score >= TABLE_MATCH_THRESHOLD:
            out.append({"template": t, "score": round(score, 3), "method": "partial"})
    out.sort(key=lambda m: -m["score"])
    return out


def template_from_mapping(
    *,
    name: str,
    description: str,
    document_id: str,
    table: Table,
    source: dict[str, Any],
    ops: list[dict],
    annotations: list[dict],
) -> dict:
    """Собрать шаблон маппинга (без id — его назначает storage)."""
    sig = table_signature(table)
    return {
        "id": None,
        "name": name,
        "description": description,
        "created_at": None,  # storage проставит при сохранении
        "source": source,
        "table_signature": sig,
        "table_fingerprint": compute_table_fingerprint(sig),
        "ops": ops,
        "annotations": annotations,
    }
