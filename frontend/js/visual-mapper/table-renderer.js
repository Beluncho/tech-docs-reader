/* Отрисовка таблицы (TableOut из API) на fabric.Canvas.
 *
 * Модель: table.cells — плоский список ячеек с row/col/rowspan/colspan/is_merged
 * (контракт этапа 2: merged-якорь несёт текст и объединённый bbox, продолжения
 * помечены is_merged + merged_with). Рендерим только якоря; продолжения пропускаем.
 *
 * Сетка окружена заголовками осей (Excel-подобная адресация: слева номера строк,
 * сверху — колонок). Зум — viewportTransform, размер canvas = содержимое × зум,
 * прокрутка — обычным скроллом контейнера (overflow: auto).
 */
"use strict";

import { Rect, Textbox } from "../../vendor/fabric-6.7.1.min.mjs";

const PAD = 6;          // внутренний отступ текста в ячейке
const MIN_COL_W = 60;   // минимальная ширина колонки, px
const MAX_COL_W = 260;  // максимальная ширина колонки, px
const CHAR_W = 7;       // оценка ширины символа для авто-подбора, px
const LINE_H = 16;      // высота строки текста, px
const ROW_PAD = 8;      // вертикальный отступ в ячейке

export const COL_HDR_W = 30;  // ширина колонки с номерами строк, px
export const ROW_HDR_H = 20;  // высота строки с номерами колонок, px

export class TableRenderer {
  /**
   * @param {Canvas} canvas — fabric.Canvas
   * @param {object} table — TableOut: {id, cells, n_rows, n_cols, header_rows, ...}
   * @param {object} [opts] — {onZoom} колбэк после смены зума
   */
  constructor(canvas, table, opts = {}) {
    this.canvas = canvas;
    this.table = table;
    this.opts = opts;
    this.zoom = 1;
    this.cellRects = new Map(); // "row,col" → Rect якорной ячейки
    this._overlayLayers = new Map(); // layer → [Rect, ...] подсветки
    this._layout();
  }

  /* --- модель → сетка --- */

  /** Плоский cells → карта "row,col" → ячейка. */
  buildGrid() {
    const grid = new Map();
    for (const c of this.table.cells || []) grid.set(`${c.row},${c.col}`, c);
    return grid;
  }

  /** Якорная (видимая) ячейка для позиции [row, col]: сама, либо якорь merged-блока. */
  anchorAt(row, col) {
    const grid = this.grid || (this.grid = this.buildGrid());
    const c = grid.get(`${row},${col}`);
    if (!c) return null;
    if (c.is_merged && c.merged_with) {
      const [r0, c0] = c.merged_with;
      return grid.get(`${r0},${c0}`) || null;
    }
    return c;
  }

  /** Ячейка в пределах сетки (row < n_rows, col < n_cols)? */
  _inGrid(c) {
    return c.row >= 0 && c.col >= 0 && c.row < this.nRows && c.col < this.nCols;
  }

  /* --- раскладка --- */

  _layout() {
    this.nRows = Math.max(0, this.table.n_rows || 0);
    this.nCols = Math.max(0, this.table.n_cols || 0);
    const grid = this.grid || (this.grid = this.buildGrid());

    // ширина колонки — по самому длинному тексту в колонке (учитывая span)
    this.colW = new Array(this.nCols).fill(MIN_COL_W);
    for (const c of grid.values()) {
      if (c.is_merged || !this._inGrid(c)) continue;
      const span = Math.max(1, c.colspan || 1);
      const need = Math.min(MAX_COL_W, (c.text || "").trim().length * CHAR_W + 2 * PAD);
      const perCol = need / Math.max(1, span);
      for (let j = c.col; j < Math.min(this.nCols, c.col + span); j++) {
        this.colW[j] = Math.max(this.colW[j], perCol);
      }
    }

    // высота строки — по числу строк текста после переноса
    this.rowH = new Array(this.nRows).fill(LINE_H + ROW_PAD);
    for (const c of grid.values()) {
      if (c.is_merged || !this._inGrid(c)) continue;
      const span = Math.max(1, c.rowspan || 1);
      const width = this._spanWidth(c, "col");
      const lines = Math.max(1, Math.ceil(((c.text || "").length * CHAR_W) / Math.max(20, width - 2 * PAD)));
      const perRow = (lines * LINE_H + ROW_PAD) / Math.max(1, span);
      for (let i = c.row; i < Math.min(this.nRows, c.row + span); i++) {
        this.rowH[i] = Math.max(this.rowH[i], perRow);
      }
    }

    // координаты границ (offset[i] — начало строки/колонки); ось начинается
    // после заголовков: offX[0] = COL_HDR_W, offY[0] = ROW_HDR_H
    this.offX = new Array(this.nCols + 1).fill(COL_HDR_W);
    for (let j = 0; j < this.nCols; j++) this.offX[j + 1] = this.offX[j] + this.colW[j];
    this.offY = new Array(this.nRows + 1).fill(ROW_HDR_H);
    for (let i = 0; i < this.nRows; i++) this.offY[i + 1] = this.offY[i] + this.rowH[i];

    this.gridW = this.nCols ? this.offX[this.nCols] - COL_HDR_W : 0;
    this.gridH = this.nRows ? this.offY[this.nRows] - ROW_HDR_H : 0;
    this.totalW = COL_HDR_W + this.gridW;
    this.totalH = ROW_HDR_H + this.gridH;
  }

  _spanWidth(c, axis) {
    if (axis === "col") {
      const to = Math.min(this.nCols, c.col + Math.max(1, c.colspan || 1));
      let w = 0;
      for (let j = c.col; j < to; j++) w += this.colW[j] || 0;
      return w;
    }
    const to = Math.min(this.nRows, c.row + Math.max(1, c.rowspan || 1));
    let h = 0;
    for (let i = c.row; i < to; i++) h += this.rowH[i] || 0;
    return h;
  }

  /* --- рендер --- */

  render() {
    this.canvas.clear();
    this.cellRects.clear();
    this._overlayLayers.clear();
    const grid = this.grid || (this.grid = this.buildGrid());
    this._renderAxis();
    for (const c of grid.values()) {
      if (c.is_merged || !this._inGrid(c)) continue; // продолжения и мусор вне сетки не рисуем
      const x = this.offX[c.col] || 0;
      const y = this.offY[c.row] || 0;
      const w = this._spanWidth(c, "col");
      const h = this._spanWidth(c, "row");
      const isHeader = this.table.header_rows && c.row < this.table.header_rows;

      const rect = new Rect({
        left: x,
        top: y,
        width: w,
        height: h,
        fill: isHeader ? "#eef2f6" : "#ffffff",
        stroke: "#b8c0ca",
        strokeWidth: 1,
        selectable: false,
        evented: true,
        hoverCursor: "pointer",
        data: { row: c.row, col: c.col, rowspan: c.rowspan || 1, colspan: c.colspan || 1 },
      });
      this.canvas.add(rect);
      this.cellRects.set(`${c.row},${c.col}`, rect);

      const text = (c.text || "").trim();
      if (text) {
        const tb = new Textbox(text, {
          left: x + PAD,
          top: y + ROW_PAD / 2,
          width: Math.max(20, w - 2 * PAD),
          fontSize: 12,
          lineHeight: LINE_H / 12,
          fill: "#1c2430",
          selectable: false,
          evented: false, // клики проходят к Rect под текстом
        });
        this.canvas.add(tb);
      }
    }
    this._applyZoom();
  }

  /**
   * Перестроиться после структурной правки таблицы (3.5): сброс кэша сетки,
   * новая раскладка, полный ре-рендер. Зум сохраняется.
   */
  refresh() {
    this.grid = null;
    this._layout();
    this.render();
  }

  /** Заголовки осей: номера колонок сверху, номера строк слева. */
  _renderAxis() {
    const hdr = (x, y, w, h, label, strong) => {
      this.canvas.add(new Rect({
        left: x, top: y, width: w, height: h,
        fill: strong ? "#dde4ec" : "#e9edf2",
        stroke: "#c4ccd6", strokeWidth: 1,
        selectable: false, evented: false,
      }));
      if (label !== "") this.canvas.add(new Textbox(String(label), {
        left: x, top: y + (h - 12) / 2, width: w,
        fontSize: 10, fill: "#5a6572", textAlign: "center",
        selectable: false, evented: false,
      }));
    };
    // угол
    hdr(0, 0, COL_HDR_W, ROW_HDR_H, "", true);
    for (let j = 0; j < this.nCols; j++) {
      hdr(this.offX[j], 0, this.colW[j], ROW_HDR_H, j + 1, true);
    }
    for (let i = 0; i < this.nRows; i++) {
      hdr(0, this.offY[i], COL_HDR_W, this.rowH[i], i + 1, false);
    }
  }

  /* --- зум --- */

  setZoom(z) {
    this.zoom = Math.min(3, Math.max(0.3, z));
    this._applyZoom();
  }

  zoomBy(factor) {
    this.setZoom(this.zoom * factor);
  }

  /** Вписать таблицу по ширине в availablePx (по высоте — скролл). */
  fitWidth(availablePx) {
    if (!this.totalW || !availablePx) return;
    this.setZoom(Math.min(1, availablePx / this.totalW));
  }

  _applyZoom() {
    // viewportTransform масштабирует содержимое; размер canvas = окно без зума
    this.canvas.setDimensions({ width: this._viewportW(), height: this._viewportH() });
    this.canvas.setViewportTransform([this.zoom, 0, 0, this.zoom, 0, 0]);
    this.canvas.requestRenderAll();
    if (this.opts.onZoom) this.opts.onZoom(this.zoom);
  }

  _viewportW() {
    return Math.max(300, Math.ceil(this.totalW * this.zoom) + 4);
  }

  _viewportH() {
    return Math.max(200, Math.ceil(this.totalH * this.zoom) + 4);
  }

  /* --- подсветка (используется выделением/ролями) --- */

  /** Число активных подсветок (всего или в конкретном слое). */
  overlayCount(layer) {
    if (layer === undefined) {
      let n = 0;
      for (const arr of this._overlayLayers.values()) n += arr.length;
      return n;
    }
    return (this._overlayLayers.get(layer) || []).length;
  }

  /**
   * Убрать подсветки. Без аргумента — все слои, с аргументом — только слой
   * (например "selection" или "annotations" — слои независимы).
   */
  clearOverlays(layer) {
    if (layer === undefined) {
      for (const arr of this._overlayLayers.values()) this._removeOverlays(arr);
      this._overlayLayers.clear();
      return;
    }
    const arr = this._overlayLayers.get(layer);
    if (arr) {
      this._removeOverlays(arr);
      this._overlayLayers.delete(layer);
    }
  }

  _removeOverlays(arr) {
    for (const o of arr) this.canvas.remove(o);
  }

  /**
   * Подсветить ячейку (по позиции в сетке — merged-блок подсвечивается якорем).
   * Слои позволяют совмещать подсветки: выделение не затирает роли и наоборот.
   */
  highlightCell(row, col, color, { stroke = "#1467c9", opacity = 0.35, layer = "default" } = {}) {
    const anchor = this.anchorAt(row, col);
    if (!anchor) return;
    const x = this.offX[anchor.col] || 0;
    const y = this.offY[anchor.row] || 0;
    const w = this._spanWidth(anchor, "col");
    const h = this._spanWidth(anchor, "row");
    const o = new Rect({
      left: x,
      top: y,
      width: w,
      height: h,
      fill: color,
      opacity,
      stroke,
      strokeWidth: 2,
      selectable: false,
      evented: false, // не перекрывать клики по ячейкам
    });
    this.canvas.add(o);
    if (!this._overlayLayers.has(layer)) this._overlayLayers.set(layer, []);
    this._overlayLayers.get(layer).push(o);
    this.canvas.requestRenderAll();
    return o;
  }
}
