/* Предпросмотр извлечения (3.6) — чистые функции над таблицей и аннотациями.
 *
 * Применяет маппинг (роли из roles.js) к таблице и строит то, что будет
 * извлечено. Без fabric/backend — те же данные доступны UI и тест-харнессу.
 *
 * Семантика:
 * - model-якорь → карточка; значения без модели → карточка «(без модели)»;
 * - значение (якорь с зоной rowspan×colspan) привязывается к параметру:
 *   ближайший param-якорь, покрывающий строку значения и левее по колонкам
 *   (ключевые таблицы, rowspan-параметры в col 0), иначе покрывающий колонку
 *   значения и выше (шапка-матрицы);
 * - модели, пересекающие значение «перпендикулярно» (∩ по строкам и модель
 *   левее, или ∩ по колонкам и модель выше): merged-значение уходит всем
 *   накрытым моделям («0,0036 (13)» rowspan 18 → 18 моделей);
 * - unit = unit аннотации параметра || ближайший unit-якорь (строка→колонка);
 * - group = group аннотации параметра || section-якорь, покрывающий значение;
 * - condition → примечания карточки; ignore исключается полностью.
 */
"use strict";

const key = (r, c) => `${r},${c}`;

/** Зона якоря (диапазон строк/колонок). */
function zone(cell) {
  return {
    r0: cell.row, r1: cell.row + Math.max(1, cell.rowspan || 1) - 1,
    c0: cell.col, c1: cell.col + Math.max(1, cell.colspan || 1) - 1,
  };
}

const coversRow = (z, r) => z.r0 <= r && r <= z.r1;
const coversCol = (z, c) => z.c0 <= c && c <= z.c1;
const leftOf = (a, b) => a.c1 < b.c0;   // a целиком левее b
const above = (a, b) => a.r1 < b.r0;    // a целиком выше b
const rowsIntersect = (a, b) => a.r0 <= b.r1 && b.r0 <= a.r1;
const colsIntersect = (a, b) => a.c0 <= b.c1 && b.c0 <= a.c1;

/**
 * Собрать предпросмотр извлечения.
 * @param {object} table — таблица (клон маппера, после структурных правок)
 * @param {Array} annotations — [{row, col, role, label?, unit?, group?}] (якоря)
 * @returns {{cards: Array, stats: {models, params, values}, warnings: string[]}}
 */
export function buildPreview(table, annotations) {
  const grid = new Map();
  for (const c of table.cells || []) grid.set(key(c.row, c.col), c);

  const byRole = { model: [], param: [], value: [], unit: [], section: [], condition: [] };
  for (const a of annotations || []) {
    const cell = grid.get(key(a.row, a.col));
    if (!cell || !byRole[a.role]) continue; // ignore и неизвестные роли пропускаем
    byRole[a.role].push({ ...a, cell, zone: zone(cell) });
  }

  const warnings = [];
  const cards = new Map(); // ключ модели → карточка
  const DEFAULT = "__default__";
  const defaultCard = () => {
    if (!cards.has(DEFAULT)) cards.set(DEFAULT, { model: null, parameters: [], conditions: [] });
    return cards.get(DEFAULT);
  };
  const cardFor = (m) => {
    const k = key(m.cell.row, m.cell.col);
    if (!cards.has(k)) {
      cards.set(k, { model: (m.cell.text || "").trim(), parameters: [], conditions: [] });
    }
    return cards.get(k);
  };

  // Параметр значения: (а) покрывает строку якоря и левее, самый правый;
  // (б) иначе покрывает колонку и выше, самый нижний.
  const findParam = (v) => {
    let best = null;
    for (const p of byRole.param) {
      if (coversRow(p.zone, v.cell.row) && leftOf(p.zone, v.zone)) {
        if (!best || p.zone.c1 > best.zone.c1) best = p;
      }
    }
    if (best) return best;
    for (const p of byRole.param) {
      if (coversCol(p.zone, v.cell.col) && above(p.zone, v.zone)) {
        if (!best || p.zone.r1 > best.zone.r1) best = p;
      }
    }
    return best;
  };

  // Модели значения: перпендикулярное пересечение зон.
  const findModels = (v) => {
    const out = [];
    for (const m of byRole.model) {
      if (rowsIntersect(m.zone, v.zone) && leftOf(m.zone, v.zone)) out.push(m);
      else if (colsIntersect(m.zone, v.zone) && above(m.zone, v.zone)) out.push(m);
    }
    return out;
  };

  // Единица значения: unit-якорь в строке (ближайший по колонке), иначе выше в колонке.
  const findUnit = (v) => {
    let best = null;
    for (const u of byRole.unit) {
      if (u.cell.row === v.cell.row) {
        if (!best || Math.abs(u.cell.col - v.cell.col) < Math.abs(best.cell.col - v.cell.col)) best = u;
      }
    }
    if (best) return (best.cell.text || "").trim();
    for (const u of byRole.unit) {
      if (coversCol(u.zone, v.cell.col) && above(u.zone, v.zone)) {
        if (!best || u.zone.r1 > best.zone.r1) best = u;
      }
    }
    return best ? (best.cell.text || "").trim() : undefined;
  };

  // Группа значения: section-якорь, накрывающий значение по строкам
  // (вертикальная секция в col 0 — левее значения, или горизонтальная полоса).
  const findSection = (v) => {
    for (const s of byRole.section) {
      if (rowsIntersect(s.zone, v.zone) && (colsIntersect(s.zone, v.zone) || leftOf(s.zone, v.zone))) {
        return (s.cell.text || "").trim();
      }
    }
    return undefined;
  };

  const usedParams = new Set();
  const paramNames = new Set();

  for (const v of byRole.value) {
    const p = findParam(v);
    const value = (v.cell.text || "").trim();
    const name = p ? (p.label || (p.cell.text || "").trim()) : null;
    const models = findModels(v);
    const targets = models.length ? models.map(cardFor) : [defaultCard()];

    if (!p) warnings.push(`Значение «${value || "(пусто)"}» (стр. ${v.cell.row + 1}, кол. ${v.cell.col + 1}) без параметра`);
    else usedParams.add(key(p.cell.row, p.cell.col));
    if (!value) warnings.push(`Пустое значение${name ? ` параметра «${name}»` : ""} (стр. ${v.cell.row + 1}, кол. ${v.cell.col + 1})`);
    if (name) paramNames.add(name);

    const unit = (p && p.unit) || findUnit(v);
    const group = (p && p.group) || findSection(v);
    for (const card of targets) {
      card.parameters.push({ name, value, unit, group, row: v.cell.row, col: v.cell.col });
    }
  }

  // сводное предупреждение про значения вне моделей
  const dc = cards.get(DEFAULT);
  if (dc && byRole.model.length && dc.parameters.length) {
    warnings.push(`Значений вне моделей: ${dc.parameters.length} — карточка «(без модели)»`);
  }

  // параметры без значений
  for (const p of byRole.param) {
    if (usedParams.has(key(p.cell.row, p.cell.col))) continue;
    warnings.push(`Параметр «${p.label || (p.cell.text || "").trim()}» без значений`);
  }

  // несколько значений у одного (модель, параметр)
  for (const card of cards.values()) {
    const seen = new Map();
    for (const e of card.parameters) {
      const k = e.name || "";
      seen.set(k, (seen.get(k) || 0) + 1);
    }
    for (const [k, n] of seen) {
      if (n > 1) warnings.push(`Несколько значений (${n}) у параметра «${k || "?"}»${card.model ? ` модели «${card.model}»` : " (без модели)"}`);
    }
  }

  // примечания → карточки моделей (по тому же правилу привязки)
  for (const cond of byRole.condition) {
    const text = (cond.cell.text || "").trim();
    if (!text) continue;
    const models = findModels(cond);
    const targets = models.length ? models.map(cardFor) : [defaultCard()];
    for (const card of targets) {
      if (!card.conditions.includes(text)) card.conditions.push(text);
    }
  }

  // карточки «(без модели)» — последняя; порядок остальных — по якорям моделей
  const ordered = [];
  const sortedModels = [...byRole.model].sort((a, b) => a.cell.row - b.cell.row || a.cell.col - b.cell.col);
  for (const m of sortedModels) {
    const card = cards.get(key(m.cell.row, m.cell.col));
    if (card) ordered.push(card);
  }
  if (cards.has(DEFAULT)) ordered.push(cards.get(DEFAULT));

  return {
    cards: ordered,
    stats: {
      models: ordered.filter((c) => c.model !== null).length,
      params: paramNames.size,
      values: byRole.value.length,
    },
    warnings,
  };
}

/** CSV предпросмотра: model;param;value;unit;group;condition («;», экранирование кавычек). */
export function toCsv(result) {
  const esc = (s) => {
    const v = s == null ? "" : String(s);
    return /[";\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
  };
  const rows = ["model;param;value;unit;group;condition"];
  for (const card of result.cards) {
    const model = card.model == null ? "(без модели)" : card.model;
    if (card.parameters.length) {
      for (const p of card.parameters) {
        rows.push([model, p.name || "", p.value || "", p.unit || "", p.group || "", ""].map(esc).join(";"));
      }
    }
    for (const c of card.conditions) rows.push([model, "", "", "", "", c].map(esc).join(";"));
  }
  return rows.join("\r\n") + "\r\n";
}
