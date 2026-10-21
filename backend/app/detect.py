"""Детекция таблиц в PDF по раскладке word-boxes (без фиксированных координат)."""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right

from app.matrix import _looks_like_value, _parse_header_text
from app.preset import HEADER_VOCAB
from app.normalize import collapse_ws, looks_numeric
from app.preset import Cell, Table


def _same_line(a: tuple, b: tuple, factor: float = 0.4) -> bool:
    """Слова на одной строке, если вертикальные интервалы пересекаются."""
    y0a, y1a = a[1], a[3]
    y0b, y1b = b[1], b[3]
    h = min(y1a - y0a, y1b - y0b)
    inter = min(y1a, y1b) - max(y0a, y0b)
    return inter > factor * h


def _group_lines(words: list[tuple]) -> list[list[tuple]]:
    words = sorted(words, key=lambda w: (round(w[1], 1), w[0]))
    lines: list[list[tuple]] = []
    for w in words:
        if lines and _same_line(lines[-1][-1], w):
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w[0])
    return [ln for ln in lines if ln]


def _split_cells(line: list[tuple], gap_threshold: float) -> list[tuple]:
    """Разбить слова строки на ячейки по горизонтальным разрывам."""
    if not line:
        return []
    cells: list[list[tuple]] = [[line[0]]]
    for w in line[1:]:
        if w[0] - cells[-1][-1][2] > gap_threshold:
            cells.append([w])
        else:
            cells[-1].append(w)
    out = []
    for group in cells:
        x0 = min(g[0] for g in group)
        y0 = min(g[1] for g in group)
        x1 = max(g[2] for g in group)
        y1 = max(g[3] for g in group)
        text = " ".join(g[4] for g in group)
        out.append((x0, y0, x1, y1, text))
    return out


def _column_separators(lines_cells: list[list[tuple]], tol: float) -> list[float]:
    """Границы колонок — кластеры середин разрывов между ячейками строк."""
    seps: list[float] = []
    for line in lines_cells:
        for i in range(len(line) - 1):
            seps.append((line[i][2] + line[i + 1][0]) / 2.0)
    if not seps:
        return []
    seps.sort()
    clusters: list[list[float]] = [[seps[0]]]
    for s in seps[1:]:
        if s - clusters[-1][-1] <= tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    return [sum(c) / len(c) for c in clusters]


def _assign_col(x0: float, x1: float, bounds: list[float]) -> int:
    best, best_overlap = 0, -1.0
    for i in range(len(bounds) - 1):
        lo, hi = bounds[i], bounds[i + 1]
        overlap = max(0.0, min(x1, hi) - max(x0, lo))
        if overlap > best_overlap:
            best, best_overlap = i, overlap
    if best_overlap <= 0:
        center = (x0 + x1) / 2
        best = min(range(len(bounds) - 1), key=lambda i: abs((bounds[i] + bounds[i + 1]) / 2 - center))
    return best


def detect_tables_on_page(words: list[tuple], page_number: int, min_rows: int = 3) -> list[Table]:
    if len(words) < 6:
        return []
    heights = [w[3] - w[1] for w in words]
    median_h = sorted(heights)[len(heights) // 2] or 8.0
    gap = max(median_h * 0.55, 2.0)

    raw_lines = _group_lines(words)
    lines_cells = [_split_cells(ln, gap) for ln in raw_lines]
    lines_cells = [ln for ln in lines_cells if ln]
    if not lines_cells:
        return []

    separators = _column_separators(lines_cells, max(median_h * 1.2, 4.0))
    if len(separators) < 1:
        return []
    right_edge = max(c[2] for line in lines_cells for c in line) + 4.0
    bounds = [0.0, *separators, right_edge]
    n_cols = len(bounds) - 1

    rows: list[list[tuple | None]] = []
    for line in lines_cells:
        row: list[tuple | None] = [None] * n_cols
        for (x0, y0, x1, y1, text) in line:
            col = _assign_col(x0, x1, bounds)
            if 0 <= col < len(row):
                if row[col] is None:
                    row[col] = (x0, y0, x1, y1, text)
                else:
                    cur = row[col]
                    row[col] = (min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1), cur[4] + " " + text)
        rows.append(row)

    # убираем колонки, встречающиеся меньше чем в 2 строках (шум)
    col_counts = [sum(1 for r in rows if r[j] is not None) for j in range(n_cols)]
    keep = [j for j in range(n_cols) if col_counts[j] >= 2]
    if len(keep) < 2:
        keep = sorted(range(n_cols), key=lambda j: -col_counts[j])[:2]
    rows = [[r[j] for j in keep] for r in rows]
    n_cols = len(keep)

    def nonempty_count(row) -> int:
        return sum(1 for c in row if c is not None)

    blocks: list[list[int]] = []
    cur: list[int] = []
    for i, row in enumerate(rows):
        if nonempty_count(row) >= 2:
            cur.append(i)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    tables: list[Table] = []
    for idx, block in enumerate(blocks):
        if len(block) < min_rows:
            continue
        start, end = block[0], block[-1]
        while start > 0:
            row = rows[start - 1]
            if nonempty_count(row) >= 2:
                break
            text = next((c[4] for c in row if c is not None), "")
            if text and len(text) <= 90 and not looks_numeric(text):
                start -= 1
            else:
                break
        while end < len(rows) - 1:
            row = rows[end + 1]
            if nonempty_count(row) >= 2:
                break
            text = next((c[4] for c in row if c is not None), "")
            if text and len(text) <= 90 and not looks_numeric(text):
                end += 1
            else:
                break

        cells: list[list[Cell]] = []
        for i in range(start, end + 1):
            row_cells: list[Cell] = []
            for j in range(n_cols):
                c = rows[i][j]
                if c is None:
                    row_cells.append(Cell(text="", row=i - start, col=j, page=page_number))
                else:
                    x0, y0, x1, y1, text = c
                    row_cells.append(
                        Cell(text=text, row=i - start, col=j, page=page_number, x0=x0, y0=y0, x1=x1, y1=y1)
                    )
            cells.append(row_cells)

        xs = [c.x0 for r in cells for c in r if c.x0 is not None] or [0]
        x2 = [c.x1 for r in cells for c in r if c.x1 is not None] or [0]
        ys = [c.y0 for r in cells for c in r if c.y0 is not None] or [0]
        y2 = [c.y1 for r in cells for c in r if c.y1 is not None] or [0]
        bbox = [min(xs), min(ys), max(x2), max(y2)]
        tables.append(Table(id=f"p{page_number + 1}-t{idx + 1}", page=page_number, rows=cells, bbox=bbox, source="word"))
    return tables
def _cluster_coords(values, tol=0.8):
    """Group close coordinates into line positions."""
    values = sorted(values)
    groups = []
    for v in values:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def _nearest_line(v, centers, tol=1.2):
    """Индекс ближайшего центра в пределах tol (None, если дальше).

    `centers` отсортированы по возрастанию (выход _cluster_coords) —
    поиск двоичный, O(log n) вместо линейного перебора.
    """
    if not centers:
        return None
    i = bisect_left(centers, v)
    if i == 0:
        best = 0
    elif i == len(centers):
        best = len(centers) - 1
    else:
        best = i - 1 if v - centers[i - 1] <= centers[i] - v else i
    return best if abs(centers[best] - v) <= tol else None


def _drawing_segments(rects):
    """Split thin drawing rects into vertical/horizontal border segments."""
    vert, hor = [], []
    for x0, y0, x1, y1 in rects:
        w = x1 - x0
        h = y1 - y0
        if w < 1.4 and h >= 3.0:
            vert.append((x0, y0, x1, y1))
        elif h < 1.4 and w >= 3.0:
            hor.append((x0, y0, x1, y1))
    return vert, hor


def _bbox_overlap_ratio(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return inter / area if area > 0 else 0.0


def _covered_fraction(a, b):
    """Доля площади bbox `a`, покрытая bbox `b` (0..1)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = (ax1 - ax0) * (ay1 - ay0)
    return inter / area if area > 0 else 0.0


def detect_tables_from_grid(words, rects, page_number, min_rows=2) -> list[Table]:
    """Table detection from drawn cell borders (vector grid lines).

    Works on PDF pages where cells are drawn as thin rectangles. Falls back
    to nothing when no grid is present; callers keep the word-based detector.
    """
    vert, hor = _drawing_segments(rects or [])
    if len(vert) < 4 or len(hor) < 4:
        return []
    vvals = [(x0 + x1) / 2 for x0, y0, x1, y1 in vert]
    hvals = [(y0 + y1) / 2 for x0, y0, x1, y1 in hor]
    vlines = _cluster_coords(vvals)
    hlines = _cluster_coords(hvals)

    def support(vals, centers):
        cnt = [0] * len(centers)
        for v in vals:
            i = _nearest_line(v, centers)
            if i is not None:
                cnt[i] += 1
        return cnt

    vcnt = support(vvals, vlines)
    hcnt = support(hvals, hlines)
    vlines = [x for x, c in zip(vlines, vcnt) if c >= 2]
    hlines = [y for y, c in zip(hlines, hcnt) if c >= 2]
    if len(vlines) < 3 or len(hlines) < 2:
        return []
    # удвоенные границы (толстая рамка = 2 линии ~1pt) дают фантомные колонки
    vlines = _cluster_coords(vlines, tol=2.0)
    hlines = _cluster_coords(hlines, tol=2.0)
    if len(vlines) < 3 or len(hlines) < 2:
        return []

    # per band (between horizontal lines): vertical lines crossing it.
    # Вырожденные полосы (высота <2pt от удвоенных линий-кластеров) отбрасываем:
    # они ложно разрывают таблицу, отрезая шапку (VP: «Parameter Unit 2522...»).
    band_verticals = []
    for j in range(len(hlines) - 1):
        lo, hi = hlines[j], hlines[j + 1]
        if hi - lo < 2.0:
            continue
        idxs = set()
        for x0, y0, x1, y1 in vert:
            if y0 <= lo + 1.0 and y1 >= hi - 1.0:
                i = _nearest_line((x0 + x1) / 2, vlines)
                if i is not None:
                    idxs.add(i)
        band_verticals.append((j, lo, hi, sorted(idxs)))

    runs = []
    cur = []
    for band in band_verticals:
        if len(band[3]) >= 2:
            cur.append(band)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    tables = []
    for run in runs:
        rows_info = run
        counts = {}
        for _j, _lo, _hi, idxs in rows_info:
            for i in idxs:
                counts[i] = counts.get(i, 0) + 1
        threshold = max(2, round(len(run) * 0.35))
        col_idx = sorted(i for i in range(len(vlines)) if counts.get(i, 0) >= threshold)
        if len(col_idx) < 3:
            continue
        n_rows = len(run)
        n_cols = len(col_idx) - 1

        # Полную сетку полос×колонок достраиваем границами: merge = связная
        # область ячеек, между которыми внутренние линии НЕ нарисованы.
        # vborder[i][c]: вертикальная линия между колонками c и c+1 в полосе i.
        vborder = [[col_idx[c + 1] in rows_info[i][3] for c in range(n_cols - 1)]
                   for i in range(n_rows)]
        # Горизонтальные сегменты раскладываем по линиям сетки заранее
        # (y-центр сегмента → ближайшая hline в пределах 1pt) и кэшируем
        # перекрытия «линия × колонка»: линейный обход всех сегментов на каждую
        # пару полос×колонок был главным горячим местом на больших таблицах.
        hor_by_line: dict[int, list[tuple[float, float]]] = {}
        for x0, y0, x1, y1 in hor:
            cy = (y0 + y1) / 2
            k = bisect_left(hlines, cy)
            for kk in (k - 1, k):
                if 0 <= kk < len(hlines) and abs(hlines[kk] - cy) <= 1.0:
                    hor_by_line.setdefault(kk, []).append((x0, x1))
        cover_cache: dict[tuple[int, int], bool] = {}

        def _line_covers_col(k: int, c: int) -> bool:
            """Линия k перекрывает колонку c на >=50% ширины (с кэшем)."""
            key = (k, c)
            res = cover_cache.get(key)
            if res is None:
                x_lo, x_hi = vlines[col_idx[c]], vlines[col_idx[c + 1]]
                res = any(
                    min(x1, x_hi) - max(x0, x_lo) >= (x_hi - x_lo) * 0.5
                    for x0, x1 in hor_by_line.get(k, ())
                )
                cover_cache[key] = res
            return res

        # hborder[i][c]: горизонтальная линия между полосами i и i+1 в колонке c.
        # Соседние полосы могут быть разделены выброшенной вырожденной полосой —
        # проверяем все линии между ними.
        hborder = [
            [
                any(
                    _line_covers_col(k, c)
                    for k in range(rows_info[i][0] + 1, rows_info[i + 1][0] + 1)
                )
                for c in range(n_cols)
            ]
            for i in range(n_rows - 1)
        ]

        # Связные компоненты ячеек по отсутствующим границам (flood fill).
        comp = [[-1] * n_cols for _ in range(n_rows)]
        comps: list[list[tuple[int, int]]] = []
        for i in range(n_rows):
            for c in range(n_cols):
                if comp[i][c] != -1:
                    continue
                cid = len(comps)
                stack = [(i, c)]
                comp[i][c] = cid
                cells = [(i, c)]
                while stack:
                    r, k = stack.pop()
                    if k + 1 < n_cols and comp[r][k + 1] == -1 and not vborder[r][k]:
                        comp[r][k + 1] = cid
                        stack.append((r, k + 1))
                        cells.append((r, k + 1))
                    if k > 0 and comp[r][k - 1] == -1 and not vborder[r][k - 1]:
                        comp[r][k - 1] = cid
                        stack.append((r, k - 1))
                        cells.append((r, k - 1))
                    if r + 1 < n_rows and comp[r + 1][k] == -1 and not hborder[r][k]:
                        comp[r + 1][k] = cid
                        stack.append((r + 1, k))
                        cells.append((r + 1, k))
                    if r > 0 and comp[r - 1][k] == -1 and not hborder[r - 1][k]:
                        comp[r - 1][k] = cid
                        stack.append((r - 1, k))
                        cells.append((r - 1, k))
                comps.append(cells)

        # Прямоугольные компоненты >1 ячейки — merged cell (якорь + продолжения);
        # L-образные оставляем раздельными ячейками (fallback без merge).
        merged_shapes: dict[int, tuple[int, int, int, int]] = {}
        for cid, cells_c in enumerate(comps):
            if len(cells_c) < 2:
                continue
            rs = [r for r, _ in cells_c]
            cs = [c for _, c in cells_c]
            r0, c0 = min(rs), min(cs)
            r1, c1 = max(rs), max(cs)
            if len(cells_c) == (r1 - r0 + 1) * (c1 - c0 + 1):
                merged_shapes[cid] = (r0, c0, r1 - r0 + 1, c1 - c0 + 1)

        # Слова биннятся центром в сетку (полоса × колонка); границы полос и
        # колонок отсортированы — попадание ищется двоичным поиском.
        band_los = [b[1] for b in rows_info]
        col_bounds = [vlines[i] for i in col_idx]
        word_cells: dict[tuple[int, int], list[tuple]] = {}
        for w in words:
            yc = (w[1] + w[3]) / 2
            xc = (w[0] + w[2]) / 2
            i = bisect_right(band_los, yc) - 1
            if i < 0 or yc >= rows_info[i][2]:
                continue
            c = bisect_right(col_bounds, xc) - 1
            if c < 0 or c >= n_cols:
                continue
            word_cells.setdefault((i, c), []).append(w)

        model_rows = []
        for i in range(n_rows):
            lo, hi = rows_info[i][1], rows_info[i][2]
            row_cells: list[Cell] = []
            for c in range(n_cols):
                cid = comp[i][c]
                shape = merged_shapes.get(cid)
                if shape and (i, c) != (shape[0], shape[1]):
                    # клетка-продолжение merged: пустая, со ссылкой на якорь
                    row_cells.append(
                        Cell(text="", row=i, col=c, page=page_number,
                             x0=vlines[col_idx[c]], y0=lo, x1=vlines[col_idx[c + 1]], y1=hi,
                             is_merged=True, merged_with=(shape[0], shape[1]))
                    )
                    continue
                if shape:
                    r0, c0, rowspan, colspan = shape
                    found = []
                    for (r, k) in comps[cid]:
                        found.extend(word_cells.get((r, k), []))
                    found.sort(key=lambda w: (round(w[1], 1), w[0]))
                    row_cells.append(
                        Cell(
                            text=" ".join(w[4] for w in found),
                            row=i, col=c, page=page_number,
                            x0=vlines[col_idx[c0]], y0=rows_info[r0][1],
                            x1=vlines[col_idx[c0 + colspan]], y1=rows_info[r0 + rowspan - 1][2],
                            rowspan=rowspan, colspan=colspan,
                        )
                    )
                    continue
                found = sorted(word_cells.get((i, c), []), key=lambda w: (round(w[1], 1), w[0]))
                row_cells.append(
                    Cell(
                        text=" ".join(w[4] for w in found),
                        row=i, col=c, page=page_number,
                        x0=vlines[col_idx[c]], y0=lo, x1=vlines[col_idx[c + 1]], y1=hi,
                    )
                )
            model_rows.append(row_cells)

        content = [r for r in model_rows if sum(1 for c in r if c.text.strip()) >= 2]
        if len(content) < min_rows:
            continue
        xs = [c.x0 for r in model_rows for c in r if c.x0 is not None] or [0.0]
        x2 = [c.x1 for r in model_rows for c in r if c.x1 is not None] or [0.0]
        ys = [c.y0 for r in model_rows for c in r if c.y0 is not None] or [0.0]
        y2 = [c.y1 for r in model_rows for c in r if c.y1 is not None] or [0.0]
        bbox = [min(xs), min(ys), max(x2), max(y2)]
        tables.append(
            Table(
                id=f"p{page_number + 1}-t{len(tables) + 1}",
                page=page_number,
                rows=model_rows,
                bbox=bbox,
                source="grid",
                has_merged_cells=bool(merged_shapes),
            )
        )
    return tables


_GRID_MODEL_CODE_RE = re.compile(r"^[А-ЯЁа-яёA-Za-z0-9]{0,6}?\d{1,3}-\d{1,4}(?:-\d{1,4})?$")

# Минимальные пороги признания таблицы матрицей (модель×параметр).
MATRIX_MIN_MODELS = 3      # разных строк/колонок с моделями
MATRIX_MIN_DATA_ROWS = 3   # строк данных со значениями
MATRIX_MIN_PARAM_COLS = 2  # колонок с числовыми значениями


# Модель в токене: дефис-разделённый код с цифрами (СМ80-50-200-2, 1К100-65-200м-с,
# НС 13-70-PP, ЭЦВ 5-4-75).
_MODEL_TOK_RE = re.compile(r"^[A-Za-zА-ЯЁа-яё0-9]{1,8}(?:-[A-Za-zА-ЯЁа-яё0-9]{1,8})+$")
_ALPHA_PREFIX_RE = re.compile(r"^[A-Za-zА-ЯЁа-яё]{1,6}$")
# код типоразмера в шапке транспонированной матрицы без префикса («2522», «2534»)
_PLAIN_CODE_RE = re.compile(r"^\d{2,6}$")


def _cell_text(c: Cell) -> str:
    return collapse_ws(c.text).strip()


def _is_model_token(tok: str) -> bool:
    """Токен-модель: дефисный код с цифрами (не числовое значение)."""
    return bool(re.search(r"\d", tok)) and bool(_MODEL_TOK_RE.match(tok))


def _cell_models(c: Cell) -> list[str]:
    """Модели в ячейке-«модели»: все токены — модели или буквенные префиксы
    («НС 13-70 НСГ 13-70»). Числовые значения и смешанный текст — не модели."""
    if c.is_merged:
        return []
    s = _cell_text(c)
    if not s or _looks_like_value(s):
        return []
    out: list[str] = []
    for tok in s.split(" "):
        if _is_model_token(tok):
            out.append(tok)
        elif not _ALPHA_PREFIX_RE.match(tok):
            return []
    return out


def _is_column_numbering(cells: list[Cell]) -> bool:
    """Строка нумерации колонок («1 2 3 ... 20») — не модели."""
    if not cells:
        return False
    texts = [re.sub(r"[.…]+$", "", _cell_text(c)).rstrip("-–—") for c in cells]
    if not all(re.fullmatch(r"\d{1,2}", t) for t in texts):
        return False
    nums = [int(t) for t in texts]
    gaps = [b - a for a, b in zip(nums, nums[1:])]
    return all(g == 1 for g in gaps)


def _header_model_cells(row: list[Cell]) -> list[Cell]:
    """Ячейки-коды моделей в строке шапки (транспонированная матрица):
    модели или чистые коды («2522»); после начала кодов текста быть не должно."""
    cells: list[Cell] = []
    started = False
    for c in row[1:]:
        t = _cell_text(c)
        if not t:
            continue
        if c.is_merged:
            started = True
            continue
        tok = re.sub(r"[.…]+$", "", t).rstrip("-–—")
        if _is_model_token(tok) or _PLAIN_CODE_RE.match(tok):
            started = True
            cells.append(c)
        elif started:
            return []
    if _is_column_numbering(cells):
        return []
    return cells


def _row_value_count(row: list[Cell]) -> int:
    """Число ячеек-значений в строке (числа/диапазоны, якоря merged считаются)."""
    n = 0
    for c in row:
        if c.is_merged:
            continue
        t = _cell_text(c)
        if t and _looks_like_value(t):
            n += 1
    return n


def _param_label(text: str) -> bool:
    """Текст — название параметра («Подача, м3/ч», «Мощность электродвигателя»)."""
    return bool(text) and len(text) <= 90 and bool(_parse_header_text(text))


# маркеры перечней деталей/комплектности (СМ p54/56): модели в первой колонке,
# но колонки — учётные поля (шт./масса одной детали/применяемость), не параметры
_PARTS_COL_MARKERS = (
    "кол. шт", "кол шт", "кол-во", "количество", "масса 1", "масса 1шт",
    "масса одной", "применяемость", "нормативно- тех", "быстроизнашиваемых",
    "наименование детали", "материал",
)


def _is_parts_list(rows: list[list[Cell]], model_rows: list[int]) -> bool:
    """Перечень деталей: шапка содержит учётные колонки, а не параметры.

    Проверяются отдельные ячейки шапки (не склейка строки): у матрицы ТХ
    ячейка «Обозначение насоса (агрегата)» не должна давать срабатывание.
    """
    for i in range(model_rows[0]):
        for c in rows[i]:
            t = _cell_text(c).lower()
            if t and any(m in t for m in _PARTS_COL_MARKERS):
                return True
    return False


def detect_matrix_structure(t: Table) -> dict:
    """Определить матричную структуру модель×параметр и ориентацию.

    Возвращает {"is_matrix": bool, "orientation": "rows"|"columns"|None}:
    - "rows" — модели в первой колонке, параметры в колонках (НС, СМ, ЭЦВ);
    - "columns" — модели в шапке, параметры в строках (1Кс «Таблица 1», VP).

    Признаки «rows»: >=3 строк, первая колонка которых — модель, при >=2
    колонках значений; строка шапки над телом содержит названия параметров.
    Признаки «columns»: >=3 ячеек моделей в одной из первых строк, ниже —
    строки «параметр + значения».
    """
    rows = t.rows
    if len(rows) < MATRIX_MIN_MODELS + 1 or t.n_cols < 3:
        return {"is_matrix": False, "orientation": None}

    # --- ориентация «rows»: модели в первой колонке -------------------------
    model_rows = [i for i, row in enumerate(rows)
                  if row and _cell_models(row[0])]
    if model_rows:
        # колонки со значениями в теле
        counts: dict[int, int] = {}
        for i in model_rows:
            for j in range(1, len(rows[i])):
                c = rows[i][j]
                if c.is_merged or not _cell_text(c):
                    continue
                if _looks_like_value(_cell_text(c)):
                    counts[j] = counts.get(j, 0) + 1
        thr = max(2, int(len(model_rows) * 0.35))
        data_cols = [j for j in sorted(counts) if counts[j] >= thr]
        if len(data_cols) >= MATRIX_MIN_PARAM_COLS and len(model_rows) >= MATRIX_MIN_MODELS:
            # шапка: строка с названиями параметров над телом (или merged-шапка)
            first = model_rows[0]
            # шапка: >=2 ячеек-параметров над телом (мульти-колоночная шапка;
            # случайный заголовок «Таблица 5- Нагрузки...» не считается)
            hdr_cells = [
                c for i in range(first) for c in rows[i]
                if _param_label(_cell_text(c))
            ]
            has_hdr = len(hdr_cells) >= 2
            # merged-таблицы (НС): шапка всегда над телом, один раз на серию
            if has_hdr or t.has_merged_cells:
                # перечень деталей («Кольцо 220-230-30»...) — не матрица:
                # первая колонка похожа на модели, но колонки «Кол. шт.»/
                # «Масса» не совпадают с названиями параметров насоса
                if _is_parts_list(rows, model_rows):
                    return {"is_matrix": False, "orientation": None}
                return {"is_matrix": True, "orientation": "rows"}

    # --- ориентация «columns»: модели в шапке ------------------------------
    for i, row in enumerate(rows[:4]):
        model_cells = _header_model_cells(row)
        if len(model_cells) < MATRIX_MIN_MODELS:
            continue
        # чистые коды («2522») неотличимы от значений — строка значений
        # не даёт >=3 кодовых ячеек подряд без последующего текста

        # ниже — строки «параметр + значения»: значения в модельных колонках
        # и текстовая подпись слева от них
        model_cols = {c.col for c in model_cells}
        param_rows = 0
        for r in rows[i + 1:]:
            vals = any(c.col in model_cols and not c.is_merged
                       and _looks_like_value(_cell_text(c))
                       for c in r if _cell_text(c))
            label = any(c.col not in model_cols and _cell_text(c)
                        and not _looks_like_value(_cell_text(c)) for c in r)
            if vals and label:
                param_rows += 1
        if param_rows >= MATRIX_MIN_DATA_ROWS:
            return {"is_matrix": True, "orientation": "columns"}

    return {"is_matrix": False, "orientation": None}


def annotate_matrix(t: Table, info: dict | None = None) -> Table:
    """Проставить is_matrix/orientation по эвристикам (in place).

    `info` — уже вычисленная структура (detect_matrix_structure), чтобы не
    считать её повторно при совместном вызове с detect_header_rows.
    """
    if info is None:
        info = detect_matrix_structure(t)
    t.is_matrix = info["is_matrix"]
    t.orientation = info["orientation"]
    return t


# --- определение строк заголовка (подэтап 2.5) ------------------------------

# сколько верхних строк сканируем на границу «шапка → данные»
HEADER_SCAN_ROWS = 6
# подписи таблиц над шапкой — не заголовок
_CAPTION_RE = re.compile(r"^(таблица|продолжение\s+табл|окончание\s+табл)(?![а-яёa-z])", re.IGNORECASE)


def _is_caption_row(row: list[Cell]) -> bool:
    """Подпись таблицы («Таблица 1», «Продолжение табл. 2»): единственная текстовая
    ячейка на строку, без параметров и значений."""
    texts = [c for c in row if _cell_text(c)]
    if len(texts) != 1:
        return False
    t = texts[0].text if hasattr(texts[0], "text") else ""
    return bool(_CAPTION_RE.match(collapse_ws(t).strip()))


def _is_header_row(row: list[Cell]) -> bool:
    """Строка-заголовок: содержит ячейки-параметры или словарные заголовки."""
    texts = [_cell_text(c) for c in row if _cell_text(c)]
    if not texts:
        return False
    if _is_caption_row(row):
        return False
    if any(_param_label(t) for t in texts):
        return True
    low = [t.lower() for t in texts]
    return any(v in t for t in low for v in HEADER_VOCAB)


def detect_header_rows(t: Table, info: dict | None = None) -> int:
    """Количество строк заголовка (0 = шапки нет).

    `info` — уже вычисленная матричная структура (detect_matrix_structure),
    чтобы не считать её повторно при совместном вызове с annotate_matrix.

    Граница «шапка → данные»: последняя строка-заголовок перед первой строкой
    данных (>=2 значений или модель в первой колонке). Для матриц используется
    их структура: rows — шапка до первой модельной строки; columns — до первой
    строки «параметр + значения». Подписи таблиц («Таблица 1») не считаются
    шапкой, но и не разрывают её (шапка после подписи).
    """
    rows = t.rows
    if not rows:
        return 0
    if info is None:
        info = detect_matrix_structure(t)

    def _first_data_row() -> int | None:
        # columns-матрица: строка кодов моделей в шапке — заголовок, данные
        # начинаются со строк «параметр + значения» (коды «2522» неотличимы
        # от числовых значений, поэтому общий проход их пропускает)
        if info.get("orientation") == "columns":
            model_row = None
            for i, row in enumerate(rows[:4]):
                if _header_model_cells(row):
                    model_row = i
                    break
            if model_row is not None:
                for i, row in enumerate(rows[model_row + 1:HEADER_SCAN_ROWS], model_row + 1):
                    texts = [_cell_text(c) for c in row if _cell_text(c)]
                    if not texts:
                        continue
                    if _param_label(texts[0]) or _row_value_count(row) >= 2:
                        return i
                return None
        for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
            if _is_caption_row(row) or not any(_cell_text(c) for c in row):
                continue
            if row and _cell_models(row[0]):
                return i
            if _row_value_count(row) >= 2:
                return i
            if _header_model_cells(row):
                # шапка columns: строка кодов моделей — сама заголовок
                continue
        return None

    first = _first_data_row()
    if first is None or first == 0:
        return 0
    # считаем строки-заголовки над первой строкой данных; мусорные строки
    # (подписи/пустые) пропускаем — шапкой считаем только заголовочные
    # строка-заголовок: словарная/param-ячейка, строка кодов моделей columns-
    # матрицы или чисто текстовая строка без значений; подписи и пустые
    # строки не считаются (счёт — по строкам, не по индексам)
    count = 0
    for i in range(first):
        row = rows[i]
        if _is_caption_row(row) or not any(_cell_text(c) for c in row):
            continue
        if info.get("orientation") == "columns" and _header_model_cells(row):
            count += 1
        elif _is_header_row(row) or _row_value_count(row) == 0:
            count += 1
    if count == 0:
        # ни одной явной строки-заголовка, но данные начинаются не с первой
        # строки (merged-шапка без словаря) — считаем все строки до данных шапкой
        count = first if t.has_merged_cells else 0
    return count


def annotate_table(t: Table) -> Table:
    """Проставить header_rows + is_matrix/orientation (in place).

    Матричная структура считается один раз и переиспользуется обоими
    определителями (раньше detect_matrix_structure вызывался дважды).
    """
    info = detect_matrix_structure(t)
    t.header_rows = detect_header_rows(t, info)
    annotate_matrix(t, info)
    return t


# Минимальная плотность заполнения grid-таблицы, принимаемой по признаку
# merged cells: разреженные сетки (фоновая разлиновка анкет, «миллиметровка»)
# дают ложные таблицы 100x30 с пустыми ячейками.
GRID_MERGE_DENSITY = 0.15


def _grid_density(t: Table) -> float:
    """Доля ячеек с текстом (якоря merged считаются, продолжения — нет)."""
    total = sum(len(r) for r in t.rows)
    if not total:
        return 0.0
    filled = sum(1 for r in t.rows for c in r if c.text.strip())
    return filled / total


def _is_grid_model_code(text: str) -> bool:
    """Код типоразмера в ячейке шапки («20-50», «1Кс12-50-...», «1К50-32-125»)."""
    s = re.sub(r"\s+", " ", text).strip()
    s = re.sub(r"[.\u2026]+$", "", s).rstrip("-–—")
    return bool(s) and re.search(r"\d", s) and bool(_GRID_MODEL_CODE_RE.match(s))


def _grid_is_transposed(t: Table) -> bool:
    """Ориентация «модели в шапке»: в одной из первых строк >=3 ячеек-кодов моделей."""
    for row in t.rows[:4]:
        hits = [c for c in row[1:] if c.text.strip() and _is_grid_model_code(c.text)]
        if len(hits) >= 3:
            return True
    return False


def merge_page_tables(page_number, grid_tables, word_tables) -> list[Table]:
    """Консервативное слияние сеточного и словесного детекторов.

    Grid-таблица попадает в результат, только если она:
    - не дублирует словесную таблицу (её bbox не покрыт более чем на 50%
      одной словесной таблицей той же области);
    - несёт контент, недоступный словесному детектору:
      * «транспонированную» матрицу с моделями в шапке (строки = показатели),
        где подпись показателя занимает несколько строк (1Кs Таблицы 1/2), или
      * merged cells (rowspan/colspan) — словесный детектор режет такие
        таблицы на куски (НС «Таблица 1»: 6 кусков вместо одной таблицы);
        отсекаются разреженные сетки (плотность < GRID_MERGE_DENSITY) —
        фоновая разлиновка анкет даёт ложные таблицы 100x30 почти без текста.

    Словесные куски, покрытые более чем на 50% оставшейся grid-таблицей,
    отбрасываются (сетка собирает их точнее).
    """
    kept_grid = []
    for g in grid_tables:
        duplicated = any(_covered_fraction(g.bbox, w.bbox) > 0.5 for w in word_tables)
        if duplicated:
            continue
        if _grid_is_transposed(g):
            kept_grid.append(g)
        elif g.has_merged_cells and _grid_density(g) >= GRID_MERGE_DENSITY:
            # merged-таблица (rowspan/colspan): словесный детектор режет её на куски
            kept_grid.append(g)
    kept_word = []
    for w in word_tables:
        covered = any(_covered_fraction(w.bbox, g.bbox) > 0.5 for g in kept_grid)
        if not covered:
            kept_word.append(w)
    out = list(kept_word) + list(kept_grid)
    for idx, t in enumerate(out):
        t.id = f"p{page_number + 1}-t{idx + 1}"
        annotate_table(t)
    return out
