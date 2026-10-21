/* Чистые операции над таблицей (TableOut) для визуального маппера (3.5).
 *
 * Модель таблицы (контракт этапа 2) — плоский cells с якорями merged-блоков
 * (текст + rowspan/colspan) и continuation-слотами (is_merged + merged_with).
 * Для структурных правок неудобна: операции над списком БЛОКОВ
 * {row, col, rowspan, colspan, text} — toBlocks/fromBlocks нормализуют
 * туда-обратно (дыры сетки при этом заполняются пустыми ячейками).
 *
 * Каждая операция мутирует переданный table и возвращает
 * {changed, remap}: remap — [[ключЯкоряСтарый, ключНовый|null], ...]
 * для переноса аннотаций (ключ — "row,col" якоря).
 */
"use strict";

/* --- нормализация cells ↔ блоки --- */

/** cells → блоки (continuation-слоты резолвятся к якорям). */
export function toBlocks(table) {
  const grid = new Map();
  for (const c of table.cells || []) grid.set(`${c.row},${c.col}`, c);
  const blocks = [];
  for (const c of table.cells || []) {
    if (c.is_merged && c.merged_with) {
      const a = grid.get(`${c.merged_with[0]},${c.merged_with[1]}`);
      // continuation с живым якорем покрыт блоком якоря; битый — сам блок
      if (a && !a.is_merged) continue;
    }
    blocks.push({
      row: c.row,
      col: c.col,
      rowspan: Math.max(1, c.rowspan || 1),
      colspan: Math.max(1, c.colspan || 1),
      text: c.text || "",
    });
  }
  return blocks;
}

/** Блоки → cells: якорь + continuation-слоты; пересчёт has_merged_cells. */
export function fromBlocks(table, blocks) {
  const cells = [];
  for (const b of blocks) {
    cells.push({
      row: b.row, col: b.col,
      rowspan: b.rowspan, colspan: b.colspan,
      text: b.text, is_merged: false, merged_with: null,
    });
    for (let i = b.row; i < b.row + b.rowspan; i++) {
      for (let j = b.col; j < b.col + b.colspan; j++) {
        if (i === b.row && j === b.col) continue;
        cells.push({
          row: i, col: j, rowspan: 1, colspan: 1,
          text: "", is_merged: true, merged_with: [b.row, b.col],
        });
      }
    }
  }
  table.cells = cells;
  table.has_merged_cells = blocks.some((b) => b.rowspan > 1 || b.colspan > 1);
}

/* --- вспомогательное --- */

const blockEnd = (b) => ({ r1: b.row + b.rowspan - 1, c1: b.col + b.colspan - 1 });

/** Блок и прямоугольник пересекаются? */
function intersects(b, r0, c0, r1, c1) {
  const e = blockEnd(b);
  return b.row <= r1 && e.r1 >= r0 && b.col <= c1 && e.c1 >= c0;
}

/** Блок целиком внутри прямоугольника? */
function inside(b, r0, c0, r1, c1) {
  const e = blockEnd(b);
  return b.row >= r0 && e.r1 <= r1 && b.col >= c0 && e.c1 <= c1;
}

/**
 * Скомпоновать последовательные remap-ы: acc — [[orig, cur|null]], next —
 * [[from, to|null]] применяется к cur-состоянию. Удалённые (null) не
 * воскресают, повторные затирания не создают дублей.
 */
function composeRemap(acc, next) {
  const out = acc.map((e) => [e[0], e[1]]);
  for (const [from, to] of next) {
    let touched = false;
    for (const e of out) {
      if (e[1] === from) { e[1] = to; touched = true; }
      if (e[0] === from) touched = true; // ключ уже учтён (в т.ч. удалённый)
    }
    if (!touched) out.push([from, to]);
  }
  return out;
}

/* --- операции --- */

/**
 * Объединить прямоугольник [r0..r1]×[c0..c1] в одну ячейку. Прямоугольник
 * расширяется до полного покрытия частично затронутых merged-блоков
 * (блок нельзя слить частично). Тексты поглощённых блоков join-ятся.
 */
export function mergeRect(table, r0, c0, r1, c1) {
  const blocks = toBlocks(table);
  let rect = {
    r0: Math.min(r0, r1), c0: Math.min(c0, c1),
    r1: Math.max(r0, r1), c1: Math.max(c0, c1),
  };
  // расширение до фикс-пойнта: любой пересечённый блок входит целиком
  let grew = true;
  while (grew) {
    grew = false;
    for (const b of blocks) {
      if (!intersects(b, rect.r0, rect.c0, rect.r1, rect.c1)) continue;
      const e = blockEnd(b);
      const nr0 = Math.min(rect.r0, b.row), nc0 = Math.min(rect.c0, b.col);
      const nr1 = Math.max(rect.r1, e.r1), nc1 = Math.max(rect.c1, e.c1);
      if (nr0 !== rect.r0 || nc0 !== rect.c0 || nr1 !== rect.r1 || nc1 !== rect.c1) {
        rect = { r0: nr0, c0: nc0, r1: nr1, c1: nc1 };
        grew = true;
      }
    }
  }
  if (rect.r0 === rect.r1 && rect.c0 === rect.c1) return { changed: false, remap: [] };

  const absorbed = blocks.filter((b) => inside(b, rect.r0, rect.c0, rect.r1, rect.c1));
  // no-op: прямоугольник уже совпадает с одним существующим блоком
  if (absorbed.length === 1 && absorbed[0].row === rect.r0 && absorbed[0].col === rect.c0 &&
      blockEnd(absorbed[0]).r1 === rect.r1 && blockEnd(absorbed[0]).c1 === rect.c1) {
    return { changed: false, remap: [] };
  }

  // выживает аннотация блока, накрывающего верхний-левый слот итогового прямоугольника
  const survivor = absorbed.find((b) =>
    rect.r0 >= b.row && rect.r0 <= blockEnd(b).r1 &&
    rect.c0 >= b.col && rect.c0 <= blockEnd(b).c1);
  const remap = absorbed.map((b) => {
    const key = `${b.row},${b.col}`;
    return [key, b === survivor ? `${rect.r0},${rect.c0}` : null];
  });

  // тексты поглощённых блоков — по порядку (row, col), как в исходной таблице
  const texts = absorbed
    .slice()
    .sort((a, b) => a.row - b.row || a.col - b.col)
    .map((b) => (b.text || "").trim())
    .filter(Boolean);

  const kept = blocks.filter((b) => !inside(b, rect.r0, rect.c0, rect.r1, rect.c1));
  kept.push({
    row: rect.r0, col: rect.c0,
    rowspan: rect.r1 - rect.r0 + 1, colspan: rect.c1 - rect.c0 + 1,
    text: texts.join(" "),
  });
  fromBlocks(table, kept);
  return { changed: true, remap };
}

/**
 * Разделить merged-блоки, накрытые слотами [{row, col}]: каждый — на 1×1.
 * Текст остаётся на слоте якоря (ключ аннотации не меняется).
 */
export function splitBlocks(table, slots) {
  const blocks = toBlocks(table);
  const out = [];
  let changed = false;
  for (const b of blocks) {
    const hit = slots.some((s) =>
      s.row >= b.row && s.row < b.row + b.rowspan &&
      s.col >= b.col && s.col < b.col + b.colspan);
    if (!hit || (b.rowspan === 1 && b.colspan === 1)) { out.push(b); continue; }
    changed = true;
    for (let i = b.row; i < b.row + b.rowspan; i++) {
      for (let j = b.col; j < b.col + b.colspan; j++) {
        out.push({ row: i, col: j, rowspan: 1, colspan: 1, text: i === b.row && j === b.col ? b.text : "" });
      }
    }
  }
  if (!changed) return { changed: false, remap: [] };
  fromBlocks(table, out);
  return { changed: true, remap: [] };
}

/**
 * Транспонировать таблицу (строки ↔ колонки). Спаны и merged_with
 * меняются местами вместе с координатами; у матрицы флипается orientation.
 */
export function transposeTable(table) {
  const src = toBlocks(table);
  if (!src.length) return { changed: false, remap: [] };
  const blocks = src.map((b) => ({
    row: b.col, col: b.row,
    rowspan: b.colspan, colspan: b.rowspan,
    text: b.text,
  }));
  const remap = blocks.map((b) => [`${b.col},${b.row}`, `${b.row},${b.col}`]);
  fromBlocks(table, blocks);
  const t = table.n_rows;
  table.n_rows = table.n_cols;
  table.n_cols = t;
  if (table.is_matrix) {
    table.orientation = table.orientation === "columns" ? "rows" : "columns";
  }
  return { changed: true, remap };
}

/** Удалить строки по индексам (уникальным; удаляем с конца — индексы не плывут). */
export function deleteRows(table, indices) {
  return _deleteAxis(table, [...new Set(indices)].sort((a, b) => a - b), "row");
}

/** Удалить колонки по индексам. */
export function deleteCols(table, indices) {
  return _deleteAxis(table, [...new Set(indices)].sort((a, b) => a - b), "col");
}

function _deleteAxis(table, indices, axis) {
  const isRow = axis === "row";
  const sizeKey = isRow ? "n_rows" : "n_cols";
  const spanKey = isRow ? "rowspan" : "colspan";
  const posKey = isRow ? "row" : "col";
  let changed = false;
  let remap = [];
  for (let k = indices.length - 1; k >= 0; k--) {
    const idx = indices[k];
    if (idx < 0 || idx >= table[sizeKey]) continue;
    const blocks = toBlocks(table);
    const out = [];
    const stepRemap = [];
    for (const b of blocks) {
      const pos = b[posKey];
      const spans = pos <= idx && idx < pos + b[spanKey];
      if (pos === idx && b[spanKey] === 1) {
        // блок целиком в удаляемой строке/колонке — исчезает вместе с аннотацией
        stepRemap.push([`${b.row},${b.col}`, null]);
        continue;
      }
      if (spans) {
        // блок спанит через удалённую линию — сжимается, якорь остаётся с текстом
        out.push({ ...b, [spanKey]: b[spanKey] - 1 });
        continue;
      }
      if (pos > idx) {
        out.push({ ...b, [posKey]: pos - 1 });
        stepRemap.push([`${b.row},${b.col}`, isRow ? `${b.row - 1},${b.col}` : `${b.row},${b.col - 1}`]);
        continue;
      }
      out.push(b);
    }
    table[sizeKey] -= 1;
    if (isRow && table.header_rows > idx) table.header_rows = Math.max(0, table.header_rows - 1);
    fromBlocks(table, out);
    remap = composeRemap(remap, stepRemap);
    changed = true;
  }
  return { changed, remap };
}

/* --- воспроизведение правок по рецепту (3.7) ------------------------------- */

/**
 * Применить к таблице ops-рецепт структурных правок (мутабельно, как сами
 * операции). Спеки пишет VisualMapper при выполнении операций (getOps):
 *   {op:"merge", rect:[r0,c0,r1,c1]} — прямоугольник выделения как есть;
 *   {op:"split", slots:[[r,c],...]}  — точные слоты выделения;
 *   {op:"transpose"};
 *   {op:"del_rows", rows:[...]}, {op:"del_cols", cols:[...]}.
 * No-op и неизвестные спеки пропускаются (рецепт устойчив к частично
 * неподходящей структуре). Remap не нужен: аннотации шаблона хранятся
 * в финальных координатах.
 */
export function applyOps(table, ops) {
  for (const spec of ops || []) {
    if (!spec || typeof spec !== "object") continue;
    if (spec.op === "merge" && Array.isArray(spec.rect) && spec.rect.length === 4) {
      mergeRect(table, spec.rect[0], spec.rect[1], spec.rect[2], spec.rect[3]);
    } else if (spec.op === "split" && Array.isArray(spec.slots) && spec.slots.length) {
      splitBlocks(table, spec.slots.map(([r, c]) => ({ row: r, col: c })));
    } else if (spec.op === "transpose") {
      transposeTable(table);
    } else if (spec.op === "del_rows" && Array.isArray(spec.rows)) {
      deleteRows(table, spec.rows);
    } else if (spec.op === "del_cols" && Array.isArray(spec.cols)) {
      deleteCols(table, spec.cols);
    }
  }
  return table;
}
