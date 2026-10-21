# -*- coding: utf-8 -*-
"""Подэтап 3.8: применение маппинга на backend (ops-рецепт + предпросмотр).

mapping_apply.py — порт table-ops.js / preview.js; тесты проверяют ту же
семантику, что и клиентский харнесс (включая кросс-чек на НС p6-t1:
18 карточек × 7 параметров = 126 записей из 45 value-ячеек, 0 предупреждений).
"""
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.mapping_apply import (
    apply_ops,
    build_preview,
    delete_cols,
    delete_rows,
    merge_rect,
    normalize_annotations,
    split_blocks,
    transpose_table,
)
from app.preset import Cell, Table

client = TestClient(app)


# --- синтетические таблицы -----------------------------------------------------


def _grid(*rows_text: str, header_rows: int = 0, **kw) -> Table:
    rows = [
        [Cell(text=t, row=i, col=j) for j, t in enumerate(texts)]
        for i, texts in enumerate(rows_text)
    ]
    return Table(id="t", rows=rows, header_rows=header_rows, **kw)


def _block_table(blocks: list[tuple], n_rows: int, n_cols: int, **kw) -> Table:
    """Таблица из блоков (r, c, rowspan, colspan, text); дыры — пустые ячейки."""
    cells: dict[tuple[int, int], Cell] = {}
    for r, c, rs, cs, text in blocks:
        for i in range(r, r + rs):
            for j in range(c, c + cs):
                if (i, j) == (r, c):
                    cells[(i, j)] = Cell(text=text, row=i, col=j, rowspan=rs, colspan=cs)
                else:
                    cells[(i, j)] = Cell(text="", row=i, col=j, is_merged=True, merged_with=(r, c))
    rows = [
        [cells.get((i, j)) or Cell(text="", row=i, col=j) for j in range(n_cols)]
        for i in range(n_rows)
    ]
    return Table(id="t", rows=rows, header_rows=kw.pop("header_rows", 0),
                 has_merged_cells=any(rs > 1 or cs > 1 for _r, _c, rs, cs, _ in blocks), **kw)


def _anchor(table: Table, r: int, c: int) -> Cell:
    return table.get_cell(r, c)


# --- apply_ops: операции (паритет с table-ops.js) -------------------------------


def test_merge_rect_vertical_joins_texts():
    t = _grid(["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"])
    res = merge_rect(t, 0, 0, 1, 0)
    assert res["changed"] is True
    assert res["remap"] == [["0,0", "0,0"], ["1,0", None]]
    a = _anchor(t, 0, 0)
    assert (a.rowspan, a.colspan, a.text) == (2, 1, "a d")
    assert t.get_cell(1, 0) is a  # продолжение раскрывается в якорь


def test_merge_rect_expands_to_full_blocks():
    # блок 1×2 нельзя слить частично — прямоугольник расширяется до него
    t = _block_table([(0, 0, 1, 2, "AB"), (1, 0, 1, 1, "x"), (1, 1, 1, 1, "y")], 2, 2)
    res = merge_rect(t, 0, 1, 1, 1)
    assert res["changed"] is True
    a = _anchor(t, 0, 0)
    assert (a.rowspan, a.colspan) == (2, 2)
    assert a.text == "AB x y"


def test_merge_rect_noops():
    t = _grid(["a", "b"], ["c", "d"])
    assert merge_rect(t, 0, 0, 0, 0)["changed"] is False  # одна ячейка
    t2 = _block_table([(0, 0, 2, 1, "a"), (0, 1, 1, 1, "b"), (1, 1, 1, 1, "c")], 2, 2)
    assert merge_rect(t2, 0, 0, 1, 0)["changed"] is False  # уже один блок


def test_split_blocks_to_1x1():
    t = _block_table([(0, 0, 2, 2, "big"), (0, 2, 1, 1, "z"), (1, 2, 1, 1, "w")], 2, 3)
    res = split_blocks(t, [(1, 1)])
    assert res["changed"] is True
    assert t.get_cell(0, 0).text == "big" and t.get_cell(0, 0).rowspan == 1
    assert t.get_cell(1, 1).text == "" and not t.get_cell(1, 1).is_merged
    assert t.has_merged_cells is False


def test_transpose_swaps_axes_and_orientation():
    t = _block_table(
        [(0, 0, 2, 1, "p"), (0, 1, 1, 1, "m"), (1, 1, 1, 1, "v"), (0, 2, 1, 1, "u"), (1, 2, 1, 1, "w")],
        2, 3, is_matrix=True, orientation="rows",
    )
    res = transpose_table(t)
    assert res["changed"] is True
    assert res["remap"][0] == ["0,0", "0,0"]  # якорь (0,0) → (0,0)
    assert (len(t.rows), t.n_cols) == (3, 2)
    assert t.get_cell(0, 0).colspan == 2  # rowspan 2 ↔ colspan 2
    assert t.is_matrix and t.orientation == "columns"


def test_delete_rows_shifts_and_decrements_header():
    t = _grid(["h1", "h2"], ["a", "b"], ["c", "d"], header_rows=1)
    res = delete_rows(t, [1])
    assert res["changed"] is True
    assert res["remap"] == [["1,0", None], ["1,1", None], ["2,0", "1,0"], ["2,1", "1,1"]]
    assert len(t.rows) == 2 and t.header_rows == 1
    assert t.get_cell(1, 0).text == "c"

    t2 = _grid(["h1", "h2"], ["a", "b"], header_rows=1)
    delete_rows(t2, [0])  # строка шапки
    assert t2.header_rows == 0 and t2.get_cell(0, 0).text == "a"


def test_delete_rows_block_spanning_deleted_row_compresses():
    t = _block_table([(0, 0, 3, 1, "p"), (0, 1, 1, 1, "a"), (1, 1, 1, 1, "b"), (2, 1, 1, 1, "c")], 3, 2)
    res = delete_rows(t, [1])
    # «b» исчезла с аннотацией; блок «p» сжался (якорь жив); «c» поднялась
    assert res["remap"] == [["1,1", None], ["2,1", "1,1"]]
    assert t.get_cell(0, 0).rowspan == 2 and t.get_cell(0, 0).text == "p"
    assert t.get_cell(1, 1).text == "c"


def test_delete_cols():
    t = _grid(["a", "b", "c"], ["d", "e", "f"])
    res = delete_cols(t, [0, 2])
    assert res["changed"] is True
    assert t.n_cols == 1
    assert [c.text for row in t.rows for c in row] == ["b", "e"]
    assert delete_cols(_grid(["a"]), [5])["changed"] is False  # вне диапазона


def test_apply_ops_skips_unknown_and_broken_specs():
    t = _grid(["a", "b", "c"], ["d", "e", "f"])
    apply_ops(t, [
        {"op": "bogus"},
        {"op": "merge"},                     # без rect
        {"op": "merge", "rect": [0, 0]},     # короткий rect
        {"op": "split"},                     # без slots
        "not-a-dict",
        {"op": "del_rows"},                  # без rows
        {"op": "transpose"},
    ])
    assert (len(t.rows), t.n_cols) == (3, 2)  # сработал только transpose (2×3 → 3×2)


def test_apply_ops_recipe_merge_transpose_del():
    t = _block_table([(0, 0, 1, 1, "h"), (0, 1, 1, 1, "u"), (0, 2, 1, 1, "v"),
                      (1, 0, 1, 1, "a"), (1, 1, 1, 1, "1"), (1, 2, 1, 1, "2"),
                      (2, 0, 1, 1, "b"), (2, 1, 1, 1, "3"), (2, 2, 1, 1, "4"),
                      (3, 0, 1, 1, "c"), (3, 1, 1, 1, "5"), (3, 2, 1, 1, "6")], 4, 3)
    t.header_rows = 1
    apply_ops(t, [
        {"op": "merge", "rect": [0, 1, 0, 2]},   # шапка: u+v
        {"op": "transpose"},                     # 4×3 → 3×4
        {"op": "del_rows", "rows": [2]},         # «u v» сжимается, 2/4/6 исчезают
    ])
    assert (len(t.rows), t.n_cols) == (2, 4)
    assert t.get_cell(0, 0).text == "h"
    assert t.get_cell(1, 0).text == "u v" and t.get_cell(1, 0).rowspan == 1
    texts = {c.text for row in t.rows for c in row if not c.is_merged}
    assert texts == {"h", "a", "b", "c", "u v", "1", "3", "5"}


# --- normalize_annotations (паритет с setAnnotations) --------------------------


def test_normalize_annotations_anchor_missing_unknown():
    t = _block_table([(0, 0, 2, 1, "p"), (0, 1, 1, 1, "a"), (1, 1, 1, 1, "b")], 2, 2)
    anns = [
        {"row": 1, "col": 0, "role": "value"},        # continuation → якорь (0,0)
        {"row": 5, "col": 5, "role": "param"},        # слота нет
        {"row": 0, "col": 1, "role": "неизвестная"},  # неизвестная роль
        {"row": 0, "col": 1, "role": "model"},
        {"row": 0, "col": 1, "role": "param", "label": "L"},  # дубль по якорю — последний
    ]
    out = normalize_annotations(t, anns)
    assert out == [
        {"row": 0, "col": 0, "role": "value"},
        {"row": 0, "col": 1, "role": "param", "label": "L"},
    ]


# --- build_preview (паритет с preview.js) ---------------------------------------


def test_preview_keyvalue_with_default_card():
    t = _grid(["Подача", "70", "м3/ч"], ["Напор", "45", "м"])
    anns = [
        {"row": 0, "col": 0, "role": "param"},
        {"row": 0, "col": 1, "role": "value"},
        {"row": 0, "col": 2, "role": "unit"},
        {"row": 1, "col": 0, "role": "param"},
        {"row": 1, "col": 1, "role": "value"},
    ]
    r = build_preview(t, anns)
    assert len(r["cards"]) == 1
    card = r["cards"][0]
    assert card["model"] is None  # карточка «(без модели)»
    assert card["parameters"] == [
        {"name": "Подача", "value": "70", "unit": "м3/ч", "group": None, "row": 0, "col": 1},
        {"name": "Напор", "value": "45", "unit": None, "group": None, "row": 1, "col": 1},
    ]
    assert r["stats"] == {"models": 0, "params": 2, "values": 2}
    assert r["warnings"] == []  # моделей нет — сводка «вне моделей» не выводится


def test_preview_matrix_rows_merged_value_to_all_models():
    # модели в col 0 (строки 1–3), параметры в шапке, merged-значение rowspan 3
    t = _block_table(
        [(0, 1, 1, 1, "Подача"), (0, 2, 1, 1, "Напор"),
         (1, 0, 1, 1, "М1"), (2, 0, 1, 1, "М2"), (3, 0, 1, 1, "М3"),
         (1, 1, 3, 1, "0,0036 (13)"), (1, 2, 1, 1, "70"), (2, 2, 1, 1, "71"), (3, 2, 1, 1, "72")],
        4, 3,
    )
    anns = (
        [{"row": 0, "col": c, "role": "param"} for c in (1, 2)]
        + [{"row": r, "col": 0, "role": "model"} for r in (1, 2, 3)]
        + [{"row": 1, "col": 1, "role": "value"}, {"row": 1, "col": 2, "role": "value"},
           {"row": 2, "col": 2, "role": "value"}, {"row": 3, "col": 2, "role": "value"}]
    )
    r = build_preview(t, anns)
    assert r["stats"] == {"models": 3, "params": 2, "values": 4}
    by_model = {c["model"]: c for c in r["cards"]}
    # merged-значение rowspan 3 ушло всем трём моделям
    for m in ("М1", "М2", "М3"):
        entries = {p["name"]: p["value"] for p in by_model[m]["parameters"]}
        assert entries == {"Подача": "0,0036 (13)", "Напор": {"М1": "70", "М2": "71", "М3": "72"}[m]}
    assert r["warnings"] == []


def test_preview_matrix_columns_rowspan_param():
    # модели в шапке, rowspan-параметр покрывает две строки значений
    t = _block_table(
        [(0, 0, 1, 1, "Parameter"), (0, 2, 1, 1, "M1"), (0, 3, 1, 1, "M2"),
         (1, 0, 2, 1, "Мощность"), (1, 2, 1, 1, "11"), (1, 3, 1, 1, "15"),
         (2, 2, 1, 1, "12"), (2, 3, 1, 1, "16"), (2, 0, 1, 1, ""), (2, 1, 1, 1, "")],
        3, 4,
    )
    anns = (
        [{"row": 0, "col": c, "role": "model"} for c in (2, 3)]
        + [{"row": 1, "col": 0, "role": "param"}]
        + [{"row": r, "col": c, "role": "value"} for r in (1, 2) for c in (2, 3)]
    )
    r = build_preview(t, anns)
    assert r["stats"] == {"models": 2, "params": 1, "values": 4}
    by_model = {c["model"]: c for c in r["cards"]}
    assert [p["value"] for p in by_model["M1"]["parameters"]] == ["11", "12"]
    assert [p["value"] for p in by_model["M2"]["parameters"]] == ["15", "16"]
    assert all(p["name"] == "Мощность" for c in r["cards"] for p in c["parameters"])


def test_preview_warnings():
    # «p1» без значений; у «x» два значения
    t = _grid(["p1", "x", ""], ["", "v1", ""], ["", "v2", ""])
    anns = [
        {"row": 0, "col": 0, "role": "param"},
        {"row": 0, "col": 1, "role": "param", "label": "x"},
        {"row": 1, "col": 1, "role": "value"},
        {"row": 2, "col": 1, "role": "value"},
    ]
    r = build_preview(t, anns)
    assert "Параметр «p1» без значений" in r["warnings"]
    assert "Несколько значений (2) у параметра «x» (без модели)" in r["warnings"]

    # значение без параметра + пустое значение
    t2 = _grid(["x", ""])
    r2 = build_preview(t2, [{"row": 0, "col": 0, "role": "value"}, {"row": 0, "col": 1, "role": "value"}])
    assert any("без параметра" in w for w in r2["warnings"])
    assert any("Пустое значение" in w for w in r2["warnings"])


def test_preview_overrides_section_condition_ignore():
    # вертикальная секция rowspan 3 в col 0 — группа для всех значений
    t = _block_table(
        [(0, 0, 3, 1, "Секция А"),
         (1, 1, 1, 1, "Подача"), (1, 2, 1, 1, "70"), (1, 3, 1, 1, "прим. 1"),
         (2, 1, 1, 1, "Напор"), (2, 2, 1, 1, "45"), (2, 3, 1, 1, "")],
        3, 4,
    )
    anns = [
        {"row": 0, "col": 0, "role": "section"},
        {"row": 1, "col": 1, "role": "param", "label": "Q", "unit": "м3/ч", "group": "Гидравлика"},
        {"row": 1, "col": 2, "role": "value"},
        {"row": 1, "col": 3, "role": "condition"},
        {"row": 2, "col": 1, "role": "param"},
        {"row": 2, "col": 2, "role": "value"},
        {"row": 2, "col": 3, "role": "ignore"},
    ]
    r = build_preview(t, anns)
    card = r["cards"][0]
    q = next(p for p in card["parameters"] if p["name"] == "Q")
    assert (q["value"], q["unit"], q["group"]) == ("70", "м3/ч", "Гидравлика")
    h = next(p for p in card["parameters"] if p["name"] == "Напор")
    assert (h["unit"], h["group"]) == (None, "Секция А")  # group из section-якоря
    assert card["conditions"] == ["прим. 1"]
    assert r["stats"]["values"] == 2  # ignore не посчитан


# --- API: /api/mapping/validate и /api/mapping/apply-template -------------------


def _upload_table_doc(rows: list[list[str]]):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    resp = client.post(
        "/api/documents",
        files={"file": ("tab.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_api_validate_with_ops_and_annotations():
    doc = _upload_table_doc([
        ["Подача", "НС 13-70", "70"],
        ["Напор", "НС 13-180", "45"],
    ])
    table_id = doc["tables"][0]["id"]
    r = client.post("/api/mapping/validate", json={
        "document_id": doc["id"],
        "table_id": table_id,
        "ops": [{"op": "transpose"}],  # 2×3 → 3×2
        "annotations": [
            {"row": 0, "col": 0, "role": "param"},
            {"row": 0, "col": 1, "role": "param"},
            {"row": 1, "col": 0, "role": "model"},
            {"row": 1, "col": 1, "role": "model"},
            {"row": 2, "col": 0, "role": "value"},
            {"row": 2, "col": 1, "role": "value"},
        ],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["stats"] == {"models": 2, "params": 2, "values": 2}
    by_model = {c["model"]: c for c in out["cards"]}
    assert {p["name"]: p["value"] for p in by_model["НС 13-70"]["parameters"]} == {"Подача": "70"}
    assert {p["name"]: p["value"] for p in by_model["НС 13-180"]["parameters"]} == {"Напор": "45"}
    assert out["warnings"] == []


def test_api_validate_errors():
    doc = _upload_table_doc([["a", "b"], ["c", "d"]])
    anns = [{"row": 0, "col": 0, "role": "param"}]
    assert client.post("/api/mapping/validate", json={
        "document_id": "nope", "table_id": "t", "annotations": anns,
    }).status_code == 404
    assert client.post("/api/mapping/validate", json={
        "document_id": doc["id"], "table_id": "nope", "annotations": anns,
    }).status_code == 404
    assert client.post("/api/mapping/validate", json={
        "document_id": doc["id"], "table_id": doc["tables"][0]["id"], "annotations": [],
    }).status_code == 422


def test_api_apply_template():
    doc = _upload_table_doc([
        ["Подача", "НС 13-70", "70"],
        ["Напор", "НС 13-70", "45"],
    ])
    table_id = doc["tables"][0]["id"]
    saved = client.post("/api/presets/save-from-mapping", json={
        "name": "Транспонированный",
        "document_id": doc["id"],
        "table_id": table_id,
        "ops": [{"op": "transpose"}],
        "annotations": [
            {"row": 0, "col": 0, "role": "param"},
            {"row": 1, "col": 0, "role": "model"},
            {"row": 2, "col": 0, "role": "value"},
            {"row": 9, "col": 9, "role": "value"},  # слота нет — будет пропущен
        ],
    })
    assert saved.status_code == 200, saved.text
    mid = saved.json()["id"]

    r = client.post("/api/mapping/apply-template", json={
        "document_id": doc["id"], "table_id": table_id, "template_id": mid,
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["template_id"] == mid
    assert out["applied_ops"] == 1
    assert (out["table"]["n_rows"], out["table"]["n_cols"]) == (3, 2)  # транспонирована
    assert out["table"]["cells"][0]["text"] == "Подача"
    assert [a["role"] for a in out["annotations"]] == ["param", "model", "value"]
    assert out["skipped_annotations"] == 1

    # несовпавшая структура — не ошибка, всё пропущено
    other = _upload_table_doc([["x"], ["y"]])
    r2 = client.post("/api/mapping/apply-template", json={
        "document_id": other["id"], "table_id": other["tables"][0]["id"], "template_id": mid,
    })
    assert r2.status_code == 200
    assert r2.json()["skipped_annotations"] >= 1

    # 404: документ / таблица / шаблон
    assert client.post("/api/mapping/apply-template", json={
        "document_id": "nope", "table_id": "t", "template_id": mid}).status_code == 404
    assert client.post("/api/mapping/apply-template", json={
        "document_id": doc["id"], "table_id": "nope", "template_id": mid}).status_code == 404
    assert client.post("/api/mapping/apply-template", json={
        "document_id": doc["id"], "table_id": table_id, "template_id": "nope"}).status_code == 404
