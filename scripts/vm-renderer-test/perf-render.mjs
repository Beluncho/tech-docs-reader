// Замер времени рендера таблицы в Visual Mapper (подэтап 5.3).
// Логика рендера (TableRenderer.buildGrid + render) на fabric-заглушке:
// без браузера и отрисовки — чистое время сборки объектов таблицы.
// Использование: node perf-render.mjs <table.json> [итераций=20]
import { readFileSync } from "node:fs";
import { TableRenderer } from "./table-renderer.js";

const path = process.argv[2];
if (!path) { console.error("usage: node perf-render.mjs <table.json> [iterations]"); process.exit(2); }
const n = Number(process.argv[3] || 20);
const table = JSON.parse(readFileSync(path, "utf8"));

const fakeCanvas = () => ({ add() {}, remove() {}, clear() {}, setDimensions() {}, setViewportTransform() {}, requestRenderAll() {} });
const collector = () => ({ list: [], add(...o) { this.list.push(...o); }, remove() {}, clear() {}, setDimensions() {}, setViewportTransform() {}, requestRenderAll() {} });

// прогрев (JIT/кэши модулей) + эталон числа объектов
new TableRenderer(fakeCanvas(), table).render();
const warm = collector();
new TableRenderer(warm, table).render();

const times = [];
for (let i = 0; i < n; i++) {
  const o = collector();
  const t0 = performance.now();
  new TableRenderer(o, table).render();
  times.push(performance.now() - t0);
}
times.sort((a, b) => a - b);
const mean = times.reduce((s, x) => s + x, 0) / times.length;
console.log(JSON.stringify({
  table: `${table.n_rows}x${table.n_cols}`,
  cells: table.cells.length,
  objects: warm.list.length,
  iterations: n,
  min_ms: +times[0].toFixed(1),
  mean_ms: +mean.toFixed(1),
  max_ms: +times[times.length - 1].toFixed(1),
}));
