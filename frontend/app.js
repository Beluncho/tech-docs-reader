/* Tech Docs Reader — одна страница «Техническая документация». */
"use strict";

import { VisualMapper } from "./js/visual-mapper/canvas.js";
import { CELL_ROLES, ROLES_BY_ID } from "./js/visual-mapper/roles.js";
import { buildPreview, toCsv } from "./js/visual-mapper/preview.js";
import { applyOps } from "./js/visual-mapper/table-ops.js";

const $ = (id) => document.getElementById(id);
const api = (path, opts) => fetch("/api" + path, opts).then(async (r) => {
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return r.json();
});

window.addEventListener("error", (e) => {
  const f = document.getElementById("fatal");
  if (f) { f.hidden = false; f.textContent = "Ошибка интерфейса: " + (e.message || e.type || "неизвестно"); }
});

let docs = [];
let current = null;       // DocumentOut
let currentResult = null; // ExtractResult
let presets = [];
let llmEnabled = false;
let docTemplateMatches = {}; // tableId → {template_id, template_name, score, method} | null (3.9)

async function refreshHealth() {
  const apiHint = $("api-hint");
  const llmHint = $("llm-hint");
  try {
    const h = await api("/health");
    llmEnabled = h.llm_enabled;
    apiHint.textContent = "API: ок";
    apiHint.className = "hint ok";
    llmHint.textContent = llmEnabled ? "LLM: включён" : "LLM: выключен";
    updateMapperLlmButton(); // кнопка LLM в маппере могла стать доступной (4.5)
  } catch (err) {
    apiHint.textContent = "API недоступен: " + err.message;
    apiHint.className = "hint err";
    llmHint.textContent = "LLM: —";
  }
}

async function refreshPresets() {
  presets = await api("/presets");
  const sel = $("preset-select");
  const prev = sel.value;
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "— без набора —";
  sel.appendChild(none);
  presets.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = (p.builtin ? "● " : "○ ") + p.name;
    sel.appendChild(o);
  });
  if (presets.some((p) => p.id === prev)) sel.value = prev;
}

/** Статус документа для списка/мета-строки (5.4): текст + класс подсветки. */
function docStatusInfo(status) {
  const map = {
    PARSED: ["готов", "st-ok"],
    PARSE_FAILED: ["ошибка разбора", "st-err"],
    OCR_FAILED: ["сканы не распознаны (OCR не настроен)", "st-warn"],
  };
  return map[status] || [status, ""];
}

async function refreshDocs() {
  docs = await api("/documents");
  const list = $("doc-list");
  list.innerHTML = "";
  if (!docs.length) {
    const note = document.createElement("div");
    note.className = "empty-note";
    note.textContent = "Документов пока нет — загрузите первый.";
    list.appendChild(note);
  }
  docs.forEach((d) => {
    const el = document.createElement("div");
    el.className = "doc-item" + (current && current.id === d.id ? " active" : "");
    const fmt = d.fmt.toUpperCase();
    const [stText, stCls] = docStatusInfo(d.status);
    el.innerHTML =
      `<div class="fname">${escapeHtml(d.filename)}</div>` +
      `<div class="fmeta">${fmt} · ${d.pages} стр. · <span class="${stCls}">${escapeHtml(stText)}</span></div>`;
    el.onclick = () => openDoc(d.id);
    list.appendChild(el);
  });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Человекочитаемое сообщение об ошибке API (5.5): код из detail → русский текст
 *  с подсказкой, что делать. Неизвестные коды и сетевые ошибки — как есть. */
function friendlyError(e) {
  const msg = (e && e.message) || String(e || "");
  const code = (msg.match(/^[A-Z][A-Z_]+/) || [""])[0]; // "LLM_FAILED: ..." → LLM_FAILED
  const rest = msg.slice(code.length).replace(/^[:\s—-]+/, "");
  const map = {
    FILE_TOO_LARGE: "файл больше лимита загрузки — уменьшите файл",
    UNSUPPORTED_FILE_TYPE: `формат не поддерживается${rest ? ` (${rest})` : ""} — загрузите PDF, XLSX, XLS, DOCX, CSV или TXT`,
    DOCUMENT_NOT_FOUND: "документ не найден — возможно, удалён. Обновите список",
    NOT_PDF: "операция доступна только для PDF-документов",
    PAGE_NOT_FOUND: "страница не найдена в документе",
    DOCUMENT_PARSE_FAILED: "не удалось разобрать документ — файл повреждён или в неподходящем виде",
    OCR_FAILED: "документ содержит сканы, а OCR не настроен — загрузите текстовый документ или включите OCR",
    PRESET_NOT_FOUND: "набор не найден — возможно, удалён. Обновите список наборов",
    PRESET_NOT_FOUND_OR_BUILTIN: "встроенный набор нельзя изменить — создайте свой на его основе",
    NO_TABLES: "в документе нет таблиц — извлекать нечего",
    NO_PRESET_MATCHED: "набор не выбран: выберите набор в списке, настройте маппинг («Настроить набор» / «🎨 Визуальный маппинг») или извлеките через LLM",
    TABLE_NOT_FOUND: "таблица не найдена — документ мог быть перезагружен. Переоткройте документ",
    MAPPING_NOT_FOUND: "маппинг для таблицы не создан — разметьте роли в визуальном маппере",
    LLM_NOT_CONFIGURED: "LLM не настроен: укажите ключ YANDEXGPT_API_KEY на сервере (перезапустите после)",
    LLM_FAILED: `LLM не смог обработать запрос${rest ? `: ${rest}` : " — повторите позже или настройте маппинг вручную"}`,
    NO_CORRECTIONS: "нет данных для сохранения",
  };
  return map[code] || msg;
}

/** Единый вывод сообщения в #warnings (5.5): cls "err" (красный) / "ok" (зелёный).
 *  Сбрасывается при новом результате/открытии документа. */
function setNotice(text, cls) {
  const w = $("warnings");
  w.textContent = text;
  w.className = cls || "";
}

let uploadBusy = false; // загрузка идёт — повторный drop/выбор файла игнорируем (5.4)

/** Индикатор «Обработка документа…» в зоне загрузки (5.4). */
function setUploadBusy(on, text) {
  uploadBusy = on;
  const dz = $("dropzone");
  const st = $("dropzone-status");
  dz.classList.toggle("busy", on);
  st.hidden = !on;
  if (on) st.className = ""; // новая загрузка стирает прошлую ошибку
  st.textContent = text || "";
}

async function uploadFiles(files) {
  if (uploadBusy) return; // уже обрабатываем — не начинаем параллельную загрузку
  const ocr = $("ocr-check").checked;
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    setUploadBusy(true, `⏳ Обработка документа «${f.name}»… (${i + 1} из ${files.length})`);
    const fd = new FormData();
    fd.append("file", f);
    if (ocr) fd.append("ocr", "true");
    try {
      const d = await api("/documents", { method: "POST", body: fd });
      await refreshDocs();
      await openDoc(d.id);
    } catch (e) {
      // ошибка загрузки — в зону загрузки (документ мог не открыться, #warnings не видно)
      const st = $("dropzone-status");
      st.hidden = false;
      st.className = "err";
      st.textContent = `⚠ ${f.name}: ${friendlyError(e)}`;
      console.error(e);
    }
  }
  setUploadBusy(false);
}

async function openDoc(id) {
  current = await api("/documents/" + id);
  currentResult = current.last_result || null;
  $("doc-view").hidden = false;
  $("empty").hidden = true;
  $("doc-title").textContent = current.filename;
  const sel = $("preset-select");
  sel.value = "";
  if (current.auto_preset_id && presets.some((p) => p.id === current.auto_preset_id)) {
    sel.value = current.auto_preset_id;
  }
  setNotice((currentResult && currentResult.warnings || []).join("; "));
  renderTab(activeTab);
  updateRuleSaveState();
  refreshDocs();
  refreshTemplateMatches(); // индикатор шаблонов маппинга (3.9) — не блокирует открытие
}

/** Мета-строка документа; пере-собирается после обновления шаблонов маппинга. */
function updateDocMetaLine() {
  if (!current) return;
  const lr = currentResult;
  const tplMatches = Object.values(docTemplateMatches).filter(Boolean);
  $("doc-meta").textContent =
    `${current.fmt.toUpperCase()} · ${current.pages} стр. · статус ${docStatusInfo(current.status)[0]}` +
    (current.error ? " · " + current.error : "") +
    (current.auto_preset_id ? " · авто-набор: " + current.auto_preset_id : "") +
    (lr && lr.parameters && lr.parameters.length
      ? ` · результат: ${lr.parameters.length} поз. (${lr.source || "?"})`
      : "") +
    (tplMatches.length
      ? ` · 🎨 шаблоны маппинга: ${pluralRu(tplMatches.length, "таблица", "таблицы", "таблиц")}`
      : "");
}

function pluralRu(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

/** Мачи шаблонов маппинга для всех таблиц документа — одним запросом (3.9). */
async function refreshTemplateMatches() {
  if (!current) return;
  try {
    const out = await api(`/mapping/match-document?document_id=${current.id}`);
    docTemplateMatches = {};
    out.matches.forEach((m) => { docTemplateMatches[m.table_id] = m.template_id ? m : null; });
  } catch (_) {
    docTemplateMatches = {}; // индикатор не критичен
  }
  updateDocMetaLine();
  updateMapperTableSelectLabels();
  drawOverlay(); // рамки таблиц в превью страницы — с маркерами шаблонов
}

let activeTab = "preview";

function setTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-body").forEach((b) => b.classList.toggle("active", b.id === "tab-" + name));
  renderTab(name);
}

function renderTab(name) {
  if (!current) return;
  if (name === "preview") renderPreview();
  else renderParams();
}

let currentPage = 0;
let pageNav = null;

async function renderPreview() {
  const box = $("tab-preview");
  if (current.fmt === "pdf") {
    const pages = current.pages || 1;
    if (pageNav === null) pageNav = 0;
    const p = Math.min(pageNav, pages - 1);
    currentPage = p;
    let html = `<div id="page-nav">`;
    html += `<button id="pg-prev" ${p === 0 ? "disabled" : ""}>◀</button>`;
    html += `<span>стр. ${p + 1} / ${pages}</span>`;
    html += `<button id="pg-next" ${p >= pages - 1 ? "disabled" : ""}>▶</button></div>`;
    html += `<div id="page-container"><img id="page-img" alt="страница"/></div>`;
    box.innerHTML = html;
    const img = $("page-img");
    img.onload = drawOverlay;
    img.src = `/api/documents/${current.id}/page/${p}`;
    $("pg-prev").onclick = () => { pageNav--; renderPreview(); };
    $("pg-next").onclick = () => { pageNav++; renderPreview(); };
  } else {
    // xlsx/docx/csv/txt — табличная сетка
    let html = `<table class="grid"><tr>`;
    const ncols = current.tables.length ? current.tables[0].n_cols : 1;
    for (let j = 0; j < Math.min(ncols, 30); j++) html += `<th>${String.fromCharCode(65 + j)}</th>`;
    html += `</tr>`;
    const rows = current.tables.length ? current.tables[0].cells : [];
    const byRow = {};
    rows.forEach((c) => { (byRow[c.row] = byRow[c.row] || {})[c.col] = c.text; });
    const nrows = current.tables.length ? current.tables[0].n_rows : 0;
    for (let i = 0; i < Math.min(nrows, 60); i++) {
      html += `<tr>`;
      for (let j = 0; j < Math.min(ncols, 30); j++) html += `<td>${escapeHtml(byRow[i] ? byRow[i][j] || "" : "")}</td>`;
      html += `</tr>`;
    }
    box.innerHTML = html + `<p style="font-size:11px;color:#889">Показано до 60 строк таблицы 1. Маппинг — в конструкторе.</p>`;
  }
}

function drawOverlay() {
  const img = $("page-img");
  const wrap = $("page-container");
  if (!img || !wrap || !current) return; // превью страницы не открыто
  wrap.querySelectorAll(".tbl-rect").forEach((e) => e.remove());
  fetch("/api/documents/" + current.id + "/pageinfo/" + currentPage)
    .then((r) => r.json())
    .then(async (info) => {
      const s = img.naturalWidth / info.pdf_width;
      const tables = current.tables.filter((t) => t.page === currentPage);
      for (const t of tables) {
        if (!t.bbox || t.bbox.length < 4) continue;
        const [x0, y0, x1, y1] = t.bbox;
        const rect = document.createElement("div");
        rect.className = "tbl-rect";
        rect.style.cssText = `position:absolute;left:${x0 * s}px;top:${y0 * s}px;width:${(x1 - x0) * s}px;height:${(y1 - y0) * s}px;border:2px solid #f00;pointer-events:none;z-index:5;`;
        const tpl = docTemplateMatches[t.id];
        rect.title = t.id + " (" + t.n_rows + "×" + t.n_cols + ")" +
          (tpl ? ` · 📌 шаблон «${tpl.template_name}»` : "");
        wrap.appendChild(rect);
      }
    })
    .catch(() => {});
}

function renderParams() {
  const box = $("tab-params");
  if (!currentResult || !currentResult.parameters.length) {
    box.innerHTML = `<p style="color:#889">Результатов пока нет. Выберите набор и нажмите «Извлечь» (или «Извлечь через LLM»).</p>`;
    return;
  }
  let html = `<p style="font-size:12px;color:#667">Набор: ${escapeHtml(currentResult.preset_name || "—")} · источник: ${currentResult.source}${currentResult.llm_model ? " · модель: " + escapeHtml(currentResult.llm_model) : ""}</p>`;
  html += `<table class="flat"><tr><th>Параметр</th><th>Значение</th><th>Ед.</th><th>Источник</th><th>Стр.</th><th>Цитата</th></tr>`;
  currentResult.parameters.forEach((p) => {
    const src = p.source === "llm" ? '<span class="src-llm">LLM</span>' : '<span class="src-preset">набор</span>';
    html += `<tr><td>${escapeHtml(p.name)}</td><td>${escapeHtml(p.value)}</td><td>${escapeHtml(p.unit || "")}</td>`;
    html += `<td>${src}</td><td>${p.evidence.page != null ? p.evidence.page : ""}</td><td style="font-size:11px;color:#556">${escapeHtml((p.evidence.quote || "").slice(0, 90))}</td></tr>`;
  });
  html += `</table>`;
  box.innerHTML = html;
}

async function doExtract(useLlm) {
  if (!current) return;
  const presetId = $("preset-select").value || null;
  $("btn-extract").disabled = $("btn-llm").disabled = true;
  try {
    currentResult = await api(`/documents/${current.id}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preset_id: presetId, use_llm: useLlm }),
    });
    setNotice((currentResult.warnings || []).join("; "));
    updateDocMetaLine(); // «результат: N поз.» в мета-строке
    updateRuleSaveState();
    setTab("params");
  } catch (e) {
    setNotice("⚠ Извлечение: " + friendlyError(e), "err");
  } finally {
    $("btn-extract").disabled = $("btn-llm").disabled = false;
  }
}

function updateRuleSaveState() {
  const btn = $("btn-save-rule");
  if (!btn) return;
  btn.hidden = !(
    current &&
    currentResult &&
    currentResult.parameters &&
    currentResult.parameters.length &&
    current.tables &&
    current.tables.length
  );
}

async function saveRuleFromResult() {
  if (!current || !currentResult || !currentResult.parameters || !currentResult.parameters.length) return;
  if (!current.tables || !current.tables.length) {
    alert("Правило сохраняется только для документов с таблицами.");
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  const defaultName = currentResult.preset_name || `Набор от ${today} — ${current.filename}`;
  const name = window.prompt("Имя правила (набора):", defaultName);
  if (name === null || !name.trim()) return;
  const description =
    `Создано из результата: ${currentResult.source}` +
    (currentResult.llm_model ? ` (${currentResult.llm_model})` : "");
  try {
    const created = await api("/presets/from-extraction", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: current.id,
        name: name.trim(),
        description: description,
        preset_id: currentResult.preset_id || null,
      }),
    });
    await refreshPresets();
    $("preset-select").value = created.id;
    setNotice(`✅ Правило сохранено: ${created.name}. Похожие документы подхватятся автоматически.`, "ok");
  } catch (e) {
    setNotice("⚠ Сохранение правила: " + friendlyError(e), "err");
  }
}

/* --- конструктор набора --- */

function openConstructor() {
  if (!current) return;
  const body = $("modal-body");
  const hasTables = current.tables.length > 0;
  let html = `<div class="ctor-intro"><b>Набор</b> — правило, по которому из похожих документов автоматически`;
  html += ` извлекаются параметры: настроили один раз — следующие такие же документы разбираются сами.`;
  html += ` Для сложных таблиц (объединённые ячейки, матрицы) удобнее <b>🎨 Визуальный маппинг</b>.</div>`;
  html += `<div class="field"><label>Название набора</label><input id="p-name" value="Набор для ${escapeHtml(current.filename)}"/></div>`;
  html += `<div class="field"><label>Описание (необязательно)</label><input id="p-desc"/></div>`;
  html += `<div class="field"><label>Тип документа</label><select id="p-doc-type">`;
  html += `<option value="passport">паспорт</option><option value="certificate">сертификат</option>`;
  html += `<option value="tz">техническое задание</option><option value="generic" selected>другое</option></select></div>`;

  html += `<div class="ctor-section">Где искать данные</div>`;
  if (hasTables) {
    html += `<div class="field"><label><input type="radio" name="p-mode" value="table" checked/> В таблице документа — назначить столбцам роли</label></div>`;
    html += `<div id="p-table-block"><div class="field"><label>Таблица</label><select id="p-table">`;
    current.tables.forEach((t) => { html += `<option value="${t.id}">${t.id} (${t.n_rows}×${t.n_cols})</option>`; });
    html += `</select></div><div id="p-columns"></div></div>`;
  }
  html += `<div class="field"><label><input type="radio" name="p-mode" value="text"${hasTables ? "" : " checked"}/> В строках текста вида «Подача: 13 м3/ч» — для txt/docx без таблиц</label>`;
  html += `<div class="ctor-hint">Ищет параметры по всему тексту документа; таблицы при этом игнорируются.</div></div>`;

  html += `<div class="ctor-section">Что извлекать</div>`;
  html += `<div class="field"><label><input type="radio" name="p-scope" value="all" checked/> Все пары «параметр → значение»</label></div>`;
  html += `<div class="field"><label><input type="radio" name="p-scope" value="named"/> Только перечисленные имена</label></div>`;
  html += `<div id="p-named-block"><div class="field"><label>Имена — по одному на строку${hasTables ? " (строки из столбца «📊 Параметр»)" : ""}</label><textarea id="p-labels"></textarea>`;
  html += `<div class="ctor-hint">Например: Подача, Напор, Мощность — извлекутся только строки с этими именами.</div></div></div>`;

  html += `<div class="ctor-section">Дополнительно — можно не трогать</div>`;
  html += `<div class="field"><label>Слова, с которых начинается блок характеристик</label><input id="p-sections" value="технические характеристики, основные параметры"/>`;
  html += `<div class="ctor-hint">Строка-заголовок секции в таблице: параметры ниже неё группируются под этим названием.</div></div>`;
  html += `<div class="field"><label>Единицы измерения (через запятую)</label><input id="p-units" value="м3/ч, л/с, кВт, об/мин, м, мм, кг, т, °С, %, В, А, Гц, МПа, кПа, бар, шт"/>`;
  html += `<div class="ctor-hint">Помогают отделить число от единицы: «13 м3/ч» → значение 13, единица м3/ч.</div></div>`;
  body.innerHTML = html;
  $("modal-backdrop").hidden = false;

  const tableSel = $("p-table");
  if (tableSel) {
    tableSel.onchange = renderColumnRoles;
    renderColumnRoles();
    // выбор режима: «в таблице» — блок таблицы активен, «в строках текста» — затемнён
    document.querySelectorAll('input[name="p-mode"]').forEach((r) => {
      r.onchange = () => { $("p-table-block").classList.toggle("off", constructorMode() !== "table"); };
    });
  }
  // «что извлекать»: «все пары» — список имён не нужен, затемняем
  document.querySelectorAll('input[name="p-scope"]').forEach((r) => {
    r.onchange = () => { $("p-named-block").classList.toggle("off", extractionScope() === "all"); };
  });
  // автозаполнение имён параметров из первого столбца первой таблицы
  if (hasTables) {
    const t = current.tables[0];
    const labels = new Set();
    t.cells.filter((c) => c.col === 0 && c.text.trim()).slice(0, 40).forEach((c) => {
      const t0 = c.text.trim();
      if (!/^[\d.,\s]+$/.test(t0) && t0.length < 80) labels.add(t0);
    });
    $("p-labels").value = [...labels].join("\n");
  }
}

/** Выбранный режим конструктора: "table" (роли столбцов) или "text" (строки «Метка: значение»). */
function constructorMode() {
  const r = document.querySelector('input[name="p-mode"]:checked');
  return r ? r.value : "text";
}

/** Что извлекать: "all" (все пары «параметр → значение») или "named" (только перечисленные имена). */
function extractionScope() {
  const r = document.querySelector('input[name="p-scope"]:checked');
  return r ? r.value : "all";
}

function renderColumnRoles() {
  const t = current.tables.find((x) => x.id === $("p-table").value);
  if (!t) return;
  const cols = t.n_cols;
  const byRow = {};
  t.cells.forEach((c) => { (byRow[c.row] = byRow[c.row] || {})[c.col] = c.text; });
  // первый непустой текст столбца — как образец рядом с ролью (первая строка бывает шапкой/мусором)
  const firstText = (j) => {
    for (let i = 0; i < t.n_rows; i++) {
      const s = byRow[i] ? (byRow[i][j] || "").trim() : "";
      if (s) return s;
    }
    return "";
  };
  const maxRows = 30;
  let html = `<div style="font-size:12px;color:#667">Таблица${t.n_rows > maxRows ? ` — первые ${maxRows} строк из ${t.n_rows}` : ""}:</div>`;
  html += `<div class="ctor-preview"><table class="grid"><tr>`;
  for (let j = 0; j < cols; j++) html += `<th>Столбец ${j + 1}</th>`;
  html += `</tr>`;
  for (let i = 0; i < Math.min(maxRows, t.n_rows); i++) {
    html += `<tr>`;
    for (let j = 0; j < cols; j++) html += `<td>${escapeHtml(byRow[i] ? (byRow[i][j] || "").slice(0, 30) : "")}</td>`;
    html += `</tr>`;
  }
  html += `</table></div>`;
  html += `<div class="field"><label>Роли столбцов — те же, что в визуальном маппинге</label>`;
  for (let j = 0; j < cols; j++) {
    html += `<div class="role-row"><span>${j + 1}.</span><select data-col="${j}">`;
    const opts = [["", "—"], ["label", "📊 Параметр"], ["value", "🔢 Значение"], ["unit", "📏 Единица"], ["section", "📂 Секция"], ["ignore", "🚫 Игнорировать"]];
    opts.forEach(([v, l]) => {
      const sel = j === 0 ? (v === "label" ? " selected" : "") : j === 1 ? (v === "value" ? " selected" : "") : "";
      html += `<option value="${v}"${sel}>${l}</option>`;
    });
    html += `</select><span class="col-sample">${escapeHtml(firstText(j).slice(0, 40))}</span></div>`;
  }
  html += `</div>`;
  html += `<div class="ctor-hint">Назначьте хотя бы «📊 Параметр» и «🔢 Значение». Параметр — имя («Подача») · значение — данные («13») · единица — «м3/ч» отдельно · секция — заголовок группы строк · игнорировать — не извлекать.</div>`;
  $("p-columns").innerHTML = html;
}

async function savePresetFromConstructor() {
  const name = $("p-name").value.trim();
  if (!name) { alert("Укажите название"); return; }
  const cols = {};
  if (constructorMode() === "table" && $("p-table")) {
    const roles = {};
    document.querySelectorAll("#p-columns [data-col]").forEach((sel) => {
      if (sel.value) roles[sel.value] = parseInt(sel.dataset.col, 10);
    });
    cols.count = current.tables.find((x) => x.id === $("p-table").value).n_cols;
    cols.has_header = false;
    cols.roles = roles;
  }
  const labels = $("p-labels").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (extractionScope() === "named" && !labels.length) {
    alert("Перечислите хотя бы одно имя — или выберите «Все пары «параметр → значение»»");
    return;
  }
  const sections = $("p-sections").value.split(",").map((s) => s.trim()).filter(Boolean);
  const units = $("p-units").value.split(",").map((s) => s.trim()).filter(Boolean);
  const payload = {
    name,
    description: $("p-desc").value.trim(),
    doc_type: $("p-doc-type").value,
    columns: cols,
    labels,
    section_keywords: sections,
    header_keywords: [],
    units,
    include_all: extractionScope() === "all",
    keyvalue: constructorMode() === "text",
  };
  try {
    await api("/presets", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("modal-backdrop").hidden = true;
    await refreshPresets();
    setNotice(`✅ Набор «${name}» сохранён`, "ok");
  } catch (e) {
    setNotice("⚠ Сохранение набора: " + friendlyError(e), "err");
  }
}

/* --- визуальный маппинг (этап 3) --- */

let mapper = null; // VisualMapper
let mapperTableId = null; // id смонтированной таблицы (для шаблонов 3.7)
let mapperBaseOps = []; // ops уже учтённые в смонтированной таблице (шаблон) — вне истории

function mapperTables() {
  // для прототипа: любые таблицы с координатной сеткой (cells) подходят
  return (current && current.tables || []).filter((t) => t.cells && t.cells.length);
}

/** Текст опции селекта таблиц маппера: мета + маркер найденного шаблона (3.9). */
function mapperTableOptionText(t) {
  const meta = [
    t.page != null ? `стр. ${t.page + 1}` : null,
    `${t.n_rows}×${t.n_cols}`,
    t.has_merged_cells ? "merged" : null,
    t.is_matrix ? `матрица (${t.orientation})` : null,
  ].filter(Boolean).join(" · ");
  const tpl = docTemplateMatches[t.id];
  return `${t.id} (${meta}${tpl ? " · 📌 шаблон" : ""})`;
}

function updateMapperTableSelectLabels() {
  const sel = $("mapper-table-select");
  if (!sel || $("mapper-backdrop").hidden) return; // маппер закрыт — нечего обновлять
  [...sel.options].forEach((o) => {
    const t = mapperTables().find((x) => x.id === o.value);
    if (!t) return;
    const tpl = docTemplateMatches[t.id];
    o.textContent = mapperTableOptionText(t);
    o.title = tpl ? `Шаблон: «${tpl.template_name}»` : "";
  });
}

function openMapper() {
  const tables = mapperTables();
  if (!tables.length) { alert("В документе нет таблиц для маппинга."); return; }

  const sel = $("mapper-table-select");
  sel.innerHTML = "";
  tables.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.id;
    o.textContent = mapperTableOptionText(t);
    const tpl = docTemplateMatches[t.id];
    if (tpl) o.title = `Шаблон: «${tpl.template_name}»`;
    sel.appendChild(o);
  });
  // по умолчанию — самая большая таблица
  const best = tables.reduce((a, b) => (b.cells.length > a.cells.length ? b : a));
  sel.value = best.id;

  renderMapperLegend();
  buildMapperRoles();
  buildMapperOps();
  ["role-param-label", "role-param-unit", "role-param-group"].forEach((id) => ($(id).value = ""));
  $("mapper-backdrop").hidden = false;
  mountMapperTable(best.id);
}

function mountMapperTable(tableId, edited, baseOps) {
  const t = mapperTables().find((x) => x.id === tableId);
  if (!t) return;
  mapperTableId = tableId;
  mapperBaseOps = baseOps || []; // правки шаблона: в таблице уже, но не в истории
  if (mapper) { mapper.destroy(); mapper = null; }
  // fabric пере-создаёт обёртку canvas при dispose; берём свежий элемент
  const wrap = $("mapper-canvas-wrap");
  wrap.innerHTML = '<canvas id="mapper-canvas"></canvas>';
  mapper = new VisualMapper($("mapper-canvas"), edited || t, {
    onCellClick: showMapperCellInfo,
    onSelectionChange: updateMapperSelection,
    onZoom: updateMapperZoomLabel,
    onHistoryChange: updateMapperHistoryButtons,
    onTableChange: (tb) => { showMapperTableInfo(tb); resetMapperPreview(); updateMapperLlmButton(); resetMapperSuggest(); },
    onAnnotationsChange: () => { resetMapperPreview(); refreshMapperSuggestState(); },
  });
  mapper.render();
  resetMapperPreview();
  if (!t.cells.length) {
    $("mapper-status").textContent = "Таблица пуста (нет ячеек)";
  } else {
    mapper.fitWidth(wrap.clientWidth - 8);
    $("mapper-status").textContent = "";
  }
  showMapperTableInfo(mapper.table);
  checkMapperTemplateMatch();
  refreshMapperTemplates();
  resetMapperSuggest(); // панель LLM не должна переживать смену таблицы
  updateMapperLlmButton();
}

function updateMapperZoomLabel() {
  if (mapper) $("mapper-zoom-reset").textContent = Math.round(mapper.getZoom() * 100) + "%";
}

/** Полный ops-рецепт: базовые правки шаблона + правки из истории маппера. */
function mapperAllOps() {
  return [...mapperBaseOps, ...(mapper ? mapper.getOps() : [])];
}

function showMapperCellInfo(cell) {
  const info = $("mapper-cell-info");
  const span = (cell.rowspan > 1 || cell.colspan > 1)
    ? ` · span ${cell.rowspan}×${cell.colspan}` : "";
  const merged = cell.is_merged
    ? ` · продолжение (${cell.merged_with ? cell.merged_with.join(",") : "?"})` : "";
  info.innerHTML =
    `<div class="mi-meta">r${cell.row}, c${cell.col}${span}${merged}</div>` +
    `<div class="mi-text">«${escapeHtml(cell.text || "(пусто)")}»</div>`;
}

function updateMapperSelection(sel) {
  const box = $("mapper-selection");
  if (!sel || !sel.length) {
    box.textContent = "Кликните по ячейке или протяните диапазон";
    return;
  }
  const rows = sel.map((s) => s.row), cols = sel.map((s) => s.col);
  const r0 = Math.min(...rows), r1 = Math.max(...rows);
  const c0 = Math.min(...cols), c1 = Math.max(...cols);
  const range = (r0 === r1 ? `строка ${r0 + 1}` : `строки ${r0 + 1}–${r1 + 1}`) +
    " × " + (c0 === c1 ? `столбец ${c0 + 1}` : `столбцы ${c0 + 1}–${c1 + 1}`);
  box.innerHTML =
    `<div class="mi-meta">Выделено: ${sel.length} ячеек</div>` +
    `<div class="mi-text">${range}</div>` +
    `<div class="mi-meta">Ctrl+клик — добавить · Shift+клик — расширить</div>`;
}

function showMapperTableInfo(t) {
  const rows = [
    `Размер: ${t.n_rows}×${t.n_cols}`,
    t.page != null ? `Страница: ${t.page + 1}` : null,
    t.header_rows ? `Шапка: ${t.header_rows} стр.` : "Шапка: нет",
    t.has_merged_cells ? "Merged cells: есть" : "Merged cells: нет",
    t.is_matrix ? `Матрица: ${t.orientation === "rows" ? "модели в строках" : "модели в столбцах"}` : "Матрица: нет",
  ].filter(Boolean);
  $("mapper-table-info").innerHTML = rows.map((r) => `<div>${r}</div>`).join("");
}

function renderMapperLegend() {
  const lg = $("mapper-legend");
  lg.innerHTML = "";
  Object.values(CELL_ROLES).forEach((r) => {
    const el = document.createElement("span");
    el.className = "lg-item";
    el.innerHTML = `<span class="lg-swatch" style="background:${r.color}"></span>${r.label}`;
    el.title = r.description;
    lg.appendChild(el);
  });
}

/* --- роли (3.4) --- */

function buildMapperRoles() {
  const box = $("mapper-roles");
  box.innerHTML = "";
  Object.values(CELL_ROLES).forEach((r) => {
    const btn = document.createElement("button");
    btn.className = "role-btn";
    btn.style.background = r.color;
    btn.textContent = r.label;
    btn.title = r.description;
    btn.onclick = () => applyRoleFromPanel(r);
    box.appendChild(btn);
  });
}

function roleParamsFromPanel() {
  return {
    label: $("role-param-label").value.trim(),
    unit: $("role-param-unit").value.trim(),
    group: $("role-param-group").value.trim(),
  };
}

function applyRoleFromPanel(role) {
  if (!mapper) return;
  const selection = mapper.getSelection();
  const n = mapper.applyRoleToSelection(role ? role.id : null, roleParamsFromPanel());
  if (n) logMapperCorrections(selection, role ? role.id : null, "role-button"); // тюнинг промптов (4.6)
  $("mapper-status").textContent = n
    ? (role ? `Роль «${role.label.replace(/^\S+\s/, "")}» назначена: ${n} ячеек` : `Роль снята: ${n} ячеек`)
    : "Сначала выделите ячейки";
}

function updateMapperHistoryButtons() {
  if (!mapper) return;
  $("mapper-undo").disabled = !mapper.canUndo;
  $("mapper-redo").disabled = !mapper.canRedo;
}

/* --- операции с ячейками (3.5) --- */

/** Выполнить операцию над выделением; без выделения — подсказка в статусе. */
function runMapperOp(fn, doneMsg, needSelection = true) {
  if (!mapper) return;
  if (needSelection && !mapper.getSelection().length) {
    $("mapper-status").textContent = "Сначала выделите ячейки";
    return;
  }
  const ok = fn();
  $("mapper-status").textContent = ok
    ? doneMsg
    : (needSelection ? "Нечего менять (выделение уже в нужном виде)" : "Нечего менять");
}

function buildMapperOps() {
  $("mapper-merge").onclick = () =>
    runMapperOp(() => mapper.mergeSelection(), "Ячейки объединены");
  $("mapper-split").onclick = () =>
    runMapperOp(() => mapper.splitSelection(), "Объединённые ячейки разделены");
  $("mapper-transpose").onclick = () =>
    runMapperOp(() => mapper.transpose(), "Таблица транспонирована", false);
  $("mapper-del-rows").onclick = () =>
    runMapperOp(() => mapper.deleteSelectionRows(), "Строки удалены");
  $("mapper-del-cols").onclick = () =>
    runMapperOp(() => mapper.deleteSelectionCols(), "Столбцы удалены");
}

/* --- предпросмотр извлечения (3.6) --- */

let previewResult = null; // кэш последнего предпросмотра

function resetMapperPreview() {
  previewResult = null;
  $("mapper-preview-stats").textContent = "";
  $("mapper-preview-warnings").textContent = "";
  $("mapper-preview-cards").textContent = "";
  $("mapper-export-json").disabled = true;
  $("mapper-export-csv").disabled = true;
}

async function generateMapperPreview() {
  if (!mapper || !current || !mapperTableId) return;
  resetMapperPreview();
  const anns = mapper.getAnnotations();
  if (!anns.length) {
    $("mapper-status").textContent = "Назначьте роли — тогда будет что извлекать";
    return;
  }
  const btn = $("mapper-preview");
  btn.disabled = true;
  try {
    // серверная валидация (3.8) — единая семантика с backend; при недоступности — локальный расчёт
    try {
      const out = await api("/mapping/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          document_id: current.id,
          table_id: mapperTableId,
          ops: mapperAllOps(),
          annotations: anns,
        }),
      });
      previewResult = { cards: out.cards, stats: out.stats, warnings: out.warnings };
    } catch (_) {
      previewResult = buildPreview(mapper.table, anns);
    }
  } finally {
    btn.disabled = false;
  }
  const { cards, stats, warnings } = previewResult;

  $("mapper-preview-stats").innerHTML =
    `Моделей: ${stats.models} · Параметров: ${stats.params} · Значений: ${stats.values}`;
  $("mapper-preview-warnings").innerHTML = warnings.length
    ? warnings.map((w) => `<div class="pv-warn">⚠️ ${escapeHtml(w)}</div>`).join("")
    : `<div class="pv-ok">✓ Конфликтов нет</div>`;

  const box = $("mapper-preview-cards");
  box.innerHTML = "";
  if (!cards.length || !cards.some((c) => c.parameters.length)) {
    box.innerHTML = `<div class="pv-warn">Ничего не извлечено — назначьте роли «Параметр» и «Значение»</div>`;
    return;
  }
  cards.forEach((card) => {
    const el = document.createElement("div");
    el.className = "pv-card";
    const title = card.model == null ? "(без модели)" : escapeHtml(card.model);
    const params = card.parameters.map((p) => {
      const unit = p.unit ? ` ${escapeHtml(p.unit)}` : "";
      const group = p.group ? ` <span class="pv-badge">${escapeHtml(p.group)}</span>` : "";
      const name = p.name ? escapeHtml(p.name) : "<i>без параметра</i>";
      return `<li>${name}: <strong>${escapeHtml(p.value) || "—"}${unit}</strong>${group}</li>`;
    }).join("");
    const conds = card.conditions.length
      ? `<div class="pv-cond">💬 ${card.conditions.map(escapeHtml).join("; ")}</div>` : "";
    el.innerHTML = `<div class="pv-title">${title}</div><ul>${params}</ul>${conds}`;
    box.appendChild(el);
  });

  $("mapper-export-json").disabled = false;
  $("mapper-export-csv").disabled = false;
  $("mapper-status").textContent =
    `Предпросмотр: ${stats.values} значений → ${cards.length} карточек` +
    (warnings.length ? ` · предупреждений: ${warnings.length}` : "");
}

function downloadBlob(name, mime, text) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function exportMapperPreview(kind) {
  if (!mapper || !previewResult) return;
  const t = mapper.table;
  if (kind === "json") {
    const payload = {
      table_id: t.id,
      page: t.page,
      size: [t.n_rows, t.n_cols],
      header_rows: t.header_rows || 0,
      ops: mapperAllOps(), // структурные правки (рецепт для шаблона)
      annotations: mapper.getAnnotations(),
      preview: previewResult,
    };
    downloadBlob(`${t.id}-mapping.json`, "application/json", JSON.stringify(payload, null, 2));
  } else {
    downloadBlob(`${t.id}-preview.csv`, "text/csv;charset=utf-8", toCsv(previewResult));
  }
}

/* --- LLM-ассистент маппинга (4.5–4.6) --- */

let llmSuggest = null; // {result: LLMSuggestOut, lowConf: [...], agreed: Set<"r,c">} — последний ответ LLM

/** Полный сброс предложения LLM (устарело: смена таблицы, структурные правки, роли больше не его). */
function resetMapperSuggest() {
  llmSuggest = null;
  $("mapper-llm-out").hidden = true;
  if (mapper) mapper.clearSuggestions();
}

/**
 * Синхронизировать панель LLM с текущими ролями (4.6): правка роли не стирает
 * список спорных — пункт помечается «исправлено». Полный сброс, если ни одна
 * аннотация LLM больше не совпадает с текущими ролями (undo к своим, шаблон,
 * очистка) или структура менялась.
 */
function refreshMapperSuggestState() {
  if (!llmSuggest || !mapper) return;
  const current = new Map(mapper.getAnnotations().map((a) => [`${a.row},${a.col}`, a.role]));
  const match = llmSuggest.result.annotations.some((a) => current.get(`${a.row},${a.col}`) === a.role);
  if (!match) { resetMapperSuggest(); return; }
  renderMapperLlmOut();
  // подсветка — только по непроверенным спорным
  const pending = pendingMapperLowConf();
  if (pending.length) mapper.highlightSuggestions(pending);
  else mapper.clearSuggestions();
}

/** Спорные клетки, ещё не проверенные пользователем (роль = предложению LLM, без ✓). */
function pendingMapperLowConf() {
  if (!llmSuggest || !mapper) return [];
  const current = new Map(mapper.getAnnotations().map((a) => [`${a.row},${a.col}`, a.role]));
  return llmSuggest.lowConf.filter((a) =>
    current.get(`${a.row},${a.col}`) === a.role && !llmSuggest.agreed.has(`${a.row},${a.col}`));
}

/** Доступность кнопки «🤖 Предложить маппинг» + причина в tooltip. */
function updateMapperLlmButton() {
  const btn = $("mapper-llm-suggest");
  if (!btn) return;
  if (!llmEnabled) {
    btn.disabled = true;
    btn.title = "LLM не настроен: укажите ключ API на сервере (YANDEXGPT_API_KEY)";
  } else if (mapperAllOps().length) {
    btn.disabled = true;
    btn.title = "Не работает при структурных правках: координаты LLM — по исходной сетке таблицы (верните структуру или предложите маппинг до правок)";
  } else {
    btn.disabled = false;
    btn.title = "LLM размечает роли по структуре таблицы — результат заменит текущие роли (↶ вернёт)";
  }
}

/**
 * Запросить у LLM предложение маппинга для текущей таблицы и применить его
 * через setAnnotations (одно действие истории, откатывается Ctrl+Z).
 * Спорные клетки (confidence < 0.7) подсвечиваются и попадают в панель.
 */
async function suggestMapperMapping() {
  if (!mapper || !current || !mapperTableId) return;
  const myAnnCount = mapper.getAnnotations().length;
  if (myAnnCount && !window.confirm(
    `Роли не сохранены шаблоном (${myAnnCount} шт.) — предложение LLM заменит их.\nПродолжить?`
  )) return;

  const tableId = mapperTableId;
  const btn = $("mapper-llm-suggest");
  btn.disabled = true;
  $("mapper-status").textContent = "🤖 LLM анализирует таблицу…";
  let out;
  try {
    out = await api("/llm/suggest-mapping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_id: current.id, table_id: tableId }),
    });
  } catch (err) {
    $("mapper-status").textContent = "LLM: " + friendlyError(err);
    return;
  } finally {
    btn.disabled = false;
  }
  if (!mapper || mapperTableId !== tableId) return; // таблицу сменили, пока шёл запрос

  if (!out.annotations.length) {
    // пустой список — легитимный ответ (мусорная таблица), роли не трогаем
    $("mapper-status").textContent = "LLM не нашёл, что размечать в этой таблице";
    if (out.notes) $("mapper-status").textContent += `: ${out.notes}`;
    return;
  }

  // setAnnotations → onAnnotationsChange → refreshMapperSuggestState():
  // старая панель/подсветка сбросятся здесь же, новые данные запишем после вызова
  const n = mapper.setAnnotations(out.annotations);
  llmSuggest = { result: out, lowConf: out.annotations.filter((a) => a.confidence < 0.7), agreed: new Set() };
  mapper.highlightSuggestions(llmSuggest.lowConf);
  renderMapperLlmOut();
  $("mapper-status").textContent =
    `LLM: применено ${n} ролей (↶ вернёт ваши)` +
    (llmSuggest.lowConf.length ? ` · спорных: ${llmSuggest.lowConf.length}` : "") +
    (out.cached ? " · 📦 из кэша" : "");
}

/**
 * Панель результата LLM: сводка, notes, список спорных клеток.
 * Состояние каждого пункта — производное от текущих ролей (4.6):
 * не проверено (роль = LLM, нет ✓) / исправлено (роль ≠ LLM) / подтверждено (✓).
 */
function renderMapperLlmOut() {
  const box = $("mapper-llm-out");
  const s = llmSuggest;
  if (!s) { box.hidden = true; return; }
  const { result, lowConf, agreed } = s;
  const current = new Map(mapper.getAnnotations().map((a) => [`${a.row},${a.col}`, a.role]));
  const secs = (result.processing_time_ms / 1000).toFixed(1);
  const cached = result.cached ? " · 📦 из кэша" : "";  // повторное предложение без запроса (4.7)
  let html = `<div class="llm-summary">✅ Уверенно: ${result.summary.high_confidence}` +
    ` · ⚠️ Спорных: ${result.summary.low_confidence}` +
    ` · ${escapeHtml(result.model || "?")} · ${secs} с${cached}</div>`;
  if (result.notes) html += `<div class="llm-notes">${escapeHtml(result.notes)}</div>`;
  if (lowConf.length) {
    const done = lowConf.filter((a) =>
      agreed.has(`${a.row},${a.col}`) || current.get(`${a.row},${a.col}`) !== a.role).length;
    html += `<div class="llm-ambiguous">` +
      (done === lowConf.length
        ? `✅ Все спорные проверены`
        : `Спорные клетки (проверено ${done}/${lowConf.length}):`) + `</div>`;
    lowConf.forEach((a) => {
      const key = `${a.row},${a.col}`;
      const cell = mapper.renderer.anchorAt(a.row, a.col);
      const text = cell ? (cell.text || "(пусто)") : "";
      const fixed = current.get(key) !== a.role; // пользователь поменял роль
      const ok = agreed.has(key);                 // «✓ верно»
      const roleLabel = (ROLES_BY_ID[a.role] || { label: a.role }).label.replace(/^\S+\s/, "");
      if (fixed) {
        html += `<div class="llm-item done" data-row="${a.row}" data-col="${a.col}">` +
          `<span class="llm-pos">r${a.row} c${a.col}</span> «${escapeHtml(text.slice(0, 40))}»` +
          ` — ✏️ исправлено (LLM: ${escapeHtml(roleLabel)})</div>`;
        return;
      }
      const chips = (a.alternatives || [])
        .map((alt, i) => {
          const altLabel = (ROLES_BY_ID[alt.role] || { label: alt.role }).label.replace(/^\S+\s/, "");
          return `<button class="llm-chip" data-alt="${i}">→ ${escapeHtml(altLabel)} ${Math.round(alt.confidence * 100)}%</button>`;
        })
        .join("");
      html += `<div class="llm-item${ok ? " done" : ""}" data-row="${a.row}" data-col="${a.col}">` +
        `<span class="llm-pos">r${a.row} c${a.col}</span> «${escapeHtml(text.slice(0, 40))}»` +
        ` — ${escapeHtml(a.role)} ${Math.round(a.confidence * 100)}%` +
        (ok ? ` <span class="llm-ok">✓ верно</span>` : "") +
        (chips && !ok ? chips : "") +
        (!ok ? `<button class="llm-agree" title="Роль LLM верна — оставить">✓ верно</button>` : "") +
        `</div>`;
    });
  }
  box.innerHTML = html;
  box.hidden = false;
  box.querySelectorAll(".llm-item").forEach((el) => {
    const r = +el.dataset.row, c = +el.dataset.col;
    el.querySelectorAll(".llm-chip").forEach((chip) => {
      chip.onclick = (e) => {
        e.stopPropagation();
        const a = llmSuggest && llmSuggest.lowConf.find((x) => x.row === r && x.col === c);
        if (!a) return;
        const alt = (a.alternatives || [])[+chip.dataset.alt];
        if (alt) applyLlmAlternative(a, alt.role);
      };
    });
    const agree = el.querySelector(".llm-agree");
    if (agree) agree.onclick = (e) => {
      e.stopPropagation();
      if (!llmSuggest) return;
      llmSuggest.agreed.add(`${r},${c}`);
      const a = llmSuggest.lowConf.find((x) => x.row === r && x.col === c);
      if (a) logMapperCorrections([a], a.role, "confirm");
      refreshMapperSuggestState(); // аннотации не менялись — перерисуем вручную
    };
    el.onclick = () => {
      if (!mapper) return;
      mapper.selectRange({ row: r, col: c }, { row: r, col: c }); // подсветит и покажет инфо ячейки
    };
  });
}

/**
 * Быстрое исправление спорной клетки (4.6): назначить альтернативную роль
 * прямо из списка, с параметрами аннотации LLM (label/unit/group).
 */
function applyLlmAlternative(ann, role) {
  if (!mapper) return;
  if (mapper.assignRole([{ row: ann.row, col: ann.col }], role,
    { label: ann.label, unit: ann.unit, group: ann.group })) {
    mapper.selectRange({ row: ann.row, col: ann.col }, { row: ann.row, col: ann.col });
    logMapperCorrections([ann], role, "alternative");
  }
}

/**
 * Лог коррекций «LLM предложил → пользователь выбрал» для тюнинга промптов (4.8).
 * Fire-and-forget: сбой записи не мешает интерфейсу. Логируются клетки, где
 * LLM имел аннотацию, а текущая роль отличается от предложенной.
 */
function logMapperCorrections(cells, userRole, via) {
  if (!llmSuggest || !current || !mapperTableId || !mapper || !cells?.length) return;
  const llmBy = new Map(llmSuggest.result.annotations.map((a) => [`${a.row},${a.col}`, a]));
  const annBy = new Map(mapper.getAnnotations().map((a) => [`${a.row},${a.col}`, a]));
  const corrections = [];
  for (const { row, col } of cells) {
    const anchor = mapper.renderer.anchorAt(row, col);
    const key = anchor ? `${anchor.row},${anchor.col}` : `${row},${col}`;
    const a = llmBy.get(key);
    if (!a) continue;                                  // LLM тут ничего не предлагал
    if (annBy.get(key)?.role === a.role) continue;     // роль не поменялась
    corrections.push({
      row: anchor.row, col: anchor.col,
      text: anchor.text || "",
      llm_role: a.role, llm_confidence: a.confidence,
      alternatives: a.alternatives || [],
      user_role: userRole, via,
    });
  }
  if (!corrections.length) return;
  api("/llm/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      document_id: current.id,
      table_id: mapperTableId,
      model: llmSuggest.result.model,
      corrections,
    }),
  }).catch(() => {}); // лог не критичен для интерфейса
}

/* --- шаблоны маппинга (3.7) --- */

/** Автоподбор: показать баннер с найденным шаблоном для текущей таблицы. */
async function checkMapperTemplateMatch() {
  const box = $("mapper-template-match");
  box.hidden = true;
  const tableId = mapperTableId;
  if (!current || !tableId) return;
  try {
    const m = await api(`/mapping/match?document_id=${current.id}&table_id=${encodeURIComponent(tableId)}`);
    if (!m || !mapper || mapperTableId !== tableId) return; // таблицу сменили, пока шли запросы
    const how = m.method === "exact" ? "точное совпадение" : `сходство ${Math.round(m.score * 100)}%`;
    box.innerHTML = "";
    const span = document.createElement("span");
    span.textContent = `Найден шаблон: «${m.template.name}» (${how}) `;
    const btn = document.createElement("button");
    btn.textContent = "Применить";
    btn.onclick = () => {
      btn.disabled = true;
      applyMapperTemplate(m.template).finally(() => { btn.disabled = false; });
    };
    box.append(span, btn);
    box.hidden = false;
  } catch (_) { /* автоподбор не критичен */ }
}

/** Применить шаблон: сервер применяет ops-рецепт и нормализует роли (3.8);
 *  при недоступности — локальный applyOps на клоне исходной таблицы. */
async function applyMapperTemplate(tpl) {
  const t = mapperTables().find((x) => x.id === mapperTableId);
  if (!t || !current) return;
  let edited;
  let anns = tpl.annotations || [];
  try {
    const out = await api("/mapping/apply-template", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: current.id,
        table_id: mapperTableId,
        template_id: tpl.id,
      }),
    });
    edited = out.table;
    anns = out.annotations;
  } catch (_) {
    edited = VisualMapper.cloneTable(t);
    applyOps(edited, tpl.ops || []);
  }
  if (!mapper || mapperTableId !== t.id) return; // таблицу сменили, пока шли запросы
  mountMapperTable(t.id, edited, tpl.ops || []);
  const n = mapper.setAnnotations(anns);
  const skipped = (tpl.annotations || []).length - n;
  $("mapper-status").textContent =
    `Шаблон «${tpl.name}» применён: правок ${ (tpl.ops || []).length }, ролей ${n}` +
    (skipped > 0 ? ` · пропущено ${skipped} (структура не совпала)` : "");
  $("mapper-template-match").hidden = true;
}

/** Сохранить текущий маппинг (роли + правки) как шаблон. */
async function saveMapperTemplate() {
  if (!mapper || !current || !mapperTableId) return;
  const anns = mapper.getAnnotations();
  if (!anns.length) {
    $("mapper-status").textContent = "Назначьте роли — тогда будет что сохранять";
    return;
  }
  const name = $("mapper-tpl-name").value.trim() || `${current.filename} — ${mapperTableId}`;
  const btn = $("mapper-tpl-save");
  btn.disabled = true;
  try {
    const tpl = await api("/presets/save-from-mapping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description: `Из «${current.filename}», таблица ${mapperTableId} (${mapper.table.n_rows}×${mapper.table.n_cols})`,
        document_id: current.id,
        table_id: mapperTableId,
        ops: mapperAllOps(),
        annotations: anns,
      }),
    });
    $("mapper-tpl-name").value = "";
    $("mapper-status").textContent = `Шаблон «${tpl.name}» сохранён`;
    refreshMapperTemplates();
    refreshTemplateMatches(); // индикатор на основном экране и маркер в селекте (3.9)
  } catch (err) {
    $("mapper-status").textContent = "Не сохранено: " + err.message;
  } finally {
    btn.disabled = false;
  }
}

/** Список шаблонов: применить к текущей таблице / удалить. */
async function refreshMapperTemplates() {
  const box = $("mapper-tpl-list");
  let templates;
  try {
    templates = await api("/mapping/templates");
  } catch (_) {
    box.innerHTML = '<div class="mapper-tpl-empty">Список шаблонов недоступен</div>';
    return;
  }
  box.innerHTML = "";
  if (!templates.length) {
    box.innerHTML = '<div class="mapper-tpl-empty">Шаблонов пока нет</div>';
    return;
  }
  templates.forEach((tpl) => {
    const el = document.createElement("div");
    el.className = "mapper-tpl-item";
    const name = document.createElement("span");
    name.className = "tpl-name";
    name.title = tpl.description || tpl.name;
    name.textContent = tpl.name;
    const apply = document.createElement("button");
    apply.textContent = "→";
    apply.title = "Применить к текущей таблице";
    apply.onclick = () => {
      apply.disabled = true;
      applyMapperTemplate(tpl).finally(() => { apply.disabled = false; });
    };
    const del = document.createElement("button");
    del.className = "tpl-del";
    del.textContent = "✕";
    del.title = "Удалить шаблон";
    del.onclick = async () => {
      try {
        await api(`/mapping/templates/${tpl.id}`, { method: "DELETE" });
      } catch (err) {
        $("mapper-status").textContent = "Не удалено: " + err.message;
        return;
      }
      refreshMapperTemplates();
      refreshTemplateMatches(); // маркеры могли пропасть (3.9)
    };
    el.append(name, apply, del);
    box.appendChild(el);
  });
}

/** Есть несохранённые роли — уточнить, прежде чем терять их (3.10). */
function confirmLoseAnnotations(action) {
  const n = mapper ? mapper.getAnnotations().length : 0;
  if (!n) return true;
  return window.confirm(
    `Роли не сохранены шаблоном (${n} шт.) — при ${action} они пропадут.\nПродолжить?`
  );
}

function closeMapper() {
  if (!confirmLoseAnnotations("закрытии маппера")) return;
  if (mapper) { mapper.destroy(); mapper = null; }
  $("mapper-backdrop").hidden = true;
}

/* --- инициализация --- */
document.addEventListener("DOMContentLoaded", () => {
  refreshHealth();
  refreshPresets().then(refreshDocs);

  const dz = $("dropzone");
  const openFileDialog = () => { if (!uploadBusy) $("file-input").click(); };
  dz.ondragover = (e) => { e.preventDefault(); if (!uploadBusy) dz.classList.add("over"); };
  dz.ondragleave = () => dz.classList.remove("over");
  dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); uploadFiles(e.dataTransfer.files); };
  dz.onclick = (e) => { if (e.target.closest("label")) return; openFileDialog(); };
  $("btn-empty-upload").onclick = openFileDialog;
  $("file-input").onchange = (e) => { uploadFiles(e.target.files); e.target.value = ""; };

  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => setTab(t.dataset.tab)));
  $("btn-extract").onclick = () => doExtract(false);
  $("btn-llm").onclick = () => doExtract(true);
  $("btn-constructor").onclick = openConstructor;
  $("btn-mapper").onclick = openMapper;
  $("mapper-close").onclick = closeMapper;
  $("mapper-backdrop").onclick = (e) => { if (e.target === $("mapper-backdrop")) closeMapper(); };
  $("mapper-table-select").onchange = (e) => {
    if (!confirmLoseAnnotations("переключении таблицы")) {
      e.target.value = mapperTableId; // откат выбора, остаёмся на текущей
      return;
    }
    mountMapperTable(e.target.value);
  };
  $("mapper-zoom-in").onclick = () => mapper && mapper.zoomIn();
  $("mapper-zoom-out").onclick = () => mapper && mapper.zoomOut();
  $("mapper-zoom-reset").onclick = () => mapper && mapper.resetZoom();
  $("mapper-clear-annotations").onclick = () => mapper && mapper.clearAnnotations();

  $("btn-save-rule").onclick = saveRuleFromResult;
  $("btn-preset-save").onclick = savePresetFromConstructor;
  $("btn-modal-close").onclick = () => ($("modal-backdrop").hidden = true);
  $("modal-backdrop").onclick = (e) => { if (e.target === $("modal-backdrop")) $("modal-backdrop").hidden = true; };

  // роли и история маппера (3.4)
  $("mapper-remove-role").onclick = () => applyRoleFromPanel(null);
  $("mapper-undo").onclick = () => mapper && mapper.undo();
  $("mapper-redo").onclick = () => mapper && mapper.redo();

  // операции с ячейками (3.5) и предпросмотр (3.6)
  $("mapper-preview").onclick = generateMapperPreview;
  $("mapper-export-json").onclick = () => exportMapperPreview("json");
  $("mapper-export-csv").onclick = () => exportMapperPreview("csv");
  $("mapper-export-json").disabled = true;
  $("mapper-export-csv").disabled = true;

  // LLM-ассистент маппинга (4.5)
  $("mapper-llm-suggest").onclick = suggestMapperMapping;

  // шаблоны маппинга (3.7)
  $("mapper-tpl-save").onclick = saveMapperTemplate;
  document.addEventListener("keydown", (e) => {
    if ($("mapper-backdrop").hidden || !mapper) return;
    if (e.key === "Escape") { closeMapper(); return; }
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    const k = e.key.toLowerCase();
    if (k === "z" && !e.shiftKey) { e.preventDefault(); mapper.undo(); }
    else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); mapper.redo(); }
    else if (k === "s") { e.preventDefault(); saveMapperTemplate(); }
  });
});
