"""Применение маппинга Visual Mapper на backend (этап 3.8).

Порт клиентской логики на модель Table (этап 2):
- `apply_ops` — ops-рецепт структурных правок (table-ops.js из 3.5/3.7);
- `build_preview` — предпросмотр извлечения по ролям (preview.js из 3.6);
- `normalize_annotations` — якоря merged + пропуск несуществующих слотов
  и неизвестных ролей (setAnnotations из canvas.js).

Единый источник семантики для /api/mapping/validate, /api/mapping/apply-template
и будущего серверного извлечения (этап 5). Клиентский код остаётся для
мгновенного отклика UI — паритет закреплён тестами на синтетических таблицах.
"""
from __future__ import annotations

from app.preset import Cell, Table

# Роли ячеек (roles.js): всё, кроме этих id, — неизвестные
KNOWN_ROLES = {"model", "param", "value", "unit", "section", "condition", "ignore"}

# --- нормализация rows ↔ блоки (table-ops.js: toBlocks/fromBlocks) ------------


def _raw_cell(table: Table, row: int, col: int) -> Cell | None:
    if 0 <= row < len(table.rows) and 0 <= col < len(table.rows[row]):
        return table.rows[row][col]
    return None


def _to_blocks(table: Table) -> list[dict]:
    """rows → блоки {row, col, rowspan, colspan, text} (continuation → якорь)."""
    blocks: list[dict] = []
    for row in table.rows:
        for c in row:
            if c.is_merged and c.merged_with is not None:
                anchor = _raw_cell(table, *c.merged_with)
                # continuation с живым якорем покрыт блоком якоря; битый — сам блок
                if anchor is not None and not anchor.is_merged:
                    continue
            blocks.append(
                {
                    "row": c.row,
                    "col": c.col,
                    "rowspan": max(1, c.rowspan or 1),
                    "colspan": max(1, c.colspan or 1),
                    "text": c.text or "",
                }
            )
    return blocks


def _from_blocks(table: Table, blocks: list[dict]) -> None:
    """Блоки → rows: якорь + continuation-слоты; дыры сетки — пустые ячейки."""
    cells: dict[tuple[int, int], Cell] = {}
    max_r = max_c = -1
    for b in blocks:
        for i in range(b["row"], b["row"] + b["rowspan"]):
            for j in range(b["col"], b["col"] + b["colspan"]):
                if (i, j) == (b["row"], b["col"]):
                    cells[(i, j)] = Cell(
                        text=b["text"], row=i, col=j,
                        rowspan=b["rowspan"], colspan=b["colspan"],
                        is_merged=False, merged_with=None,
                    )
                else:
                    cells[(i, j)] = Cell(
                        text="", row=i, col=j,
                        is_merged=True, merged_with=(b["row"], b["col"]),
                    )
        max_r = max(max_r, b["row"] + b["rowspan"] - 1)
        max_c = max(max_c, b["col"] + b["colspan"] - 1)
    table.rows = [
        [cells.get((i, j)) or Cell(text="", row=i, col=j) for j in range(max_c + 1)]
        for i in range(max_r + 1)
    ]
    table.has_merged_cells = any(b["rowspan"] > 1 or b["colspan"] > 1 for b in blocks)
    table.grid_rows = len(table.rows)
    table.grid_cols = max_c + 1


def _end(b: dict) -> tuple[int, int]:
    return b["row"] + b["rowspan"] - 1, b["col"] + b["colspan"] - 1


def _intersects(b: dict, r0: int, c0: int, r1: int, c1: int) -> bool:
    er, ec = _end(b)
    return b["row"] <= r1 and er >= r0 and b["col"] <= c1 and ec >= c0


def _inside(b: dict, r0: int, c0: int, r1: int, c1: int) -> bool:
    er, ec = _end(b)
    return b["row"] >= r0 and er <= r1 and b["col"] >= c0 and ec <= c1


def _compose_remap(acc: list[list], nxt: list[list]) -> list[list]:
    """Скомпоновать последовательные remap-ы (удалённые не воскресают)."""
    out = [[o, n] for o, n in acc]
    for src, dst in nxt:
        touched = False
        for e in out:
            if e[1] == src:
                e[1] = dst
                touched = True
            if e[0] == src:
                touched = True  # ключ уже учтён (в т.ч. удалённый)
        if not touched:
            out.append([src, dst])
    return out


# --- операции (table-ops.js) --------------------------------------------------


def merge_rect(table: Table, r0: int, c0: int, r1: int, c1: int) -> dict:
    """Объединить прямоугольник в одну ячейку (расширение до фикс-пойнта)."""
    blocks = _to_blocks(table)
    rect = (min(r0, r1), min(c0, c1), max(r0, r1), max(c0, c1))
    grew = True
    while grew:  # любой пересечённый блок входит целиком
        grew = False
        for b in blocks:
            if not _intersects(b, *rect):
                continue
            er, ec = _end(b)
            new = (
                min(rect[0], b["row"]), min(rect[1], b["col"]),
                max(rect[2], er), max(rect[3], ec),
            )
            if new != rect:
                rect = new
                grew = True
    if rect[0] == rect[2] and rect[1] == rect[3]:
        return {"changed": False, "remap": []}

    absorbed = [b for b in blocks if _inside(b, *rect)]
    # no-op: прямоугольник уже совпадает с одним существующим блоком
    if len(absorbed) == 1:
        b = absorbed[0]
        er, ec = _end(b)
        if (b["row"], b["col"], er, ec) == rect:
            return {"changed": False, "remap": []}

    # выживает аннотация блока, накрывающего верхний-левый слот прямоугольника
    survivor = next(
        (b for b in absorbed
         if b["row"] <= rect[0] <= b["row"] + b["rowspan"] - 1
         and b["col"] <= rect[1] <= b["col"] + b["colspan"] - 1),
        None,
    )
    remap = [
        [f'{b["row"]},{b["col"]}',
         f"{rect[0]},{rect[1]}" if b is survivor else None]
        for b in absorbed
    ]

    texts = [b["text"].strip() for b in sorted(absorbed, key=lambda x: (x["row"], x["col"])) if b["text"].strip()]
    kept = [b for b in blocks if not _inside(b, *rect)]
    kept.append(
        {
            "row": rect[0], "col": rect[1],
            "rowspan": rect[2] - rect[0] + 1, "colspan": rect[3] - rect[1] + 1,
            "text": " ".join(texts),
        }
    )
    _from_blocks(table, kept)
    return {"changed": True, "remap": remap}


def split_blocks(table: Table, slots: list[tuple[int, int]]) -> dict:
    """Разделить merged-блоки, накрытые слотами: каждый — на 1×1."""
    blocks = _to_blocks(table)
    out: list[dict] = []
    changed = False
    for b in blocks:
        hit = any(
            b["row"] <= r < b["row"] + b["rowspan"] and b["col"] <= c < b["col"] + b["colspan"]
            for r, c in slots
        )
        if not hit or (b["rowspan"] == 1 and b["colspan"] == 1):
            out.append(b)
            continue
        changed = True
        for i in range(b["row"], b["row"] + b["rowspan"]):
            for j in range(b["col"], b["col"] + b["colspan"]):
                out.append(
                    {"row": i, "col": j, "rowspan": 1, "colspan": 1,
                     "text": b["text"] if (i, j) == (b["row"], b["col"]) else ""}
                )
    if not changed:
        return {"changed": False, "remap": []}
    _from_blocks(table, out)
    return {"changed": True, "remap": []}


def transpose_table(table: Table) -> dict:
    """Транспонировать таблицу (строки ↔ колонки); у матрицы флип orientation."""
    src = _to_blocks(table)
    if not src:
        return {"changed": False, "remap": []}
    blocks = [
        {"row": b["col"], "col": b["row"], "rowspan": b["colspan"], "colspan": b["rowspan"], "text": b["text"]}
        for b in src
    ]
    remap = [[f"{b['col']},{b['row']}", f"{b['row']},{b['col']}"] for b in blocks]
    _from_blocks(table, blocks)
    if table.is_matrix:
        table.orientation = "columns" if table.orientation != "columns" else "rows"
    return {"changed": True, "remap": remap}


def _delete_axis(table: Table, indices: list[int], axis: str) -> dict:
    is_row = axis == "row"
    size = len(table.rows) if is_row else table.n_cols
    changed = False
    remap: list[list] = []
    for idx in sorted(set(indices), reverse=True):  # с конца — индексы не плывут
        if idx < 0 or idx >= size:
            continue
        blocks = _to_blocks(table)
        out: list[dict] = []
        step_remap: list[list] = []
        for b in blocks:
            pos = b["row"] if is_row else b["col"]
            span = b["rowspan"] if is_row else b["colspan"]
            if pos == idx and span == 1:
                # блок целиком в удаляемой линии — исчезает вместе с аннотацией
                step_remap.append([f'{b["row"]},{b["col"]}', None])
                continue
            if pos <= idx < pos + span:
                # блок спанит через удаляемую линию — сжимается, якорь живёт
                nb = dict(b)
                nb["rowspan" if is_row else "colspan"] = span - 1
                out.append(nb)
                continue
            if pos > idx:
                nb = dict(b)
                if is_row:
                    nb["row"] = pos - 1
                    step_remap.append([f"{b['row']},{b['col']}", f"{b['row'] - 1},{b['col']}"])
                else:
                    nb["col"] = pos - 1
                    step_remap.append([f"{b['row']},{b['col']}", f"{b['row']},{b['col'] - 1}"])
                out.append(nb)
                continue
            out.append(b)
        if is_row and table.header_rows > idx:
            table.header_rows = max(0, table.header_rows - 1)
        _from_blocks(table, out)
        remap = _compose_remap(remap, step_remap)
        changed = True
        size -= 1
    return {"changed": changed, "remap": remap}


def delete_rows(table: Table, indices: list[int]) -> dict:
    return _delete_axis(table, indices, "row")


def delete_cols(table: Table, indices: list[int]) -> dict:
    return _delete_axis(table, indices, "col")


def apply_ops(table: Table, ops: list[dict]) -> Table:
    """Применить ops-рецепт структурных правок (мутабельно).

    Спекы — как в getOps() VisualMapper: {op:"merge", rect:[r0,c0,r1,c1]},
    {op:"split", slots:[[r,c],...]}, {op:"transpose"}, {op:"del_rows", rows:[...]},
    {op:"del_cols", cols:[...]}. No-op и неизвестные спеки пропускаются.
    """
    for spec in ops or []:
        if not isinstance(spec, dict):
            continue
        op = spec.get("op")
        if op == "merge" and isinstance(spec.get("rect"), list) and len(spec["rect"]) == 4:
            merge_rect(table, *spec["rect"])
        elif op == "split" and spec.get("slots"):
            split_blocks(table, [(r, c) for r, c in spec["slots"]])
        elif op == "transpose":
            transpose_table(table)
        elif op == "del_rows" and isinstance(spec.get("rows"), list):
            delete_rows(table, spec["rows"])
        elif op == "del_cols" and isinstance(spec.get("cols"), list):
            delete_cols(table, spec["cols"])
    return table


# --- нормализация аннотаций (canvas.js: setAnnotations) -----------------------


def normalize_annotations(table: Table, annotations: list[dict]) -> list[dict]:
    """К якорям merged; пропуск несуществующих слотов и неизвестных ролей.

    Дубли по якорю — последний побеждает (как Map в setAnnotations).
    """
    out: dict[tuple[int, int], dict] = {}
    for a in annotations or []:
        if not isinstance(a, dict) or a.get("role") not in KNOWN_ROLES:
            continue
        cell = table.get_cell(a.get("row", -1), a.get("col", -1))
        if cell is None:
            continue
        clean = {"row": cell.row, "col": cell.col, "role": a["role"]}
        for k in ("label", "unit", "group"):
            if a.get(k):
                clean[k] = a[k]
        out[(cell.row, cell.col)] = clean
    return list(out.values())


# --- предпросмотр извлечения (preview.js: buildPreview) -----------------------

DEFAULT_CARD_KEY = (-1, -1)


def _zone(cell: Cell) -> dict:
    return {
        "r0": cell.row, "r1": cell.row + max(1, cell.rowspan or 1) - 1,
        "c0": cell.col, "c1": cell.col + max(1, cell.colspan or 1) - 1,
    }


def _covers_row(z: dict, r: int) -> bool:
    return z["r0"] <= r <= z["r1"]


def _covers_col(z: dict, c: int) -> bool:
    return z["c0"] <= c <= z["c1"]


def _left_of(a: dict, b: dict) -> bool:
    return a["c1"] < b["c0"]


def _above(a: dict, b: dict) -> bool:
    return a["r1"] < b["r0"]


def _rows_intersect(a: dict, b: dict) -> bool:
    return a["r0"] <= b["r1"] and b["r0"] <= a["r1"]


def _cols_intersect(a: dict, b: dict) -> bool:
    return a["c0"] <= b["c1"] and b["c0"] <= a["c1"]


def build_preview(table: Table, annotations: list[dict]) -> dict:
    """Применить роли к таблице и собрать предпросмотр извлечения.

    Возвращает {cards, stats, warnings} — форма совпадает с preview.js,
    чтобы клиент и сервер показывали одно и то же.
    """
    by_role: dict[str, list[dict]] = {r: [] for r in KNOWN_ROLES if r != "ignore"}
    for a in annotations or []:
        if not isinstance(a, dict) or a.get("role") not in by_role:
            continue
        cell = table.get_cell(a.get("row", -1), a.get("col", -1))
        if cell is None:
            continue
        by_role[a["role"]].append({**a, "cell": cell, "zone": _zone(cell)})

    warnings: list[str] = []
    cards: dict[tuple[int, int], dict] = {}

    def default_card() -> dict:
        if DEFAULT_CARD_KEY not in cards:
            cards[DEFAULT_CARD_KEY] = {"model": None, "parameters": [], "conditions": []}
        return cards[DEFAULT_CARD_KEY]

    def card_for(m: dict) -> dict:
        k = (m["cell"].row, m["cell"].col)
        if k not in cards:
            cards[k] = {"model": (m["cell"].text or "").strip(), "parameters": [], "conditions": []}
        return cards[k]

    # Параметр значения: (а) покрывает строку якоря и левее, самый правый;
    # (б) иначе покрывает колонку и выше, самый нижний.
    def find_param(v: dict) -> dict | None:
        best = None
        for p in by_role["param"]:
            if _covers_row(p["zone"], v["cell"].row) and _left_of(p["zone"], v["zone"]):
                if best is None or p["zone"]["c1"] > best["zone"]["c1"]:
                    best = p
        if best is not None:
            return best
        for p in by_role["param"]:
            if _covers_col(p["zone"], v["cell"].col) and _above(p["zone"], v["zone"]):
                if best is None or p["zone"]["r1"] > best["zone"]["r1"]:
                    best = p
        return best

    # Модели значения: перпендикулярное пересечение зон.
    def find_models(v: dict) -> list[dict]:
        out = []
        for m in by_role["model"]:
            if _rows_intersect(m["zone"], v["zone"]) and _left_of(m["zone"], v["zone"]):
                out.append(m)
            elif _cols_intersect(m["zone"], v["zone"]) and _above(m["zone"], v["zone"]):
                out.append(m)
        return out

    # Единица значения: unit-якорь в строке (ближайший по колонке), иначе выше в колонке.
    def find_unit(v: dict) -> str | None:
        best = None
        for u in by_role["unit"]:
            if u["cell"].row == v["cell"].row:
                if best is None or abs(u["cell"].col - v["cell"].col) < abs(best["cell"].col - v["cell"].col):
                    best = u
        if best is not None:
            return (best["cell"].text or "").strip()
        for u in by_role["unit"]:
            if _covers_col(u["zone"], v["cell"].col) and _above(u["zone"], v["zone"]):
                if best is None or u["zone"]["r1"] > best["zone"]["r1"]:
                    best = u
        return (best["cell"].text or "").strip() if best is not None else None

    # Группа значения: section-якорь, накрывающий значение по строкам.
    def find_section(v: dict) -> str | None:
        for s in by_role["section"]:
            if _rows_intersect(s["zone"], v["zone"]) and (
                _cols_intersect(s["zone"], v["zone"]) or _left_of(s["zone"], v["zone"])
            ):
                return (s["cell"].text or "").strip()
        return None

    used_params: set[tuple[int, int]] = set()
    param_names: set[str] = set()

    for v in by_role["value"]:
        p = find_param(v)
        value = (v["cell"].text or "").strip()
        name = (p.get("label") or (p["cell"].text or "").strip()) if p else None
        models = find_models(v)
        targets = [card_for(m) for m in models] if models else [default_card()]

        if p is None:
            warnings.append(
                f"Значение «{value or '(пусто)'}» (стр. {v['cell'].row + 1}, кол. {v['cell'].col + 1}) без параметра"
            )
        else:
            used_params.add((p["cell"].row, p["cell"].col))
        if not value:
            where = f" параметра «{name}»" if name else ""
            warnings.append(f"Пустое значение{where} (стр. {v['cell'].row + 1}, кол. {v['cell'].col + 1})")
        if name:
            param_names.add(name)

        unit = (p.get("unit") if p else None) or find_unit(v)
        group = (p.get("group") if p else None) or find_section(v)
        for card in targets:
            card["parameters"].append(
                {"name": name, "value": value, "unit": unit, "group": group,
                 "row": v["cell"].row, "col": v["cell"].col}
            )

    # сводное предупреждение про значения вне моделей
    dc = cards.get(DEFAULT_CARD_KEY)
    if dc is not None and by_role["model"] and dc["parameters"]:
        warnings.append(f"Значений вне моделей: {len(dc['parameters'])} — карточка «(без модели)»")

    # параметры без значений
    for p in by_role["param"]:
        if (p["cell"].row, p["cell"].col) in used_params:
            continue
        warnings.append(f"Параметр «{p.get('label') or (p['cell'].text or '').strip()}» без значений")

    # несколько значений у одного (модель, параметр)
    for card in cards.values():
        seen: dict[str, int] = {}
        for e in card["parameters"]:
            k = e["name"] or ""
            seen[k] = seen.get(k, 0) + 1
        for k, n in seen.items():
            if n > 1:
                whose = f" модели «{card['model']}»" if card["model"] else " (без модели)"
                warnings.append(f"Несколько значений ({n}) у параметра «{k or '?'}»{whose}")

    # примечания → карточки моделей (по тому же правилу привязки)
    for cond in by_role["condition"]:
        text = (cond["cell"].text or "").strip()
        if not text:
            continue
        models = find_models(cond)
        targets = [card_for(m) for m in models] if models else [default_card()]
        for card in targets:
            if text not in card["conditions"]:
                card["conditions"].append(text)

    # карточки «(без модели)» — последняя; порядок остальных — по якорям моделей
    ordered = []
    for m in sorted(by_role["model"], key=lambda m: (m["cell"].row, m["cell"].col)):
        card = cards.get((m["cell"].row, m["cell"].col))
        if card is not None:
            ordered.append(card)
    if DEFAULT_CARD_KEY in cards:
        ordered.append(cards[DEFAULT_CARD_KEY])

    return {
        "cards": ordered,
        "stats": {
            "models": sum(1 for c in ordered if c["model"] is not None),
            "params": len(param_names),
            "values": len(by_role["value"]),
        },
        "warnings": warnings,
    }
