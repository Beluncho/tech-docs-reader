"""Детерминированное извлечение ТХ по «набору» (маппинг + словарь меток)."""
from __future__ import annotations

import re

from app.loaders._base import LoadResult
from app.matrix import extract_matrices
from app.models import Evidence, Parameter
from app.normalize import collapse_ws, looks_numeric, normalize_header
from app.preset import Table

DEFAULT_UNITS = [
    # расход / объём
    "м3/ч", "м3/час", "м³/ч", "м3/мин", "м³/мин", "м3/с", "м³/с", "л/с", "л/мин", "л/ч",
    "см3/мин", "см³/мин", "м3", "м³", "см3", "см³", "л", "мл",
    "m3/h", "m³/h", "l/min", "l/s", "m3/min", "m³/min", "m3", "m³", "l", "ml",
    # масса
    "кг", "т", "т/ч", "кгс/см2", "кгс/см²", "г",
    "kg", "g", "t",
    # мощность / энергия
    "кВт", "квт", "квт/ч", "квт·ч", "вт", "л.с.", "ква", "кв·а",
    "kw", "w", "kva", "kwh",
    # скорость
    "об/мин", "об/с", "мин-1", "rpm", "об/мин.", "мин",
    # время
    "ч", "час", "мин.", "с", "мс", "год", "лет", "сут", "ч.",
    "h", "min", "s", "ms", "y",
    # длина / размер
    "мм", "см", "м", "км", "мкм", "мм²", "дм", "in", "дюйм",
    "mm", "cm", "m", "km",
    # температура
    "°с", "°с.", "°c", "°c.", "°f", "к", "сст", "сСт",
    # электричество
    "в", "а", "гц", "кв", "ма", "мвт", "вт", "v", "a", "hz", "kv", "w", "ma",
    # давление
    "мпа", "кпа", "бар", "па", "мм рт. ст.", "атм", "кгс/см", "psi",
    "mpa", "kpa", "bar", "pa",
    # прочее
    "%", "н", "кн", "градус", "шт", "раз", "дб", "db", "гц.", "ppm",
]

SKIP_LABELS = {
    "№", "№ п/п", "п/п", "nn", "номер", "кол-во", "количество", "примечание", "описание",
    "номер документа", "дата заказа", "дата", "страница", "версия", "идент. номер",
    "номер поз", "номер позиции заказчика", "заказчик", "количество мест", "подпись",
    "печать", "лист", "листов", "согласовано", "утверждаю", "разработал", "проверил",
    "таблица", "должность", "организация", "договор", "номер договора", "счет",
    "содержание", "оглавление", "введение", "серийный номер", "номер артикула", "артикул",
    "юридический адрес", "электронная почта", "телефон", "тел/факс", "тел", "факс",
    "e-mail", "email", "отдел", "окк", "дата выпуска", "срок годности", "cno", "серийный №",
}

# префиксы служебных/структурных меток (по нормализованному тексту)
SKIP_PREFIXES = (
    "номер", "кол-во", "количество", "дата", "страница", "версия", "идент", "подпись",
    "печать", "лист", "договор", "счет", "счёт", "таблица", "продолжение", "рисунок",
    "приложение", "содержание", "оглавление", "телефон", "тел", "факс", "email",
    "e-mail", "адрес", "сайт", "www", "отдел", "окк", "организация", "должность",
    "утвержда", "согласовано", "разработал", "проверил", "заказчик", "число", "артикул",
    "кол",
)


def _has_junk_prefix(nt: str) -> bool:
    for p in SKIP_PREFIXES:
        if nt.startswith(p) and (len(nt) == len(p) or not nt[len(p)].isalpha()):
            if p == "число" and "полюс" in nt:
                continue  # «Число полюсов» — технический параметр, а не запись меню
            return True
    return False


def _split_value_unit(value_text: str, units: list[str]) -> tuple[str, str | None]:
    vt = collapse_ws(value_text)
    if not vt:
        return vt, None
    lowered = vt.lower()
    for unit in sorted(units, key=len, reverse=True):
        u = unit.strip().lower()
        if not u:
            continue
        if lowered.endswith(u):
            idx = len(lowered) - len(u)
            if idx == 0:
                return vt[:idx].strip(), vt[idx:].strip()
            prev = lowered[idx - 1]
            if prev.isspace() or prev.isdigit() or prev in "+-–—(≤≥":
                return vt[:idx].strip(), vt[idx:].strip()
    # fallback: «число + нечисловой хвост» («2,35 bar.r» -> 2,35 + bar.r)
    m = re.match(r"^\s*([-–—]?\d+(?:[.,]\d+)?)\s+([^\s][^0-9]{0,13})\s*$", vt)
    if m:
        return m.group(1), m.group(2)
    return vt, None


def _looks_like_value(value_text: str, units: list[str]) -> bool:
    """Отсев мусора: значение должно выглядеть как число или «число + единица»."""
    vt = collapse_ws(value_text)
    if not vt:
        return False
    if looks_numeric(vt):
        return True
    val, unit = _split_value_unit(vt, units)
    if unit and looks_numeric(val):
        return True
    # коды/составные значения с цифрами тоже берём, но не длинные простыни текста
    return bool(re.search(r"\d", vt)) and len(vt) <= 60


def _starts_with_number(text: str) -> bool:
    return bool(re.match(r"^\s*[-–—(+\u2264\u2265\u00b1]?\d", text))


def _first_numeric_cell(row, label_col: int) -> str:
    """Первая ячейка правее метки, начинающаяся с цифры (значение)."""
    for j in range(label_col + 1, len(row)):
        t = row[j].text.strip()
        if t and _starts_with_number(t):
            return t
    return ""


_SECTION_RE = re.compile(r"^\d+(?:\.\d+){0,3}\s*[.)]?\s*[А-ЯA-Z]")
_CODE_RE = re.compile(r"^(?:[А-ЯA-Z]{1,4}|[а-яa-z]{1,4})?\d+[А-ЯA-Zа-яa-z0-9./\-]*$")


def _split_label_unit(label_text: str, units: list[str]) -> tuple[str, str | None]:
    label = label_text.strip()
    m = re.search(r"\(([^()]+)\)\s*$", label)
    if m and m.group(1).strip().lower() in {u.lower() for u in units}:
        return label[: m.start()].strip(), m.group(1).strip()
    # «Масса, кг, не более» / «Давление, МПа, не менее»
    m2 = re.match(r"^(.*?),\s*([^,]+?)\s*,\s*(?:не более|не менее|не выше|не ниже)\s*$", label)
    if m2 and m2.group(2).strip().lower() in {u.lower() for u in units}:
        return m2.group(1).strip(), m2.group(2).strip()
    for unit in sorted(units, key=len, reverse=True):
        u = unit.strip()
        for sep in (", ", " "):
            if label.lower().endswith(sep + u.lower()):
                return label[: -(len(sep) + len(u))].strip(), u
    return label, None


def _match_label(cell_text: str, vocab: set[str]) -> str | None:
    t = normalize_header(cell_text)
    if not t or t in SKIP_LABELS:
        return None
    if t in vocab:
        return t
    for v in vocab:
        if v and len(v) >= 3 and (v in t or t in v):
            return v
    return None


def _is_section_row(row, section_keywords: list[str]) -> str | None:
    filled = [c.text for c in row if c.text.strip()]
    if len(filled) != 1:
        return None
    text = filled[0].strip()
    norm = normalize_header(text)
    if looks_numeric(text):
        return None
    for kw in section_keywords:
        k = normalize_header(kw)
        if k and (k in norm or norm in k):
            return collapse_ws(text)
    if len(text) <= 70 and text.isupper():
        return collapse_ws(text)
    return None


def _junk_label(t: str, units: list[str] | None = None) -> bool:
    """Мусорная «метка»: служебная, заголовок, код, обрывок текста."""
    text = collapse_ws(t)
    nt = normalize_header(text)
    if not nt or nt in SKIP_LABELS:
        return True
    if len(nt) <= 1:
        return True
    if _has_junk_prefix(nt):
        return True
    if any(w in nt for w in ("выбор", "настройк", "адр", "серийн", "соотн", "текущ")):
        return True
    if nt in ("рабочий", "рабочий интервал", "рабочий интрвал"):
        return True
    compact = re.sub(r"\s+", "", text).lower()
    if compact.startswith("таблица") or re.search(r"(приложени|наименовани|оглавлени|содержани|приведен)", text.lower()):
        return True
    if text.endswith("-"):  # обрыв переноса строки
        return True
    if text.replace(" ", "").count(".") >= 3:
        return True
    # заголовки/пункты, оканчивающиеся точкой (кроме «... единица.»)
    if text.endswith("."):
        stripped = text.rstrip(".").rstrip()
        u = list(units or DEFAULT_UNITS)
        if not any(stripped.lower().endswith(x.strip().lower()) for x in u if x.strip()):
            return True
    if _SECTION_RE.match(text):
        return True
    if re.search(r"\d", text):  # в свободном режиме метки-параметры без цифр
        return True
    if not re.search(r"[А-Яа-яA-Za-z]", text):
        return True
    if text[0] in ".,;:—–-•()[]«»\"'":
        return True
    if text[0].islower():  # обрывки/продолжения текста
        return True
    if text.isupper() and text.lower() not in {x.strip().lower() for x in units or DEFAULT_UNITS if x.strip()}:
        return True  # заголовки/шапки капсом (кроме «МПА» и т.п.)
    if text.count("/") >= 2 and not re.search(r"\([^()]*/[^()]*\)", text):
        return True  # многоязычные поля деклараций/заголовков (кроме габаритов «(W/D/H)»)
    if re.search(r"[äöüßÄÖÜéèêàáíóúñç]", text):  # посторонние языки (нем./фр.)
        return True
    if re.search(r"[A-Za-zА-Яа-я]\.[A-Za-zА-Яа-я]", text):  # сокращённые поля меню: Адр.мощ, Част.имя
        return True
    return False


def _looks_like_phone(value_text: str) -> bool:
    vt = collapse_ws(value_text)
    if not vt:
        return False
    clean = vt.rstrip(";.,").replace(",", " ")
    if not re.fullmatch(r"[0-9()\-. ]+", clean):
        return False
    digits = sum(c.isdigit() for c in clean)
    return digits >= 6 and bool(re.search(r"[()\-]", clean))


def _looks_like_zero_padded(value_text: str) -> bool:
    """Значения-«заглушки» контроллера/счётчиков: 0000, 000.0, 001, 0001, 00.63..."""
    s = collapse_ws(value_text).replace(".", "").replace(":", "")
    if not s or not re.fullmatch(r"\d+", s):
        return False
    return len(s) >= 3 and (s.startswith("00") or set(s) == {"0"})


def _looks_like_plain_value(text: str) -> bool:
    """Ячейка-значение: число, диапазон или число+единица (не текст, начинающийся с цифры)."""
    t = collapse_ws(text)
    if not t or not _starts_with_number(t):
        return False
    _, unit = _split_value_unit(t, DEFAULT_UNITS)
    if unit:
        return True
    core = re.sub(r"[0-9\s.,;:+\-–—/()≤≥±%°:·×xхX*…]", "", t)
    return core == ""


def _clean_label(text: str) -> str:
    """Обрезать незакрытую скобку-«хвост» («Рабочее давление (для...» -> «Рабочее давление»)."""
    if text.count("(") > text.count(")"):
        return re.split(r"\s*\(", text, 1)[0].strip()
    return text


def _is_unit_cell(text: str, units: list[str]) -> bool:
    return collapse_ws(text).lower() in {u.strip().lower() for u in units if u.strip()}


def _find_label_value(row) -> tuple[int, str, str | None] | None:
    """Свободный поиск пары «метка -> значение» в строке таблицы.

    Возвращает (колонка метки, значение, единица) либо None.
    Матричные строки (несколько чисел правее метки) игнорируются — это вариант Б.
    """
    texts = [c.text.strip() for c in row]
    for li in range(len(texts)):
        t = _clean_label(texts[li])
        if not t or _junk_label(t, DEFAULT_UNITS):
            continue
        # значения — короткие числовые ячейки; пояснения с цифрой («90° угольник …») не считаем
        value_like = [
            j for j in range(li + 1, len(texts))
            if texts[j] and _looks_like_plain_value(texts[j])
        ]
        if not value_like:
            continue
        # 2+ значений правее метки => строка матрицы (вариант Б)
        if len(value_like) > 1:
            continue
        vi = value_like[0]
        value_text = texts[vi]
        if _looks_like_phone(value_text) or _looks_like_zero_padded(value_text):
            continue
        if re.match(r"^\d{4,6},?\s+[A-ZА-Я]", collapse_ws(value_text)):  # индекс+город и т.п.
            continue
        value, unit = _split_value_unit(value_text, DEFAULT_UNITS)
        if _looks_like_zero_padded(value):
            continue
        # единица может стоять между меткой и значением («Подача | м3/ч | 12,5»)
        if unit is None:
            for j in range(li + 1, vi):
                if texts[j] and _is_unit_cell(texts[j], DEFAULT_UNITS):
                    unit = texts[j]
                    break
        # единица может быть записана в конце метки («Среднее время ..., ч»)
        if unit is None:
            _, label_unit = _split_label_unit(t, DEFAULT_UNITS)
            if label_unit:
                unit = label_unit
        # «Общая масса» в перечнях ЗИП — итоговая строка без единицы, не пара ТХ
        if unit is None and t.lower() == "общая масса":
            continue
        # целые 1..99 без единицы — перечни/запчасти («Screw, Handle: 2»), не ТХ
        if unit is None and re.fullmatch(r"[1-9]\d?", value):
            if not re.search(r"полюс", t.lower()):  # «Число полюсов» — безразмерная ТХ
                continue
        return li, value_text, unit
    return None


def _auto_header_rows(table: Table) -> int:
    """Автоопределённая шапка (детектор, подэтап 2.5) — только там, где ей можно
    доверять: матрицы и merged-таблицы (НС/СМ/VP). В key-value word-таблицах
    граница «шапка → данные» ненадёжна (строка «метка + 1 значение» не
    распознаётся как данные) и может проглотить реальные строки параметров."""
    if table.is_matrix or table.has_merged_cells:
        return table.header_rows
    return 0


def extract_from_table(
    table: Table,
    preset: dict,
    doc_name: str,
    include_all: bool = False,
) -> list[Parameter]:
    cfg = preset.get("columns", {})
    role_cols = dict(cfg.get("roles", {}))
    header_map = cfg.get("header", {})
    has_header = bool(cfg.get("has_header", False))
    units = list(preset.get("units") or DEFAULT_UNITS)
    vocab = {normalize_header(l) for l in preset.get("labels", []) if l.strip()}
    section_keywords = list(preset.get("section_keywords", []))

    col_roles = role_cols
    start_row = 0
    if has_header and header_map:
        for i, row in enumerate(table.rows[:12]):
            mapped = {normalize_header(c.text): j for j, c in enumerate(row) if normalize_header(c.text)}
            matched = {}
            for t, j in mapped.items():
                role = header_map.get(t)
                if role is None:
                    for k, r in header_map.items():
                        if k and (k in t or t in k):
                            role = r
                            break
                if role:
                    matched.setdefault(role, j)
            if matched:
                col_roles = dict(role_cols)
                for role, j in matched.items():
                    col_roles[role] = j
                start_row = i + 1
                table.header_row = i
                break
    # автоопределённая шапка (детектор, подэтап 2.5): строки до данных не сканируем
    if _auto_header_rows(table) > start_row:
        start_row = _auto_header_rows(table)

    label_col = col_roles.get("label", 0)
    value_col = col_roles.get("value", label_col + 1)
    unit_col = col_roles.get("unit")
    section_col = col_roles.get("section")
    allow_all = include_all or bool(preset.get("include_all", False))

    params: list[Parameter] = []
    current_section: str | None = None
    seen_names: set[str] = set()

    for ri, row in enumerate(table.rows):
        if ri < start_row:
            continue
        sec = _is_section_row(row, section_keywords)
        if sec:
            current_section = sec
            continue

        def cell(ci):
            # get_cell раскрывает merged: клетка-продолжение читается как якорная
            # (rowspan-метка действует на все накрытые строки)
            if ci is None:
                return ""
            resolved = table.get_cell(ri, ci)
            return resolved.text.strip() if resolved is not None else ""

        label_text = cell(label_col)
        if not label_text or looks_numeric(label_text):
            continue
        if normalize_header(label_text) in SKIP_LABELS:
            continue
        matched = _match_label(label_text, vocab)
        if not matched and not allow_all:
            continue

        value_text = cell(value_col)
        if not value_text:
            continue
        if not matched and allow_all and not _looks_like_value(value_text, units):
            continue

        if unit_col is not None:
            value, unit = collapse_ws(value_text), cell(unit_col) or None
        else:
            value, unit = _split_value_unit(value_text, units)
        if not value:
            continue

        name, label_unit = _split_label_unit(label_text, units)
        if not name:
            name = label_text
        if unit is None:
            unit = label_unit
        if current_section:
            if name in seen_names:
                name = f"{name} ({current_section})"
            else:
                seen_names.add(name)

        quote = f"{label_text}: {value_text}"
        params.append(
            Parameter(
                name=name,
                value=value,
                unit=unit,
                evidence=Evidence(
                    document=doc_name,
                    page=(table.page + 1) if table.page is not None else None,
                    quote=collapse_ws(quote),
                    table=table.id,
                    row=ri + 1,
                ),
                source="preset",
                verified=True,
            )
        )
    return params


# Таблицы-перечни (ЗИП, дефекты, коды заказа): строки там — не пары «параметр -> значение»
JUNK_TABLE_MARKERS = (
    "критерии предельных состояний",
    "разрушение подшипников",
    "возможные неисправности",
    "методы устранения",
    "код для заказа",
    "консерваци",
)


def _is_junk_table(table: Table) -> bool:
    sample = " ".join(c.text for row in table.rows[:40] for c in row).lower()
    return any(m in sample for m in JUNK_TABLE_MARKERS)



def extract_from_table_free(
    table: Table,
    preset: dict,
    doc_name: str,
) -> list[Parameter]:
    """Вариант А: свободный обход строки — метка в любой ячейке, значение правее."""
    if _is_junk_table(table):
        return []
    units = list(preset.get("units") or DEFAULT_UNITS)
    params: list[Parameter] = []
    # автоопределённая шапка (детектор): строки заголовка не сканируем
    start = _auto_header_rows(table)
    # merged-ячейки НЕ раскрываем: якорь rowspan-блока (например, «1 шт.» —
    # количество на весь блок формы ТЗ) добавляет лишние «значения» правее
    # метки, и строка ошибочно отбрасывается как матричная; grid-таблицы
    # с merged обрабатывает вариант Б (matrix.py)
    for ri in range(start, len(table.rows)):
        row = table.rows[ri]
        found = _find_label_value(row)
        if found is None:
            continue
        li, value_text, unit = found
        label_text = _clean_label(row[li].text.strip())
        value, _u = _split_value_unit(value_text, units)
        if unit is None:
            unit = _u
        if not value:
            continue
        name, label_unit = _split_label_unit(label_text, units)
        if not name:
            name = label_text
        if unit is None:
            unit = label_unit
        quote = f"{label_text}: {value_text}"
        params.append(
            Parameter(
                name=name,
                value=value,
                unit=unit,
                evidence=Evidence(
                    document=doc_name,
                    page=(table.page + 1) if table.page is not None else None,
                    quote=collapse_ws(quote),
                    table=table.id,
                    row=ri + 1,
                ),
                source="preset",
                verified=True,
            )
        )
    return params


def extract_keyvalue(lines: list[str], preset: dict, doc_name: str, include_all: bool = False) -> list[Parameter]:
    vocab = {normalize_header(l) for l in preset.get("labels", []) if l.strip()}
    units = list(preset.get("units") or DEFAULT_UNITS)
    params: list[Parameter] = []
    seen: set[str] = set()
    for ln in lines:
        m = re.match(r"^\s*(.+?)\s*[:–—\-]\s*(.+?)\s*$", ln)
        if not m:
            continue
        label_raw, value_raw = m.group(1).strip(), m.group(2).strip()
        name, label_unit = _split_label_unit(label_raw, units)
        if normalize_header(name) in SKIP_LABELS:
            continue
        matched = _match_label(name, vocab)
        allow_all = include_all or bool(preset.get("include_all", False))
        if not matched and not allow_all:
            continue
        if not value_raw:
            continue
        value, unit = _split_value_unit(value_raw, units)
        if unit is None:
            unit = label_unit
        if not matched and allow_all and not _looks_like_value(value_raw, units):
            continue
        if not value or (name in seen and not include_all):
            continue
        seen.add(name)
        params.append(
            Parameter(
                name=name,
                value=value,
                unit=unit,
                evidence=Evidence(document=doc_name, page=None, quote=collapse_ws(ln)),
                source="preset",
                verified=True,
            )
        )
    return params


def extract_deterministic(
    load_result: LoadResult,
    preset: dict,
    doc_name: str,
    include_all: bool = False,
    table_id: str | None = None,
) -> tuple[list[Parameter], list[str], str | None]:
    warnings: list[str] = []
    if preset.get("keyvalue") and load_result.text_lines:
        return extract_keyvalue(load_result.text_lines, preset, doc_name, include_all), warnings, None
    if not load_result.tables:
        warnings.append("Таблицы не найдены — настройте OCR или используйте LLM")
        return [], warnings, None

    cfg = preset.get("columns") or {}
    col_count = cfg.get("count")
    free = (col_count is None) or bool(cfg.get("free"))

    if table_id:
        target = next((t for t in load_result.tables if t.id == table_id), None)
        if target is None:
            warnings.append(f"Таблица {table_id} не найдена")
            return [], warnings, table_id
        tables = [target]
    elif free:
        tables = list(load_result.tables)
    elif col_count:
        tables = [t for t in load_result.tables if t.n_cols == col_count]
        if not tables:
            warnings.append(
                f"Нет таблиц с {col_count} колонками — настройте маппинг или используйте LLM"
            )
            return [], warnings, None
    else:
        tables = list(load_result.tables)

    params: list[Parameter] = []
    seen: set[tuple[str, str, str]] = set()
    for t in tables:
        if free:
            extracted = extract_from_table_free(t, preset, doc_name)
        else:
            extracted = extract_from_table(t, preset, doc_name, include_all)
        for p in extracted:
            key = (normalize_header(p.name), collapse_ws(p.value).lower(), (p.unit or "").lower())
            if key in seen:
                continue
            seen.add(key)
            params.append(p)
    if free:
        # вариант Б: матрицы (строки-модели × колонки-параметры)
        params.extend(
            extract_matrices(
                tables, doc_name, list(preset.get("units") or DEFAULT_UNITS), dedup=seen,
                page_words=getattr(load_result, "page_words", None),
            )
        )
    return params, warnings, tables[0].id if tables else None
