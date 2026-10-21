import { readFileSync } from "node:fs";
import { TableRenderer, COL_HDR_W, ROW_HDR_H } from "./table-renderer.js";
import { VisualMapper } from "./canvas.js";
import * as tableOps from "./table-ops.js";
import { buildPreview, toCsv } from "./preview.js";

const path = process.argv[2] || "../../out-debug/vm_test_table.json";
const data = JSON.parse(readFileSync(path, "utf8"));
const tables = Array.isArray(data) ? data : [data];
let failures = 0;
const check = (name, cond, extra = "") => {
  if (!cond) { failures++; console.error("FAIL:", name, extra); }
};

const fakeCanvas = () => ({ add() {}, remove() {}, clear() {}, setDimensions() {}, setViewportTransform() {}, requestRenderAll() {} });

/** "2,3,4" — список значений от a до b (для сравнения диапазонов). */
const rangeList = (a, b) => {
  const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i).join();
};

/* --- базовые инварианты одной таблицы (возвращают контекст для спец-проверок) --- */
function coreInvariants(table, tag) {
  const r = new TableRenderer(fakeCanvas(), table);
  const grid = r.buildGrid();
  check(`${tag}: grid = cells`, grid.size === table.cells.length, `${grid.size}/${table.cells.length}`);

  // раскладка конечна и монотонна
  check(`${tag}: offX конечна и монотонна`, r.offX.every((v, i, a) => Number.isFinite(v) && (i === 0 || v >= a[i - 1])));
  check(`${tag}: offY конечна и монотонна`, r.offY.every((v, i, a) => Number.isFinite(v) && (i === 0 || v >= a[i - 1])));

  const anchors = [...grid.values()].filter(c => !c.is_merged && r._inGrid(c));
  const rects = anchors.map(c => ({
    x: r.offX[c.col], y: r.offY[c.row],
    w: r._spanWidth(c, "col"), h: r._spanWidth(c, "row"), c,
  }));
  check(`${tag}: якоря в границах`, rects.every(rc =>
    rc.x >= COL_HDR_W - 0.01 && rc.y >= ROW_HDR_H - 0.01 &&
    rc.x + rc.w <= r.totalW + 0.01 && rc.y + rc.h <= r.totalH + 0.01));
  let overlap = null;
  outer: for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i], b = rects[j];
    const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    if (ox > 1 && oy > 1) { overlap = [a.c, b.c]; break outer; }
  }
  check(`${tag}: якоря не пересекаются`, !overlap, overlap ? JSON.stringify(overlap.map(c => [c.row, c.col])) : "");

  let resolveOk = true;
  for (const c of grid.values()) {
    if (!c.is_merged || !c.merged_with) continue;
    const a = r.anchorAt(c.row, c.col);
    if (!a || a.is_merged || a.row !== c.merged_with[0] || a.col !== c.merged_with[1]) { resolveOk = false; break; }
  }
  check(`${tag}: продолжения → якорь`, resolveOk);

  const objs = { list: [], add(...o) { this.list.push(...o); }, remove() {}, clear() { this.list = []; },
    setDimensions() {}, setViewportTransform() {}, requestRenderAll() {} };
  const r2 = new TableRenderer(objs, table);
  r2.render();
  const rectObjs = objs.list.filter(o => o.data && o.data.row !== undefined);
  check(`${tag}: render: Rect = якорей`, rectObjs.length === anchors.length, `${rectObjs.length}/${anchors.length}`);
  const hdrRects = objs.list.filter(o => !o.data && !o.text);
  check(`${tag}: оси: угол + ${r.nCols} кол + ${r.nRows} строк`, hdrRects.length === 1 + r.nCols + r.nRows,
    `${hdrRects.length}`);

  return { r };
}

/* --- прогон всех таблиц --- */
for (let i = 0; i < tables.length; i++) {
  const tag = Array.isArray(data) ? `t${i}(${tables[i].id})` : "t";
  coreInvariants(tables[i], tag);
  opsRoundTrip(tables[i], tag);
}
const table = tables[0];
console.log(`таблиц обработано: ${tables.length}`);

/* --- зум --- */
{
  const { r } = coreInvariants(table, "zoom");
  r.setZoom(99); const hi = r.zoom;
  r.setZoom(0.01); const lo = r.zoom;
  check("zoom: clamp 0.3..3", hi <= 3 && lo >= 0.3, `${hi}/${lo}`);
  r.setZoom(1);
  r.fitWidth(r.totalW / 2);
  check("fitWidth: ужимает до 0.5", Math.abs(r.zoom - 0.5) < 0.01, r.zoom.toFixed(3));
  r.fitWidth(r.totalW * 10);
  check("fitWidth: не увеличивает выше 1", r.zoom === 1, r.zoom.toFixed(3));
}

/* --- слои подсветки рендерера --- */
{
  const { r } = coreInvariants(table, "layers");
  r.highlightCell(0, 0, "#111111", { layer: "selection" });
  r.highlightCell(0, 0, "#222222", { layer: "annotations" });
  r.highlightCell(0, 0, "#333333", { layer: "annotations" });
  check("overlayCount: по слоям", r.overlayCount("selection") === 1 && r.overlayCount("annotations") === 2,
    `${r.overlayCount("selection")}/${r.overlayCount("annotations")}`);
  check("overlayCount: всего", r.overlayCount() === 3, `${r.overlayCount()}`);
  r.clearOverlays("selection");
  check("clearOverlays(слой) чистит только слой", r.overlayCount("selection") === 0 && r.overlayCount("annotations") === 2);
  r.clearOverlays();
  check("clearOverlays() чистит все", r.overlayCount() === 0);
}

/* --- wheel-зум --- */
{
  const zooms = [];
  const mapper = new VisualMapper({ id: "c" }, table, { onZoom: (z) => zooms.push(z) });
  mapper.render();
  mapper.canvas.fire("mouse:wheel", { e: { ctrlKey: true, deltaY: -100, preventDefault() {}, stopPropagation() {} } });
  check("wheel: Ctrl+вверх увеличивает", mapper.getZoom() > 1, mapper.getZoom().toFixed(3));
  const zBefore = mapper.getZoom(), nBefore = zooms.length;
  mapper.canvas.fire("mouse:wheel", { e: { ctrlKey: false, deltaY: -100, preventDefault() {}, stopPropagation() {} } });
  check("wheel: без Ctrl зум не меняется", mapper.getZoom() === zBefore && zooms.length === nBefore, `${zooms.length}`);
  check("onZoom колбэк стрелял", zooms.length >= 1);
}

/* --- выделение: клик / drag / Ctrl / Shift / фон --- */
{
  const cell = (row, col) => ({ target: { data: { row, col } }, e: {} });
  const events = [];
  const mapper = new VisualMapper({ id: "c" }, table, {
    onSelectionChange: (sel) => events.push(["sel", sel.length]),
    onCellClick: () => events.push(["click"]),
  });
  mapper.render();

  // опорные точки: обычные (не merged, span 1) ячейки в разных строках/колонках
  const plain = table.cells.filter(c => !c.is_merged && (c.rowspan || 1) === 1 && (c.colspan || 1) === 1);
  const a = plain[0];
  const b = plain.find(c => c.row !== a.row && c.col !== a.col) || plain[plain.length - 1];
  const area = (p, q) =>
    (Math.abs(p.row - q.row) + 1) * (Math.abs(p.col - q.col) + 1);
  const ctrl = plain.find(c => (c.row - a.row) * (c.col - a.col) === 0 &&
    Math.abs(c.row - b.row) + Math.abs(c.col - b.col) > 0 && c !== a && c !== b &&
    !((c.row >= Math.min(a.row, b.row) && c.row <= Math.max(a.row, b.row)) &&
      (c.col >= Math.min(a.col, b.col) && c.col <= Math.max(a.col, b.col))));

  // одиночный клик
  mapper.canvas.fire("mouse:down", cell(a.row, a.col));
  mapper.canvas.fire("mouse:up", cell(a.row, a.col));
  check("клик: 1 ячейка", mapper.getSelection().length === 1 && mapper.getSelection()[0].row === a.row);
  check("клик: onSelectionChange", events.some(([t, n]) => t === "sel" && n === 1));

  // drag-диапазон a → b
  events.length = 0;
  mapper.canvas.fire("mouse:down", cell(a.row, a.col));
  mapper.canvas.fire("mouse:move", cell(b.row, b.col));
  mapper.canvas.fire("mouse:up", cell(b.row, b.col));
  const sel = mapper.getSelection();
  check("drag: прямоугольник между ячейками", sel.length === area(a, b), `${sel.length} vs ${area(a, b)}`);
  const rows = [...new Set(sel.map(s => s.row))].sort(), cols = [...new Set(sel.map(s => s.col))].sort();
  check("drag: диапазон прямоугольный",
    rows.join() === rangeList(a.row, b.row) && cols.join() === rangeList(a.col, b.col));
  // якоря кликнутых ячеек затёрты диапазоном
  check("drag: onSelectionChange (превью+фиксация)", events.some(([t, n]) => t === "sel" && n === area(a, b)));

  // Ctrl+Click: добавить / убрать ячейку вне диапазона
  if (ctrl) {
    mapper.canvas.fire("mouse:down", { target: { data: { row: ctrl.row, col: ctrl.col } }, e: { ctrlKey: true } });
    check("Ctrl+клик: добавил ячейку", mapper.getSelection().length === area(a, b) + 1, `${mapper.getSelection().length}`);
    mapper.canvas.fire("mouse:down", { target: { data: { row: ctrl.row, col: ctrl.col } }, e: { ctrlKey: true } });
    check("Ctrl+клик: убрал ячейку", mapper.getSelection().length === area(a, b), `${mapper.getSelection().length}`);
  } else console.log("нет ячейки вне диапазона — пропуск Ctrl-проверки");

  // Shift+Click: расширение от последнего якоря
  mapper.canvas.fire("mouse:down", cell(a.row, a.col));
  mapper.canvas.fire("mouse:up", cell(a.row, a.col)); // _lastAnchor = a
  mapper.canvas.fire("mouse:down", { target: { data: { row: b.row, col: b.col } }, e: { shiftKey: true } });
  check("Shift+клик: расширение до диапазона a×b", mapper.getSelection().length === area(a, b),
    `${mapper.getSelection().length} vs ${area(a, b)}`);

  // клик по фону: очистка
  mapper.canvas.fire("mouse:down", { target: null, e: {} });
  check("клик по фону: выделение снято", mapper.getSelection().length === 0);
}

/* --- выделение + merged: продолжение даёт подсветку якоря, без дублей --- */
{
  const cont = table.cells.find(c => c.is_merged && c.merged_with);
  if (cont) {
    const mapper = new VisualMapper({ id: "c" }, table, {});
    mapper.render();
    mapper.canvas.fire("mouse:down", { target: { data: { row: cont.row, col: cont.col } }, e: {} });
    mapper.canvas.fire("mouse:up", { target: { data: { row: cont.row, col: cont.col } }, e: {} });
    check("merged: клик по продолжению → 1 слот-якорь",
      mapper.getSelection().length === 1 && mapper.getSelection()[0].row === cont.merged_with[0]);
    check("merged: одна подсветка якоря", mapper.renderer.overlayCount("selection") === 1,
      `${mapper.renderer.overlayCount("selection")}`);
  } else console.log("нет merged-продолжений — пропуск merged-проверок выделения");
}

/* --- роли: параметры, merged-нормализация, слои, undo/redo --- */
{
  // опорные точки: обычные (не merged, span 1) ячейки — разные якоря
  const plain = table.cells.filter(c => !c.is_merged && (c.rowspan || 1) === 1 && (c.colspan || 1) === 1);
  const a = plain[0], b = plain.find(c => c.row !== a.row && c.col !== a.col) || plain[plain.length - 1];
  const events = [];
  const mapper = new VisualMapper({ id: "c" }, table, {
    onAnnotationsChange: (anns) => events.push(anns.length),
    onHistoryChange: () => events.push("hist"),
  });
  mapper.render();

  // назначение с параметрами
  const n = mapper.assignRole([a, b], "param", { label: "Подача", unit: "м3/ч", group: "Гидравлика" });
  check("роли: назначены 2 якорям", n === 2, `${n}`);
  const anns = mapper.getAnnotations();
  check("роли: параметры сохранены",
    anns.length === 2 && anns.every(x => x.role === "param" && x.label === "Подача" && x.unit === "м3/ч" && x.group === "Гидравлика"));
  check("роли: подсвечены 2", mapper.renderer.overlayCount("annotations") === 2,
    `${mapper.renderer.overlayCount("annotations")}`);

  // no-op: та же роль тем же ячейкам — не действие истории
  const n2 = mapper.assignRole([a, b], "param", { label: "Подача", unit: "м3/ч", group: "Гидравлика" });
  check("роли: no-op не считается", n2 === 0, `${n2}`);

  // замена роли — перезапись аннотации
  mapper.assignRole([a], "value");
  const a1 = mapper.getAnnotations().find(x => x.row === a.row && x.col === a.col);
  check("роли: замена перезаписывает", a1.role === "value" && a1.label === undefined);

  // merged: слоты одного блока → одна аннотация на якоре
  const cont = table.cells.find(c => c.is_merged && c.merged_with);
  if (cont) {
    const [ar, ac] = cont.merged_with;
    const nm = mapper.assignRole([cont, { row: ar, col: ac }], "model");
    check("merged: слоты блока → 1 аннотация", nm === 1, `${nm}`);
    const anchor = mapper.getAnnotations().find(x => x.row === ar && x.col === ac);
    check("merged: аннотация на якоре", anchor && anchor.role === "model");
  }

  // applyRoleToSelection: без выделения → 0
  check("applyRoleToSelection: без выделения 0", mapper.applyRoleToSelection("unit") === 0);
  mapper.selectRange({ row: a.row, col: a.col }, { row: b.row, col: b.col });
  check("applyRoleToSelection: с выделением > 0", mapper.applyRoleToSelection("unit", { unit: "кВт" }) > 0);

  // снятие роли (null)
  const before = mapper.getAnnotations().length;
  const nrm = mapper.assignRole([a], null);
  check("снятие роли: 1", nrm === 1 && mapper.getAnnotations().length === before - 1);

  // слои не конфликтуют
  const annCount = mapper.renderer.overlayCount("annotations");
  mapper.selectRange({ row: 0, col: 0 }, { row: 2, col: 2 });
  check("выделение не затирает роли", mapper.renderer.overlayCount("annotations") === annCount);
  mapper.clearSelection();
  check("снятие выделения не трогает роли", mapper.renderer.overlayCount("annotations") === annCount);

  // undo / redo
  const total = mapper.getAnnotations().length;
  check("undo доступен", mapper.canUndo);
  mapper.undo(); // откат снятия роли
  check("undo: вернуло снятую роль", mapper.getAnnotations().length === total + 1);
  check("redo доступен после undo", mapper.canRedo);
  mapper.redo();
  check("redo: снова сняло", mapper.getAnnotations().length === total);
  check("redo-стек пуст после redo", !mapper.canRedo);
  // вся история: откатить до нуля, повторить до total
  while (mapper.canUndo) mapper.undo();
  check("undo: вся история → 0 аннотаций", mapper.getAnnotations().length === 0,
    `${mapper.getAnnotations().length}`);
  while (mapper.canRedo) mapper.redo();
  check("redo: вся история → total", mapper.getAnnotations().length === total,
    `${mapper.getAnnotations().length} vs ${total}`);
  check("пустой undo/redo — false", (() => { mapper._history = []; mapper._redoStack = []; return !mapper.undo() && !mapper.redo(); })());
  check("canUndo/canRedo false", !mapper.canUndo && !mapper.canRedo);

  // clearAnnotations — одно действие истории
  mapper.assignRole([a], "model");
  const beforeClear = mapper.getAnnotations().length;
  const nclear = mapper.clearAnnotations();
  check("clearAnnotations: все удалены", nclear === beforeClear && mapper.getAnnotations().length === 0);
  mapper.undo();
  check("undo после clearAnnotations восстанавливает", mapper.getAnnotations().length === beforeClear);

  // события стреляли
  check("onAnnotationsChange стрелял", events.filter((x) => typeof x === "number").length > 0);
  check("onHistoryChange стрелял", events.includes("hist"));

  // неизвестная роль → исключение
  let threw = false;
  try { mapper.assignRole([a], "nosuchrole"); } catch (_) { threw = true; }
  check("неизвестная роль → throw", threw);
}

/* --- операции с ячейками (3.5) --- */

/* Синтетика 5×4: шапка (hr=1), merged 2×2 «Подача» в (1,0), rowspan-блок
 * «Напор» (3,0), матрица rows. 20 клеток = все слоты сетки. */
const mkSynth = () => JSON.parse(JSON.stringify({
  id: "synth", page: 0, n_rows: 5, n_cols: 4, header_rows: 1,
  is_matrix: true, orientation: "rows", has_merged_cells: true,
  cells: [
    { row: 0, col: 0, text: "Параметр" }, { row: 0, col: 1, text: "ЦНС 13-70" },
    { row: 0, col: 2, text: "ЦНС 13-90" }, { row: 0, col: 3, text: "ЦНС 13-110" },
    { row: 1, col: 0, text: "Подача", rowspan: 2, colspan: 2 },
    { row: 1, col: 1, text: "", is_merged: true, merged_with: [1, 0] },
    { row: 2, col: 0, text: "", is_merged: true, merged_with: [1, 0] },
    { row: 2, col: 1, text: "", is_merged: true, merged_with: [1, 0] },
    { row: 1, col: 2, text: "70" }, { row: 1, col: 3, text: "90" },
    { row: 2, col: 2, text: "71" }, { row: 2, col: 3, text: "91" },
    { row: 3, col: 0, text: "Напор", rowspan: 2 },
    { row: 4, col: 0, text: "", is_merged: true, merged_with: [3, 0] },
    { row: 3, col: 1, text: "1" }, { row: 3, col: 2, text: "2" }, { row: 3, col: 3, text: "3" },
    { row: 4, col: 1, text: "4" }, { row: 4, col: 2, text: "5" }, { row: 4, col: 3, text: "6" },
  ],
}));
const cellAt = (t, r, c) => t.cells.find((x) => x.row === r && x.col === c);

/* чистые операции на синтетике (после каждой — инварианты рендера) */
{
  // merge вертикальный: тексты join
  const t = mkSynth();
  const res = tableOps.mergeRect(t, 1, 2, 2, 2);
  const a = cellAt(t, 1, 2);
  check("merge: changed", res.changed === true);
  check("merge: якорь rowspan 2", a.rowspan === 2 && !a.is_merged, `${a.rowspan}`);
  check("merge: тексты join", a.text === "70 71", a.text);
  const cont = cellAt(t, 2, 2);
  check("merge: continuation", cont.is_merged === true && JSON.stringify(cont.merged_with) === "[1,2]");
  coreInvariants(t, "ops-merge-v");
}
{
  // merge с расширением до целых merged-блоков
  const t = mkSynth();
  const res = tableOps.mergeRect(t, 0, 0, 1, 0); // задевает блок 2×2 в (1,0)
  const a = cellAt(t, 0, 0);
  check("merge+: расширен до 3×2", a.rowspan === 3 && a.colspan === 2, `${a.rowspan}x${a.colspan}`);
  check("merge+: тексты всех блоков", a.text.includes("Параметр") && a.text.includes("Подача"), a.text);
  // выживает аннотация блока, накрывающего верхний-левый слот (здесь — шапка (0,0))
  check("merge+: remap — выживает блок верхнего-левого слота",
    res.remap.some(([f, to]) => f === "0,0" && to === "0,0") && res.remap.some(([f, to]) => f === "1,0" && to === null),
    JSON.stringify(res.remap));
  coreInvariants(t, "ops-merge-expand");
}
{
  // merge no-op: прямоугольник = один существующий блок
  const t = mkSynth();
  const before = JSON.stringify(t.cells);
  const res = tableOps.mergeRect(t, 1, 0, 2, 1);
  check("merge no-op: changed=false", res.changed === false);
  check("merge no-op: cells нетронуты", JSON.stringify(t.cells) === before);
}
{
  // split: блок, накрытый continuation-слотом, → 1×1; текст остаётся на якоре
  const t = mkSynth();
  const res = tableOps.splitBlocks(t, [{ row: 1, col: 1 }]);
  const quad = [cellAt(t, 1, 0), cellAt(t, 1, 1), cellAt(t, 2, 0), cellAt(t, 2, 1)];
  check("split: changed", res.changed === true);
  check("split: все слоты 1×1 не merged", quad.every((c) => !c.is_merged && (c.rowspan || 1) === 1 && (c.colspan || 1) === 1));
  check("split: текст на якоре", cellAt(t, 1, 0).text === "Подача" && cellAt(t, 1, 1).text === "");
  check("split: соседний rowspan-блок цел", cellAt(t, 3, 0).rowspan === 2);
  coreInvariants(t, "ops-split");
}
{
  // transpose: размеры, spans, orientation
  const t = mkSynth();
  const res = tableOps.transposeTable(t);
  check("transpose: размеры", t.n_rows === 4 && t.n_cols === 5, `${t.n_rows}x${t.n_cols}`);
  check("transpose: orientation флип", t.orientation === "columns");
  const blocks = tableOps.toBlocks(t);
  const b2 = blocks.find((b) => b.text === "Подача");
  const b3 = blocks.find((b) => b.text === "Напор");
  check("transpose: 2×2 блок переехал (1,0)→(0,1)", b2 && b2.row === 0 && b2.col === 1 && b2.rowspan === 2 && b2.colspan === 2);
  check("transpose: rowspan→colspan", b3 && b3.row === 0 && b3.col === 3 && b3.rowspan === 1 && b3.colspan === 2);
  check("transpose: remap якорей", res.remap.some(([f, to]) => f === "1,2" && to === "2,1"));
  coreInvariants(t, "ops-transpose");
}
{
  // deleteRow: строка шапки — header_rows и сдвиг блоков
  const t = mkSynth();
  const res = tableOps.deleteRows(t, [0]);
  const b = tableOps.toBlocks(t).find((x) => x.text === "Подача");
  check("delRow: n_rows", t.n_rows === 4);
  check("delRow: header_rows--", t.header_rows === 0);
  check("delRow: remap сдвига", res.remap.some(([f, to]) => f === "1,2" && to === "0,2"), JSON.stringify(res.remap));
  check("delRow: блоки поднялись", b && b.row === 0 && b.col === 0);
  coreInvariants(t, "ops-delrow-hdr");
}
{
  // deleteRow внутрь блока: span сжимается, якорь (и аннотация) на месте
  const t = mkSynth();
  const res = tableOps.deleteRows(t, [2]);
  const b = tableOps.toBlocks(t).find((x) => x.text === "Подача");
  check("delRow в блок: rowspan сжался", b && b.row === 1 && b.rowspan === 1 && b.colspan === 2, b && `${b.rowspan}x${b.colspan}`);
  check("delRow в блок: ключ якоря не меняется", !res.remap.some(([f]) => f === "1,0"));
  coreInvariants(t, "ops-delrow-span");
}
{
  // deleteRows мульти: блок целиком в удалённых строках исчезает с аннотацией
  const t = mkSynth();
  const res = tableOps.deleteRows(t, [3, 4]);
  check("delRow мульти: n_rows", t.n_rows === 3);
  check("delRow мульти: блок «Напор» удалён", !tableOps.toBlocks(t).some((x) => x.text === "Напор"));
  check("delRow мульти: remap → null", res.remap.some(([f, to]) => f === "3,0" && to === null), JSON.stringify(res.remap));
  coreInvariants(t, "ops-delrow-multi");
}
{
  // deleteCol: сжатие colspan и drop блоков одной колонки
  const t = mkSynth();
  tableOps.deleteCols(t, [0]);
  const b = tableOps.toBlocks(t).find((x) => x.text === "Подача");
  check("delCol: n_cols", t.n_cols === 3);
  check("delCol: colspan сжался", b && b.col === 0 && b.colspan === 1 && b.rowspan === 2);
  check("delCol: блок «Напор» удалён", !tableOps.toBlocks(t).some((x) => x.text === "Напор"));
  coreInvariants(t, "ops-delcol");
}

/* интеграция в VisualMapper: клон, аннотации, undo/redo структурных */
{
  const src = mkSynth();
  const events = [];
  const mapper = new VisualMapper({ id: "c" }, src, {
    onTableChange: () => events.push("tbl"),
    onHistoryChange: () => events.push("hist"),
  });
  mapper.render();

  // документ не мутируется
  check("mapper ops: документ не мутируется", src.cells.length === 20 && src.n_rows === 5 && cellAt(src, 1, 0).text === "Подача");

  // без выделения — false
  check("mapper ops: merge без выделения false", mapper.mergeSelection() === false);
  check("mapper ops: split без выделения false", mapper.splitSelection() === false);
  check("mapper ops: delRows без выделения false", mapper.deleteSelectionRows() === false);
  check("mapper ops: delCols без выделения false", mapper.deleteSelectionCols() === false);

  // merge: выживает аннотация блока верхнего-левого слота, конфликтная дропается
  mapper.assignRole([{ row: 1, col: 2 }], "value"); // «70» — выживет
  mapper.assignRole([{ row: 1, col: 3 }], "param"); // «90» — конфликтная
  const snapshot = JSON.stringify(mapper.table.cells);
  mapper.selectRange({ row: 1, col: 2 }, { row: 1, col: 3 });
  check("mapper ops: merge выполнен", mapper.mergeSelection() === true);
  const anns = mapper.getAnnotations();
  check("mapper ops: merge — выжившая аннотация на новом якоре",
    anns.length === 1 && anns[0].row === 1 && anns[0].col === 2 && anns[0].role === "value",
    JSON.stringify(anns));
  check("mapper ops: выделение сброшено", mapper.getSelection().length === 0);
  check("mapper ops: onTableChange стрелял", events.includes("tbl"));
  check("mapper ops: onHistoryChange стрелял", events.includes("hist"));

  // undo/redo структурной операции: таблица и аннотации
  mapper.undo();
  check("mapper ops: undo — cells идентичны", JSON.stringify(mapper.table.cells) === snapshot);
  const anns2 = mapper.getAnnotations();
  check("mapper ops: undo — обе аннотации восстановлены",
    anns2.length === 2 && anns2.some((x) => x.row === 1 && x.col === 3 && x.role === "param"), JSON.stringify(anns2));
  mapper.redo();
  check("mapper ops: redo — merge вернулся", JSON.stringify(mapper.table.cells) !== snapshot);
  check("mapper ops: redo — аннотация снова одна", mapper.getAnnotations().length === 1);
  while (mapper.canUndo) mapper.undo();
  check("mapper ops: undo-all — исходная таблица", JSON.stringify(mapper.table.cells) === snapshot && mapper.getAnnotations().length === 0);
}

/* transpose + аннотации через маппер; чередование ролей и структур в истории */
{
  const mapper = new VisualMapper({ id: "c" }, mkSynth(), {});
  mapper.render();
  mapper.assignRole([{ row: 1, col: 2 }], "value", { unit: "м3/ч" });
  check("transpose: выполнен", mapper.transpose() === true);
  const a = mapper.getAnnotations();
  check("transpose: аннотация переехала (1,2)→(2,1)",
    a.length === 1 && a[0].row === 2 && a[0].col === 1 && a[0].unit === "м3/ч", JSON.stringify(a));
  mapper.undo();
  const a2 = mapper.getAnnotations();
  check("transpose: undo вернул координату аннотации", a2.length === 1 && a2[0].row === 1 && a2[0].col === 2);

  // чередование: роль → структура → роль; полный откат и повтор
  const m2 = new VisualMapper({ id: "c" }, mkSynth(), {});
  m2.render();
  m2.assignRole([{ row: 3, col: 1 }], "param");   // 1) роль
  m2.transpose();                                  // 2) структура (аннотация → (1,3))
  m2.assignRole([{ row: 1, col: 1 }], "value");   // 3) роль в новых координатах
  const annsAt3 = JSON.stringify(m2.getAnnotations());
  m2.undo(); // роль 3
  m2.undo(); // transpose
  const a3 = m2.getAnnotations();
  check("чередование: после 2×undo осталась исходная роль",
    a3.length === 1 && a3[0].row === 3 && a3[0].col === 1 && a3[0].role === "param", JSON.stringify(a3));
  check("чередование: undo вернул размеры", m2.table.n_rows === 5 && m2.table.n_cols === 4);
  m2.undo(); // роль 1
  check("чередование: 3×undo — пусто", m2.getAnnotations().length === 0 && !m2.canUndo);
  m2.redo(); m2.redo(); m2.redo();
  check("чередование: 3×redo вернули всё", JSON.stringify(m2.getAnnotations()) === annsAt3,
    JSON.stringify(m2.getAnnotations()) + " vs " + annsAt3);
}

/* удаление строк/колонок выделением + аннотации */
{
  const mapper = new VisualMapper({ id: "c" }, mkSynth(), {});
  mapper.render();
  mapper.assignRole([{ row: 1, col: 2 }], "value");
  mapper.selectRange({ row: 1, col: 0 }, { row: 2, col: 3 });
  check("delRows: выполнен", mapper.deleteSelectionRows() === true);
  check("delRows: n_rows 5→3", mapper.table.n_rows === 3);
  check("delRows: аннотации удалённых строк дропнуты", mapper.getAnnotations().length === 0);
  coreInvariants(mapper.table, "ops-mapper-delrows");

  const m2 = new VisualMapper({ id: "c" }, mkSynth(), {});
  m2.render();
  m2.assignRole([{ row: 1, col: 3 }], "value");   // колонка 3 — останется (сдвиг в 2)
  m2.assignRole([{ row: 1, col: 0 }], "param");   // якорь 2×2 блока — блок сожмётся, якорь жив
  m2.selectRange({ row: 1, col: 0 }, { row: 4, col: 0 });
  check("delCols: выполнен", m2.deleteSelectionCols() === true);
  check("delCols: n_cols 4→3", m2.table.n_cols === 3);
  const a = m2.getAnnotations();
  check("delCols: якорь сжавшегося блока жив, одиночная колонка сдвинулась",
    a.length === 2 && a.some((x) => x.col === 0 && x.role === "param") && a.some((x) => x.col === 2 && x.role === "value"),
    JSON.stringify(a));
  coreInvariants(m2.table, "ops-mapper-delcols");
}

/* раунд-трип на каждой таблице файла: merge → split → transpose → delRow,
 * инварианты после каждой операции, undo-all возвращает исходную таблицу */
function opsRoundTrip(table, tag) {
  if (!table.cells || !table.cells.length || !table.n_rows || !table.n_cols) return;
  const mapper = new VisualMapper({ id: "c" }, table, {});
  mapper.render();
  const norm = (t) => JSON.stringify({
    n: [t.n_rows, t.n_cols], h: t.header_rows || 0,
    cells: t.cells.map((c) => [c.row, c.col, c.rowspan || 1, c.colspan || 1, c.text || "", !!c.is_merged, c.merged_with || null]),
  });
  const orig = norm(table);
  const nR = table.n_rows, nC = table.n_cols;
  let did = 0;
  if (nC > 1) {
    mapper.selectRange({ row: 0, col: 0 }, { row: 0, col: 1 });
    if (mapper.mergeSelection()) did++;
    coreInvariants(mapper.table, `${tag}:ops-merge`);
  }
  mapper.selectRange({ row: 0, col: 0 }, { row: nR - 1, col: nC - 1 });
  if (mapper.splitSelection()) did++;
  coreInvariants(mapper.table, `${tag}:ops-split`);
  if (mapper.transpose()) did++;
  coreInvariants(mapper.table, `${tag}:ops-transpose`);
  if (mapper.table.n_rows > 1) {
    mapper.selectRange({ row: 0, col: 0 }, { row: 0, col: 0 });
    if (mapper.deleteSelectionRows()) did++;
    coreInvariants(mapper.table, `${tag}:ops-delrow`);
  }
  if (nR > 1 && nC > 1) check(`${tag}:ops: операции выполнились`, did >= 3, `${did}`);
  while (mapper.canUndo) mapper.undo();
  check(`${tag}:ops: undo-all вернул исходную таблицу`, norm(mapper.table) === orig);
  mapper.destroy();
}

/* --- пустая таблица --- */
{
  const r = new TableRenderer(fakeCanvas(), { id: "x", n_rows: 0, n_cols: 0, cells: [] });
  r.render();
  check("пустая таблица: totalW/H = оси", r.totalW === COL_HDR_W && r.totalH === ROW_HDR_H);
}

/* --- кривая таблица: ячейка вне сетки --- */
{
  const bad = {
    id: "bad", n_rows: 2, n_cols: 2,
    cells: [
      { row: 0, col: 0, text: "a" }, { row: 1, col: 1, text: "b" },
      { row: 5, col: 7, text: "вне сетки", rowspan: 1, colspan: 1 },
    ],
  };
  const r = new TableRenderer(fakeCanvas(), bad);
  check("кривая: раскладка без NaN", r.offX.every(Number.isFinite) && r.rowH.every(Number.isFinite));
  const objs = { list: [], add(...o) { this.list.push(...o); }, remove() {}, clear() { this.list = []; },
    setDimensions() {}, setViewportTransform() {}, requestRenderAll() {} };
  const r2 = new TableRenderer(objs, bad);
  r2.render();
  const rectObjs = objs.list.filter(o => o.data && o.data.row !== undefined);
  check("кривая: вне сетки не рисуется", rectObjs.length === 2, `${rectObjs.length}`);
}

/* --- highlightCell по продолжению → якорь --- */
{
  const { r } = coreInvariants(table, "hl");
  const cont = table.cells.find(c => c.is_merged && c.merged_with);
  if (cont) {
    const h = r.highlightCell(cont.row, cont.col, "#ff0000");
    check("highlightCell: по продолжению — якорь", h && h.left === r.offX[cont.merged_with[1]]);
    r.clearOverlays();
    check("clearOverlays", r.overlayCount() === 0);
  }
}

/* ===================== Предпросмотр извлечения (3.6) ===================== */

/** Таблица из перечня ячеек [row, col, text, rowspan=1, colspan=1] (без merged). */
const mkTable = (n_rows, n_cols, cells) => ({
  id: "pv", n_rows, n_cols,
  cells: cells.map(([row, col, text, rowspan = 1, colspan = 1]) =>
    ({ row, col, text, rowspan, colspan, is_merged: false, merged_with: null })),
});
/** Аннотации: [row, col, role, extra?] → объекты. */
const ann = (...items) => items.map(([row, col, role, extra]) => ({ row, col, role, ...extra }));

/* --- key-value: параметр и значение в строке, unit справа --- */
{
  const t = mkTable(3, 3, [
    [1, 0, "Подача"], [1, 1, "70"], [1, 2, "м3/ч"],
    [2, 0, "Напор"], [2, 1, "45"],
  ]);
  const res = buildPreview(t, ann(
    [1, 0, "param"], [1, 1, "value"], [1, 2, "unit"],
    [2, 0, "param"], [2, 1, "value"],
  ));
  check("pv kv: карточка без модели", res.cards.length === 1 && res.cards[0].model === null);
  const ps = res.cards[0].parameters;
  check("pv kv: 2 параметра", ps.length === 2, `${ps.length}`);
  check("pv kv: имя из ячейки", ps[0].name === "Подача" && ps[0].value === "70");
  check("pv kv: unit из ячейки строки", ps[0].unit === "м3/ч");
  check("pv kv: без unit", ps[1].unit === undefined);
  check("pv kv: stats", res.stats.models === 0 && res.stats.params === 2 && res.stats.values === 2,
    JSON.stringify(res.stats));
  check("pv kv: без предупреждений", res.warnings.length === 0, JSON.stringify(res.warnings));
}

/* --- matrix-rows (мини-ЦНС): модели в col 0, merged-значения rowspan → нескольким моделям --- */
{
  const t = mkTable(5, 3, [
    [0, 1, "Подача"], [0, 2, "Напор"],
    [1, 0, "М1"], [1, 1, "10", 3], [1, 2, "100", 3],
    [2, 0, "М2"],
    [3, 0, "М3"],
    [4, 0, "М4"], [4, 1, "11"], [4, 2, "101"],
  ]);
  const res = buildPreview(t, ann(
    [0, 1, "param"], [0, 2, "param"],
    [1, 0, "model"], [2, 0, "model"], [3, 0, "model"], [4, 0, "model"],
    [1, 1, "value"], [1, 2, "value"], [4, 1, "value"], [4, 2, "value"],
  ));
  check("pv rows: 4 карточки", res.cards.length === 4 && res.stats.models === 4, `${res.cards.length}`);
  check("pv rows: merged-значение → 3 моделям",
    res.cards.slice(0, 3).every(c => c.parameters.length === 2 && c.parameters[0].value === "10"),
    JSON.stringify(res.cards.map(c => c.parameters.length)));
  check("pv rows: М4 свои значения", res.cards[3].parameters[0].value === "11");
  check("pv rows: имена из шапки", res.cards[0].parameters.map(p => p.name).join() === "Подача,Напор");
  check("pv rows: stats", res.stats.models === 4 && res.stats.values === 4, JSON.stringify(res.stats));
}

/* --- matrix-columns: модели в шапке, rowspan-параметры в col 0 --- */
{
  const t = mkTable(4, 3, [
    [0, 1, "2522"], [0, 2, "2534"],
    [1, 0, "Подача", 2], [3, 0, "Напор"],
    [1, 1, "10"], [1, 2, "20"],
    [2, 1, "11"], [2, 2, "21"],
    [3, 1, "30"], [3, 2, "40"],
  ]);
  const res = buildPreview(t, ann(
    [0, 1, "model"], [0, 2, "model"],
    [1, 0, "param"], [3, 0, "param"],
    [1, 1, "value"], [1, 2, "value"], [2, 1, "value"], [2, 2, "value"], [3, 1, "value"], [3, 2, "value"],
  ));
  check("pv cols: 2 карточки по моделям шапки",
    res.cards.length === 2 && res.cards[0].model === "2522" && res.cards[1].model === "2534");
  check("pv cols: rowspan-параметр покрыл 2 строки",
    res.cards[0].parameters.length === 3, JSON.stringify(res.cards[0].parameters));
  check("pv cols: имена по строкам", res.cards[0].parameters.map(p => p.name).join() === "Подача,Подача,Напор");
}

/* --- реальная ЦНС p6-t1: шапка = 7 параметров, col 0 = 18 моделей, якоря тела = значения --- */
{
  const t = table; // первая таблица файла (vm_test_table.json = p6-t1)
  if (t.n_rows === 19 && t.n_cols === 8) {
    const anns = [];
    for (const c of t.cells) {
      if (c.is_merged || c.row >= t.n_rows || c.col >= t.n_cols) continue;
      if (c.row === 0 && c.col >= 1) anns.push({ row: c.row, col: c.col, role: "param" });
      else if (c.row >= 1 && c.col === 0) anns.push({ row: c.row, col: c.col, role: "model" });
      else if (c.row >= 1 && c.col >= 1) anns.push({ row: c.row, col: c.col, role: "value" });
    }
    const res = buildPreview(t, anns);
    check("pv ЦНС: 18 карточек-моделей", res.cards.length === 18 && res.stats.models === 18, `${res.cards.length}`);
    check("pv ЦНС: у каждой модели 7 параметров", res.cards.every(c => c.parameters.length === 7),
      res.cards.map(c => c.parameters.length).join(","));
    check("pv ЦНС: 126 записей из 45 value-ячеек",
      res.stats.values === 45 && res.cards.reduce((n, c) => n + c.parameters.length, 0) === 126,
      `${res.stats.values}/${res.cards.reduce((n, c) => n + c.parameters.length, 0)}`);
    check("pv ЦНС: без предупреждений", res.warnings.length === 0, JSON.stringify(res.warnings));
    const byName = Object.fromEntries(res.cards[0].parameters.map(p => [p.name, p.value]));
    check("pv ЦНС: 1-я модель — подача/ступени/масса",
      byName["Подача, м3/с (м3/ч)"] === "0,0036 (13)" && byName["Число ступеней, шт"] === "2" &&
      byName["Масса, кг, не более"] === "179", JSON.stringify(byName));
    const byName3 = Object.fromEntries(res.cards[2].parameters.map(p => [p.name, p.value]));
    check("pv ЦНС: rowspan-значения дошли до 3-й модели",
      byName3["Число ступеней, шт"] === "2" && byName3["Напор, м"] === "70", JSON.stringify(byName3));
    check("pv ЦНС: 3-я модель — своя мощность", byName3["Мощность электродвигате- ля, кВт (Рн)"] === "15");
  }
}

/* --- warnings --- */
{
  const t = mkTable(2, 2, [
    [0, 0, "Подача"], [0, 1, ""],
    [1, 0, "Напор"], [1, 1, "45"],
  ]);
  const res = buildPreview(t, ann([0, 0, "param"], [1, 0, "param"], [0, 1, "value"], [1, 1, "value"]));
  check("pv warn: пустое значение", res.warnings.some(w => w.includes("Пустое значение")), JSON.stringify(res.warnings));

  const t2 = mkTable(2, 2, [[0, 0, "A"], [1, 1, "x"]]);
  const res2 = buildPreview(t2, ann([0, 0, "param"], [1, 1, "value"]));
  check("pv warn: значение без параметра", res2.warnings.some(w => w.includes("без параметра")), JSON.stringify(res2.warnings));
  check("pv warn: параметр без значений", res2.warnings.some(w => w.includes("без значений")));

  const t3 = mkTable(2, 2, [[0, 0, "A", 2], [0, 1, "x"], [1, 1, "y"]]);
  const res3 = buildPreview(t3, ann([0, 0, "param"], [0, 1, "value"], [1, 1, "value"]));
  check("pv warn: несколько значений у параметра", res3.warnings.some(w => w.includes("Несколько значений")),
    JSON.stringify(res3.warnings));
}

/* --- переопределения из аннотаций + section/condition/ignore --- */
{
  const t = mkTable(4, 3, [
    [0, 0, "Раздел 1", 4],
    [1, 1, "Подача"], [1, 2, "70"],
    [2, 1, "Напор"], [2, 2, "45"],
    [3, 1, "Примечание: не для извлечения", 1, 2],
  ]);
  const res = buildPreview(t, ann(
    [0, 0, "section"],
    [1, 1, "param", { label: "Подача Q", unit: "м3/ч", group: "Гидравлика" }],
    [1, 2, "value"],
    [2, 1, "param"], [2, 2, "value"],
    [3, 1, "condition"],
  ));
  const ps = res.cards[0].parameters;
  check("pv override: label/unit/group из аннотации",
    ps[0].name === "Подача Q" && ps[0].unit === "м3/ч" && ps[0].group === "Гидравлика");
  check("pv section: группа из section-ячейки", ps[1].group === "Раздел 1", ps[1].group);
  check("pv condition: примечание в карточке",
    res.cards[0].conditions.length === 1 && res.cards[0].conditions[0].includes("не для извлечения"));

  const t4 = mkTable(2, 2, [[0, 0, "A"], [0, 1, "x"]]);
  const r4 = buildPreview(t4, ann([0, 0, "param"], [0, 1, "ignore"]));
  check("pv ignore: не извлечено", r4.stats.values === 0 && r4.cards.length === 0);
}

/* --- toCsv --- */
{
  const res = {
    cards: [
      { model: 'М1; "x"', parameters: [{ name: "A", value: "1;2", unit: "м3/ч", group: null, row: 0, col: 1 }], conditions: ["прим"] },
      { model: null, parameters: [], conditions: [] },
    ],
  };
  const csv = toCsv(res);
  const lines = csv.trim().split("\r\n");
  check("csv: заголовок", lines[0] === "model;param;value;unit;group;condition", lines[0]);
  check("csv: экранирование ; и \"", lines[1] === '"М1; ""x""";A;"1;2";м3/ч;;', lines[1]);
  check("csv: примечание строкой", lines[2].endsWith("прим"), lines[2]);
  check("csv: пустая карточка без строк", lines.length === 3, `${lines.length}`);
}

/* --- инвариант транспонирования: preview(t, anns) ≡ preview(transpose(t), remap(anns)) --- */
{
  const t = mkTable(5, 3, [
    [0, 1, "Подача"], [0, 2, "Напор"],
    [1, 0, "М1"], [1, 1, "10", 3], [1, 2, "100", 3],
    [2, 0, "М2"],
    [3, 0, "М3"],
    [4, 0, "М4"], [4, 1, "11"], [4, 2, "101"],
  ]);
  const anns = ann(
    [0, 1, "param"], [0, 2, "param"],
    [1, 0, "model"], [2, 0, "model"], [3, 0, "model"], [4, 0, "model"],
    [1, 1, "value"], [1, 2, "value"], [4, 1, "value"], [4, 2, "value"],
  );
  const before = buildPreview(t, anns);
  const t2 = JSON.parse(JSON.stringify(t));
  tableOps.transposeTable(t2);
  const anns2 = anns.map(a => ({ ...a, row: a.col, col: a.row }));
  const after = buildPreview(t2, anns2);
  const sig = (res) => res.cards.map(c =>
    `${c.model}|${c.parameters.map(p => `${p.name}=${p.value}`).join(",")}`).join(";");
  check("pv transpose: карточки те же", sig(before) === sig(after), `${sig(before)} ≠ ${sig(after)}`);
  check("pv transpose: stats те же", JSON.stringify(before.stats) === JSON.stringify(after.stats),
    `${JSON.stringify(before.stats)} ≠ ${JSON.stringify(after.stats)}`);
}

/* ===================== Шаблоны маппинга (3.7) ===================== */
{
  const norm = (t) => JSON.stringify([
    t.n_rows, t.n_cols, t.header_rows,
    t.cells.map((c) => [c.row, c.col, c.rowspan, c.colspan, c.text, !!c.is_merged, c.merged_with || null]),
  ]);

  // getOps: спеки структурных операций в порядке выполнения
  const mapper = new VisualMapper({ id: "c" }, mkSynth(), {});
  mapper.render();
  check("tpl: пустая история — ops пусты", mapper.getOps().length === 0);
  mapper.selectRange({ row: 1, col: 2 }, { row: 2, col: 2 });
  mapper.mergeSelection();
  mapper.selectRange({ row: 1, col: 2 }, { row: 2, col: 2 });
  mapper.splitSelection();
  mapper.transpose();
  mapper.selectRange({ row: 0, col: 0 }, { row: 0, col: 0 });
  mapper.deleteSelectionRows();
  const ops = mapper.getOps();
  check("tpl: 4 спеки", ops.length === 4, `${ops.length}`);
  check("tpl: merge rect", ops[0].op === "merge" && ops[0].rect.join() === "1,2,2,2", JSON.stringify(ops[0]));
  check("tpl: split slots", ops[1].op === "split" && ops[1].slots.length === 2, JSON.stringify(ops[1]));
  check("tpl: transpose", ops[2].op === "transpose");
  check("tpl: del_rows", ops[3].op === "del_rows" && ops[3].rows.join() === "0", JSON.stringify(ops[3]));

  // undo/redo согласован: undo 2 структурных → 2 спеки, redo → 4
  mapper.undo(); mapper.undo();
  check("tpl: undo убирает спеки", mapper.getOps().length === 2, `${mapper.getOps().length}`);
  mapper.redo(); mapper.redo();
  check("tpl: redo возвращает спеки", mapper.getOps().length === 4, `${mapper.getOps().length}`);

  // applyOps на свежем клоне ≡ правленая таблица маппера
  const fresh = mkSynth();
  tableOps.applyOps(fresh, mapper.getOps());
  check("tpl: applyOps воспроизводит правки", norm(fresh) === norm(mapper.table),
    `\n${norm(fresh)}\n≠\n${norm(mapper.table)}`);
  mapper.destroy();

  // setAnnotations: параметры, пропуск несуществующих, нормализация к якорю, undo
  const m2 = new VisualMapper({ id: "c" }, mkSynth(), {});
  m2.render();
  m2.setAnnotations([
    { row: 0, col: 1, role: "model" },
    { row: 1, col: 0, role: "param", label: "Подача Q", unit: "м3/ч" },
    { row: 1, col: 2, role: "value" },
    { row: 99, col: 99, role: "value" }, // слота нет — пропустить
    { row: 0, col: 0, role: "nope" },    // неизвестная роль — пропустить
  ]);
  const a = m2.getAnnotations();
  check("tpl: setAnnotations применил 3", a.length === 3, JSON.stringify(a));
  check("tpl: setAnnotations параметры",
    a.some((x) => x.row === 1 && x.col === 0 && x.role === "param" && x.label === "Подача Q" && x.unit === "м3/ч"),
    JSON.stringify(a));
  m2.setAnnotations([{ row: 2, col: 1, role: "value" }]); // слот продолжения → якорь
  const a2 = m2.getAnnotations();
  check("tpl: setAnnotations нормализует к якорю",
    a2.length === 1 && a2[0].row === 1 && a2[0].col === 0, JSON.stringify(a2));
  m2.undo();
  check("tpl: undo setAnnotations возвращает прежние", m2.getAnnotations().length === 3);
  m2.undo();
  check("tpl: undo до пустых", m2.getAnnotations().length === 0);
  m2.destroy();

  // пустые/кривые спеки → no-op
  const t3 = mkSynth();
  tableOps.applyOps(t3, []);
  tableOps.applyOps(t3, [{ op: "merge", rect: [0, 0, 0, 0] }]); // один блок → no-op
  tableOps.applyOps(t3, [{ op: "nope" }, null, { op: "merge" }]); // неизвестные/битые — мимо
  check("tpl: applyOps no-op", norm(t3) === norm(mkSynth()));
}

/* --- LLM-ассистент: подсветка спорных клеток (4.5, слой "llm") --- */
{
  const m = new VisualMapper({ id: "c" }, mkSynth(), {});
  m.render();

  // роли живут в своём слое — подсветка LLM их не затирает
  m.assignRole([{ row: 0, col: 1 }, { row: 1, col: 2 }], "value");
  const annCount = m.renderer.overlayCount("annotations");
  check("llm: роли подсвечены до начала", annCount === 2, `${annCount}`);

  // точечные клетки + продолжение merged-блока + дубль якоря
  const n = m.highlightSuggestions([
    { row: 0, col: 2 },               // обычная клетка
    { row: 1, col: 1 },               // продолжение блока «Подача» (якорь 1,0)
    { row: 1, col: 0 },               // дубль якоря того же блока
    { row: 2, col: 0 },               // ещё одно продолжение (тот же якорь)
    { row: 99, col: 99 },             // вне сетки — пропустить
  ]);
  check("llm: подсвечено 2 (клетка + якорь merged)", n === 2, `${n}`);
  check("llm: слой llm = 2 оверлея", m.renderer.overlayCount("llm") === 2,
    `${m.renderer.overlayCount("llm")}`);
  check("llm: слой annotations не тронут", m.renderer.overlayCount("annotations") === annCount);

  // повторный вызов заменяет подсветку, не накапливает
  m.highlightSuggestions([{ row: 3, col: 1 }]);
  check("llm: повтор — замена, не накопление", m.renderer.overlayCount("llm") === 1,
    `${m.renderer.overlayCount("llm")}`);
  check("llm: роли по-прежнему на месте", m.renderer.overlayCount("annotations") === annCount);

  // clearSuggestions чистит только слой llm
  m.clearSuggestions();
  check("llm: clearSuggestions чистит llm", m.renderer.overlayCount("llm") === 0);
  check("llm: clearSuggestions не трогает роли", m.renderer.overlayCount("annotations") === annCount);

  // null/пустой список — не падает, чистит слой
  m.highlightSuggestions([{ row: 0, col: 0 }]);
  check("llm: подсветка есть", m.renderer.overlayCount("llm") === 1);
  check("llm: null — 0 и без ошибок", m.highlightSuggestions(null) === 0);
  check("llm: слой чист после null", m.renderer.overlayCount("llm") === 0);

  // полная последовательность 4.5: setAnnotations (LLM) + highlightSuggestions,
  // затем undo откатывает роли, а clearSuggestions убирает подсветку
  const m2 = new VisualMapper({ id: "c" }, mkSynth(), {});
  m2.render();
  m2.assignRole([{ row: 0, col: 0 }], "param"); // своя роль до предложения
  m2.setAnnotations([
    { row: 0, col: 1, role: "model" },
    { row: 1, col: 2, role: "value" },
  ]);
  check("llm: setAnnotations заменил роли", m2.getAnnotations().length === 2,
    `${m2.getAnnotations().length}`);
  m2.highlightSuggestions([{ row: 1, col: 2 }]);
  check("llm: спорная клетка подсвечена", m2.renderer.overlayCount("llm") === 1);
  m2.undo();
  check("llm: undo вернул свою роль", m2.getAnnotations().length === 1 &&
    m2.getAnnotations()[0].role === "param", JSON.stringify(m2.getAnnotations()));
  m2.clearSuggestions();
  check("llm: подсветка снята", m2.renderer.overlayCount("llm") === 0);

  // сценарий 4.6: быстрое исправление спорной клетки альтернативной ролью
  // (assignRole поверх setAnnotations) + undo/redo вокруг него
  const m3 = new VisualMapper({ id: "c" }, mkSynth(), {});
  m3.render();
  m3.setAnnotations([                       // «предложение LLM»
    { row: 0, col: 1, role: "model" },
    { row: 1, col: 2, role: "value" },      // спорная (confidence < 0.7)
  ]);
  m3.highlightSuggestions([{ row: 1, col: 2 }]);
  check("llm: спорная подсвечена", m3.renderer.overlayCount("llm") === 1);
  m3.assignRole([{ row: 1, col: 2 }], "param", { label: "Подача" }); // чип альтернативы
  const ann3 = m3.getAnnotations();
  const fixed3 = ann3.find((a) => a.row === 1 && a.col === 2);
  check("llm: альтернатива применилась", fixed3 && fixed3.role === "param" && fixed3.label === "Подача",
    JSON.stringify(ann3));
  check("llm: другая аннотация LLM не тронута", ann3.find((a) => a.row === 0 && a.col === 1)?.role === "model");
  m3.clearSuggestions();
  m3.undo(); // откат исправления — снова роль LLM (пункт «не проверено»)
  check("llm: undo вернул роль LLM",
    m3.getAnnotations().find((a) => a.row === 1 && a.col === 2)?.role === "value",
    JSON.stringify(m3.getAnnotations()));
  m3.redo();
  check("llm: redo вернул исправление",
    m3.getAnnotations().find((a) => a.row === 1 && a.col === 2)?.role === "param",
    JSON.stringify(m3.getAnnotations()));
}

if (failures) { console.error(`\n${failures} FAILURES`); process.exit(1); }
console.log("\nALL PASSED");
