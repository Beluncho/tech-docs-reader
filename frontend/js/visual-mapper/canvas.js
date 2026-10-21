/* VisualMapper — ядро визуального маппера (этап 3).
 *
 * 3.3: выделение ячеек в стиле Excel — клик (одна ячейка), drag (диапазон),
 * Ctrl+Click (добавить/убрать ячейку), Shift+Click (расширить диапазон от
 * последнего якоря). Подсветка выделения — отдельный слой рендерера
 * ("selection"), не конфликтующий с подсветкой ролей ("annotations").
 * 3.4: роли с параметрами (label/unit/group), назначение выделению,
 * снятие роли (roleId = null), undo/redo (история диффами аннотаций).
 * 3.5: операции с ячейками (merge/split/transpose/удаление строк-колонок) —
 * таблица клонируется (документ не мутируется), структурные операции идут
 * через снапшоты в общей истории, аннотации переносятся по remap операций.
 * Предпросмотр — 3.6. Шаблоны маппинга — 3.7: ops-спеки в структурных
 * записях истории (getOps), setAnnotations для применения шаблона.
 * 4.5: подсветка спорных клеток предложения LLM — отдельный слой "llm"
 * (highlightSuggestions/clearSuggestions), не конфликтует с ролями и выделением.
 */
"use strict";

import { Canvas } from "../../vendor/fabric-6.7.1.min.mjs";
import { TableRenderer } from "./table-renderer.js";
import { ROLES_BY_ID, CELL_ROLES } from "./roles.js";
import { mergeRect, splitBlocks, transposeTable, deleteRows, deleteCols } from "./table-ops.js";

const SELECTION_COLOR = "#e3f2fd";

export class VisualMapper {
  /**
   * @param {HTMLElement} canvasEl — <canvas> элемент
   * @param {object} table — TableOut
   * @param {object} [opts] — колбэки: {onCellClick, onSelectionChange, onAnnotationsChange, onZoom, onHistoryChange, onTableChange}
   */
  constructor(canvasEl, table, opts = {}) {
    this.canvasEl = canvasEl;
    this.canvas = new Canvas(canvasEl, {
      selection: false,       // рамка group-выделения fabric не нужна
      preserveObjectStacking: true,
    });
    // структурные операции (3.5) мутируют таблицу — работаем на клоне,
    // модель документа остаётся нетронутой
    this.table = VisualMapper.cloneTable(table);
    this.opts = opts;
    this.renderer = new TableRenderer(this.canvas, this.table, { onZoom: opts.onZoom });
    this.annotations = new Map(); // "row,col" (якорь) → {role, label, unit, group}
    this.selectedCells = [];      // слоты {row, col} — выделение
    this._lastAnchor = null;      // якорь последнего клика (для Shift-расширения)
    this._dragStart = null;       // якорь начала drag-диапазона
    this._history = [];           // записи: диффы [{key, before, after}] | {structural, before, after}
    this._redoStack = [];         // отменённые действия для повтора
    this._setupInteractions();
  }

  render() {
    this.renderer.render();
  }

  destroy() {
    this.canvas.dispose();
  }

  /* --- интеракции --- */

  _setupInteractions() {
    // fabric ищет объект по точке клика с учётом evented=false у текста —
    // клик всегда попадает в Rect ячейки
    this.canvas.on("mouse:down", (opt) => {
      const target = opt.target;
      if (!target || !target.data) {
        // клик по фону/осям — снять выделение
        this.clearSelection();
        return;
      }
      const anchor = this.renderer.anchorAt(target.data.row, target.data.col);
      if (!anchor) return;
      if (opt.e.ctrlKey || opt.e.metaKey) {
        this.toggleCell(anchor.row, anchor.col);
      } else if (opt.e.shiftKey && this._lastAnchor) {
        this.selectRange(this._lastAnchor, anchor);
      } else {
        this._dragStart = anchor;
        this._clickCell({ row: anchor.row, col: anchor.col }); // мгновенная подсветка
      }
    });

    this.canvas.on("mouse:move", (opt) => {
      if (!this._dragStart) return;
      const target = opt.target;
      if (!target || !target.data) return;
      const anchor = this.renderer.anchorAt(target.data.row, target.data.col);
      if (!anchor) return;
      if (anchor.row === this._dragStart.row && anchor.col === this._dragStart.col) return;
      this._selectRange(this._dragStart, anchor); // превью диапазона при drag
    });

    this.canvas.on("mouse:up", (opt) => {
      if (!this._dragStart) return;
      const target = opt.target;
      const anchor = target && target.data
        ? this.renderer.anchorAt(target.data.row, target.data.col)
        : null;
      this._selectRange(this._dragStart, anchor || this._dragStart);
      this._dragStart = null;
    });

    // Ctrl+колесо — зум (обычное колесо — скролл контейнера, браузер сам)
    this.canvas.on("mouse:wheel", (opt) => {
      if (!opt.e.ctrlKey) return;
      opt.e.preventDefault();
      opt.e.stopPropagation();
      this.zoomBy(opt.e.deltaY > 0 ? 1 / 1.1 : 1.1);
    });
  }

  /** Одиночный выбор ячейки (клик; merged-продолжение → якорь). */
  _clickCell(data) {
    const anchor = this.renderer.anchorAt(data.row, data.col);
    if (!anchor) return;
    this._selectRange(anchor, anchor);
  }

  /* --- выделение --- */

  /** Прямоугольный диапазон между двумя ячейками (включая концы). */
  selectRange(a, b) {
    this._selectRange(a, b);
  }

  _selectRange(a, b) {
    const minRow = Math.min(a.row, b.row), maxRow = Math.max(a.row, b.row);
    const minCol = Math.min(a.col, b.col), maxCol = Math.max(a.col, b.col);
    this.selectedCells = [];
    for (let r = minRow; r <= maxRow; r++) {
      for (let c = minCol; c <= maxCol; c++) this.selectedCells.push({ row: r, col: c });
    }
    this._lastAnchor = a;
    this._renderSelection();
    if (this.opts.onSelectionChange) this.opts.onSelectionChange(this.selectedCells);
  }

  /** Ctrl+Click: добавить/убрать ячейку (якорь merged-блока) из выделения. */
  toggleCell(row, col) {
    const anchor = this.renderer.anchorAt(row, col);
    if (!anchor) return;
    const key = `${anchor.row},${anchor.col}`;
    const idx = this.selectedCells.findIndex((s) => `${s.row},${s.col}` === key);
    if (idx >= 0) this.selectedCells.splice(idx, 1);
    else this.selectedCells.push({ row: anchor.row, col: anchor.col });
    this._lastAnchor = anchor;
    this._renderSelection();
    if (this.opts.onSelectionChange) this.opts.onSelectionChange(this.selectedCells);
  }

  clearSelection() {
    if (!this.selectedCells.length) return;
    this.selectedCells = [];
    this._lastAnchor = null;
    this.renderer.clearOverlays("selection");
    if (this.opts.onSelectionChange) this.opts.onSelectionChange(this.selectedCells);
  }

  getSelection() {
    return this.selectedCells.slice();
  }

  /** Подсветка выделения: слоты группируются по якорям merged-блоков. */
  _renderSelection() {
    this.renderer.clearOverlays("selection");
    const seen = new Set();
    for (const { row, col } of this.selectedCells) {
      const anchor = this.renderer.anchorAt(row, col);
      if (!anchor) continue;
      const key = `${anchor.row},${anchor.col}`;
      if (seen.has(key)) continue;
      seen.add(key);
      this.renderer.highlightCell(row, col, SELECTION_COLOR, { layer: "selection" });
    }
    // инфо о ячейке — по последнему якорю
    if (this._lastAnchor && this.opts.onCellClick) this.opts.onCellClick(this._lastAnchor);
  }

  /* --- зум --- */

  getZoom() { return this.renderer.zoom; }
  setZoom(z) { this.renderer.setZoom(z); }
  zoomIn() { this.renderer.zoomBy(1.2); }
  zoomOut() { this.renderer.zoomBy(1 / 1.2); }
  zoomBy(factor) { this.renderer.zoomBy(factor); }
  resetZoom() { this.renderer.setZoom(1); }
  /** Вписать таблицу по ширине контейнера/окна (в px). */
  fitWidth(availablePx) { this.renderer.fitWidth(availablePx); }

  /* --- роли (3.4) --- */

  /**
   * Назначить/снять роль ячейкам. roleId = null — снять роль.
   * Ключ аннотации — якорь merged-блока (роль действует на весь блок).
   * @returns {number} число изменённых ячеек (0 = ничего не изменилось)
   */
  assignRole(cells, roleId, params = {}) {
    if (roleId && !ROLES_BY_ID[roleId]) throw new Error("Неизвестная роль: " + roleId);
    const clean = this._cleanParams(params);
    const entries = [];
    const seen = new Set();
    for (const { row, col } of cells) {
      const anchor = this.renderer.anchorAt(row, col);
      if (!anchor) continue;
      const key = `${anchor.row},${anchor.col}`;
      if (seen.has(key)) continue; // несколько слотов одного merged-блока
      seen.add(key);
      const before = this.annotations.get(key) || null;
      const after = roleId ? { role: roleId, ...clean } : null;
      if (JSON.stringify(before) === JSON.stringify(after)) continue; // no-op
      entries.push({ key, before, after });
    }
    if (!entries.length) return 0;
    this._applyEntries(entries.map((e) => ({ key: e.key, value: e.after })));
    this._history.push(entries);
    this._redoStack = [];
    this._notifyHistory();
    return entries.length;
  }

  /** Применить роль (или снять: roleId = null) к текущему выделению. */
  applyRoleToSelection(roleId, params = {}) {
    if (!this.selectedCells.length) return 0;
    return this.assignRole(this.selectedCells, roleId, params);
  }

  /** Убрать все аннотации (одно действие в истории). */
  clearAnnotations() {
    if (!this.annotations.size) return 0;
    const entries = [...this.annotations.entries()].map(([key, before]) => ({ key, before, after: null }));
    this._applyEntries(entries.map((e) => ({ key: e.key, value: e.after })));
    this._history.push(entries);
    this._redoStack = [];
    this._notifyHistory();
    return entries.length;
  }

  get canUndo() { return this._history.length > 0; }
  get canRedo() { return this._redoStack.length > 0; }

  undo() {
    const entry = this._history.pop();
    if (!entry) return false;
    if (entry.structural) this._restoreState(entry.before);
    else this._applyEntries(entry.map((e) => ({ key: e.key, value: e.before })));
    this._redoStack.push(entry);
    this._notifyHistory();
    return true;
  }

  redo() {
    const entry = this._redoStack.pop();
    if (!entry) return false;
    if (entry.structural) this._restoreState(entry.after);
    else this._applyEntries(entry.map((e) => ({ key: e.key, value: e.after })));
    this._history.push(entry);
    this._notifyHistory();
    return true;
  }

  /* --- операции с ячейками (3.5; спеки — для шаблонов 3.7) --- */

  /** Объединить выделение в одну ячейку (прямоугольник, расширенный до целых merged-блоков). */
  mergeSelection() {
    const rect = this._selectionRect();
    if (!rect) return false;
    return this._applyOp(
      (t) => mergeRect(t, rect.r0, rect.c0, rect.r1, rect.c1),
      { op: "merge", rect: [rect.r0, rect.c0, rect.r1, rect.c1] },
    );
  }

  /** Разделить merged-блоки, затронутые выделением, на 1×1. */
  splitSelection() {
    if (!this.selectedCells.length) return false;
    const slots = this.selectedCells.map((s) => [s.row, s.col]);
    return this._applyOp((t) => splitBlocks(t, this.selectedCells), { op: "split", slots });
  }

  /** Транспонировать таблицу (строки ↔ колонки). */
  transpose() {
    return this._applyOp((t) => transposeTable(t), { op: "transpose" });
  }

  /** Удалить все строки, затронутые выделением. */
  deleteSelectionRows() {
    const rows = this._selectionAxis("row");
    if (!rows.length) return false;
    return this._applyOp((t) => deleteRows(t, rows), { op: "del_rows", rows });
  }

  /** Удалить все колонки, затронутые выделением. */
  deleteSelectionCols() {
    const cols = this._selectionAxis("col");
    if (!cols.length) return false;
    return this._applyOp((t) => deleteCols(t, cols), { op: "del_cols", cols });
  }

  /** Охватывающий прямоугольник выделения или null. */
  _selectionRect() {
    if (!this.selectedCells.length) return null;
    const rows = this.selectedCells.map((s) => s.row);
    const cols = this.selectedCells.map((s) => s.col);
    return {
      r0: Math.min(...rows), r1: Math.max(...rows),
      c0: Math.min(...cols), c1: Math.max(...cols),
    };
  }

  /** Уникальные индексы строк/колонок выделения. */
  _selectionAxis(axis) {
    return [...new Set(this.selectedCells.map((s) => s[axis]))].sort((a, b) => a - b);
  }

  /**
   * Выполнить структурную операцию: снапшоты (таблица + аннотации) → операция
   * → перенос аннотаций (remap) → ре-рендер → запись в историю. Выделение
   * сбрасывается — его координаты после правки недостоверны.
   * spec — описание операции для getOps() (шаблоны маппинга, 3.7).
   */
  _applyOp(fn, spec = null) {
    const before = { table: this._snapshotTable(), annotations: this._snapshotAnnotations() };
    const res = fn(this.table);
    if (!res || !res.changed) return false;
    this._applyRemap(res.remap || []);
    const after = { table: this._snapshotTable(), annotations: this._snapshotAnnotations() };
    this.renderer.refresh();
    this.clearSelection();
    this._renderAnnotations();
    this._history.push({ structural: true, before, after, spec });
    this._redoStack = [];
    this._notifyHistory();
    if (this.opts.onAnnotationsChange) this.opts.onAnnotationsChange(this.getAnnotations());
    if (this.opts.onTableChange) this.opts.onTableChange(this.table);
    return true;
  }

  /** Перенести/удалить аннотации по remap операции ([[fromKey, toKey|null]]). */
  _applyRemap(remap) {
    for (const [from, to] of remap) {
      const ann = this.annotations.get(from);
      if (!ann) continue;
      this.annotations.delete(from);
      if (to) this.annotations.set(to, ann);
    }
  }

  _snapshotTable() {
    return {
      cells: this.table.cells.map((c) => ({ ...c })),
      n_rows: this.table.n_rows,
      n_cols: this.table.n_cols,
      header_rows: this.table.header_rows || 0,
      has_merged_cells: !!this.table.has_merged_cells,
      is_matrix: !!this.table.is_matrix,
      orientation: this.table.orientation || null,
    };
  }

  _snapshotAnnotations() {
    return [...this.annotations.entries()].map(([k, v]) => [k, { ...v }]);
  }

  /** Восстановить состояние структурной операции: таблицу и аннотации. */
  _restoreState(state) {
    this._restoreTable(state.table);
    this.annotations = new Map(state.annotations.map(([k, v]) => [k, { ...v }]));
    this._renderAnnotations();
    if (this.opts.onAnnotationsChange) this.opts.onAnnotationsChange(this.getAnnotations());
  }

  _restoreTable(snap) {
    this.table.cells = snap.cells.map((c) => ({ ...c }));
    this.table.n_rows = snap.n_rows;
    this.table.n_cols = snap.n_cols;
    this.table.header_rows = snap.header_rows;
    this.table.has_merged_cells = snap.has_merged_cells;
    this.table.is_matrix = snap.is_matrix;
    this.table.orientation = snap.orientation;
    this.renderer.refresh();
    this.clearSelection();
    if (this.opts.onTableChange) this.opts.onTableChange(this.table);
  }

  /** Копия таблицы для редактирования (cells — глубокая копия). */
  static cloneTable(t) {
    return { ...t, cells: (t.cells || []).map((c) => ({ ...c })) };
  }

  /** Применить значения аннотаций (null = удалить), перерисовать, оповестить. */
  _applyEntries(updates) {
    for (const { key, value } of updates) {
      if (value) this.annotations.set(key, value);
      else this.annotations.delete(key);
    }
    this._renderAnnotations();
    if (this.opts.onAnnotationsChange) this.opts.onAnnotationsChange(this.getAnnotations());
  }

  _notifyHistory() {
    if (this.opts.onHistoryChange) this.opts.onHistoryChange({ canUndo: this.canUndo, canRedo: this.canRedo });
  }

  /** Оставить только непустые строковые параметры. */
  _cleanParams({ label, unit, group }) {
    const out = {};
    if (label) out.label = label;
    if (unit) out.unit = unit;
    if (group) out.group = group;
    return out;
  }

  _renderAnnotations() {
    this.renderer.clearOverlays("annotations");
    for (const [key, ann] of this.annotations) {
      const [r, c] = key.split(",").map(Number);
      this.renderer.highlightCell(r, c, ROLES_BY_ID[ann.role].color, {
        stroke: "#5a6b7d",
        opacity: 0.5,
        layer: "annotations",
      });
    }
  }

  getAnnotations() {
    return [...this.annotations.entries()].map(([key, ann]) => {
      const [row, col] = key.split(",").map(Number);
      return { row, col, ...ann };
    });
  }

  /**
   * Ops-рецепт структурных правок текущей сессии (для шаблона маппинга, 3.7).
   * Дёрйв из истории: undo/redo структурных операций автоматически согласован.
   */
  getOps() {
    return this._history
      .filter((e) => e.structural && e.spec)
      .map((e) => e.spec);
  }

  /**
   * Заменить все аннотации (применение шаблона, 3.7) — одно действие истории:
   * дифф по ключам (старые удалены / новые добавлены), откатывается целиком.
   * Аннотации на несуществующих слотах (структура не совпала) пропускаются.
   */
  setAnnotations(list) {
    const next = new Map();
    for (const a of list || []) {
      if (!a || !ROLES_BY_ID[a.role]) continue;
      const anchor = this.renderer.anchorAt(a.row, a.col);
      if (!anchor) continue; // слота нет — структура шаблона не совпала
      const key = `${anchor.row},${anchor.col}`;
      const clean = { role: a.role, ...this._cleanParams(a) };
      next.set(key, clean);
    }
    const entries = [];
    for (const [key, before] of this.annotations) {
      if (!next.has(key)) entries.push({ key, before, after: null });
    }
    for (const [key, after] of next) {
      const before = this.annotations.get(key) || null;
      if (JSON.stringify(before) !== JSON.stringify(after)) entries.push({ key, before, after });
    }
    if (!entries.length) return 0;
    this._applyEntries(entries.map((e) => ({ key: e.key, value: e.after })));
    this._history.push(entries);
    this._redoStack = [];
    this._notifyHistory();
    return entries.length;
  }

  /* --- подсветка предложения LLM (4.5) --- */

  /**
   * Подсветить спорные клетки предложения LLM (confidence < 0.7) в отдельном
   * слое "llm" — поверх ролей, не затирая их. Слоты резолвятся в якоря
   * merged-блоков, дубли исключаются.
   * @param {Array<{row:number,col:number}>} cells
   */
  highlightSuggestions(cells) {
    this.renderer.clearOverlays("llm");
    const seen = new Set();
    for (const { row, col } of cells || []) {
      const anchor = this.renderer.anchorAt(row, col);
      if (!anchor) continue;
      const key = `${anchor.row},${anchor.col}`;
      if (seen.has(key)) continue;
      seen.add(key);
      this.renderer.highlightCell(row, col, "#fff4d6", {
        stroke: "#e6a700",
        opacity: 0.75,
        layer: "llm",
      });
    }
    return seen.size;
  }

  clearSuggestions() {
    this.renderer.clearOverlays("llm");
  }

  static get ROLES() { return CELL_ROLES; }
}
