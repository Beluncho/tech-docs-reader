"""LLM-ассистент маппинга: предложение аннотаций ролей для таблицы (этап 4).

Промпты лежат в app/prompts/ (system + 3 user-шаблона + few-shot примеры (синтетические)),
контракт — в app/prompts/README.md. Вызов LLM переиспользует chat_completions из app.llm:
перебор моделей с фолбэками, режим JSON по настройкам, сетевые ретраи внутри вызова.

Кэш и метрики (4.7): ответ LLM кэшируется по фингерпринту запроса (sha256 от messages —
правка промптов автоматически инвалидирует), счётчики запросов/кэш-хитов/ошибок и
потреблённые токены копятся в storage. Точность — производная от журнала коррекций (4.6).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.llm import LLMError, chat_completions
from app.preset import Table

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

ROLES = ("model", "param", "value", "unit", "section", "condition", "ignore")

# подсказка к типу промпта по (is_matrix, orientation)
_EXAMPLE_BY_TYPE = {
    "matrix_table:rows": "cns_pump.txt",
    "matrix_table:columns": "welch_pump.txt",
    "complex_table": "cm_pump.txt",
    "keyvalue_table": "oprosny_list.txt",
}

# рендер шаблона заменой (не str.format: в шаблонах литеральные фигурные скобки JSON)
_PLACEHOLDERS = ("doc_context", "page", "table_meta", "table_markdown", "merged_cells", "context_hint")

# сжатие больших таблиц: показываем голову + хвост (хвост — чтобы LLM видел, чем таблица
# заканчивается: футеры/примечания), середину заменяем маркером пропуска
MAX_ROWS = 40
TAIL_ROWS = 3
# бюджет символов на строку markdown: широкие таблицы обрезают ячейки сильнее
# (22 колонки → ~32 симв. на ячейку; ≤12 колонок — полный лимит 60)
ROW_CHAR_BUDGET = 720


def render_template(template: str, **ctx: str) -> str:
    out = template
    for name in _PLACEHOLDERS:
        out = out.replace("{" + name + "}", ctx.get(name, ""))
    return out


def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def parse_example(text: str) -> dict[str, str]:
    """Разобрать few-shot файл на пару user/assistant."""
    if "=== USER ===" not in text or "=== ASSISTANT ===" not in text:
        raise ValueError("пример без секций === USER === / === ASSISTANT ===")
    _, rest = text.split("=== USER ===", 1)
    user, assistant = rest.split("=== ASSISTANT ===", 1)
    return {"user": user.strip(), "assistant": assistant.strip()}


def load_prompts() -> dict:
    """Шаблоны промптов + few-shot примеры (по типу таблицы)."""
    examples = {
        key: parse_example(_read(f"examples/{name}"))
        for key, name in _EXAMPLE_BY_TYPE.items()
    }
    return {
        "system": _read("system.txt"),
        "matrix_table": _read("matrix_table.txt"),
        "keyvalue_table": _read("keyvalue_table.txt"),
        "complex_table": _read("complex_table.txt"),
        "examples": examples,
    }


def detect_prompt_type(table: Table) -> str:
    """Выбор user-шаблона по структуре таблицы."""
    if table.is_matrix:
        return "matrix_table"
    if table.has_merged_cells or (table.header_rows or 0) > 1:
        return "complex_table"
    return "keyvalue_table"


def build_doc_context(doc_name: str, doc_type: str = "") -> str:
    """Строка контекста документа для user-промпта: имя (без расширения) + тип.

    Пример: «НС 13-38-60 ред19 04 2024 (45) (паспорт насоса)». Пустое всё → «документ».
    """
    name = (doc_name or "").strip()
    stem, dot, ext = name.rpartition(".")
    if dot and stem.strip() and 1 <= len(ext) <= 5 and not ext.strip().isdigit():
        name = stem.strip()
    doc_type = (doc_type or "").strip()
    if doc_type:
        return f"{name} ({doc_type})" if name else doc_type
    return name or "документ"


def _cell_lines(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def _shown_rows(n_rows: int, max_rows: int, tail_rows: int) -> tuple[list[int], tuple[int, int] | None]:
    """Какие строки таблицы показывать LLM.

    Все, если влезает в лимит; иначе голова (max_rows - tail_rows) + хвост (tail_rows),
    skipped — полуинтервал [a, b) скрытых строк для маркера пропуска.
    """
    if n_rows <= max_rows:
        return list(range(n_rows)), None
    tail_rows = min(tail_rows, max(1, max_rows - 1))
    head = max(max_rows - tail_rows, 1)
    skipped = (head, n_rows - tail_rows)
    return list(range(head)) + list(range(n_rows - tail_rows, n_rows)), skipped


def format_table_as_markdown(
    table: Table, max_rows: int = MAX_ROWS, tail_rows: int = TAIL_ROWS, max_chars: int = 60
) -> str:
    """Markdown-таблица по контракту system.txt: колонка # с номерами строк, продолжения «·».

    Большие таблицы сжимаются: голова + хвост, середина — маркер «... (пропущены строки) ...»;
    координаты сквозные. Лимит обрезки ячеек сжимается для широких таблиц (ROW_CHAR_BUDGET).
    """
    n_cols = table.grid_cols
    if n_cols == 0:
        return "(таблица пуста)"
    eff_chars = max(24, min(max_chars, ROW_CHAR_BUDGET // n_cols))
    lines = ["| # | " + " | ".join(str(j) for j in range(n_cols)) + " |"]
    lines.append("|---" * (n_cols + 1) + "|")
    shown, skipped = _shown_rows(table.grid_rows, max_rows, tail_rows)
    for i in shown:
        cells = [str(i)]
        for j in range(n_cols):
            cell = table.rows[i][j] if i < len(table.rows) and j < len(table.rows[i]) else None
            if cell is None:
                cells.append("")
            elif cell.is_merged:
                cells.append("·")
            else:
                cells.append(_cell_lines(cell.text, eff_chars))
        lines.append("| " + " | ".join(cells) + " |")
        if skipped and i == skipped[0] - 1:
            a, b = skipped
            lines.append(f"... (пропущены строки {a}–{b - 1} из {table.grid_rows}: продолжение той же структуры) ...")
    return "\n".join(lines)


def format_merged_cells(table: Table, max_rows: int = MAX_ROWS, tail_rows: int = TAIL_ROWS) -> str:
    """Список якорей merged-блоков в показанном диапазоне строк (см. _shown_rows)."""
    shown, _skipped = _shown_rows(table.grid_rows, max_rows, tail_rows)
    shown_set = set(shown)
    out = []
    for row in table.rows:
        for cell in row:
            if cell.is_merged or cell.row not in shown_set:
                continue
            if cell.rowspan > 1 or cell.colspan > 1:
                out.append(f"({cell.row}, {cell.col}) {cell.rowspan}x{cell.colspan} — «{_cell_lines(cell.text, 60)}»")
    if not out:
        return "нет"
    return "\n".join(out)


def parse_llm_response(content: str) -> dict:
    """JSON из ответа LLM: снять markdown-обёртку, вырезать прозу вокруг.
    Очистка/валидация аннотаций — _sanitize_annotations + validate_annotations."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"невалидный JSON от LLM: {text[:120]!r}")
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise LLMError(f"невалидный JSON от LLM: {text[:120]!r}")
    if not isinstance(data, dict) or "annotations" not in data:
        raise LLMError("в ответе LLM нет ключа 'annotations'")
    return data


def _sanitize_annotations(raw: list) -> list[dict]:
    """Минимальная очистка: только dict-ы с известной ролью и координатами row/col либо range.
    Дальше — validate_annotations (развёртка range по якорям, границы сетки)."""
    out = []
    for ann in raw:
        if not isinstance(ann, dict):
            continue
        role = ann.get("role")
        if role not in ROLES:
            continue
        has_cell = isinstance(ann.get("row"), int) and isinstance(ann.get("col"), int)
        rng = ann.get("range")
        has_range = (
            isinstance(rng, list) and len(rng) == 4 and all(isinstance(x, int) for x in rng)
        )
        if not has_cell and not has_range:
            continue
        clean = {"role": role}
        if has_range and not has_cell:
            clean["range"] = rng
        else:
            clean["row"], clean["col"] = ann["row"], ann["col"]
        for key in ("label", "unit", "group", "reasoning"):
            if isinstance(ann.get(key), str) and ann[key].strip():
                clean[key] = ann[key].strip()
        try:
            conf = float(ann.get("confidence", 0.5))
        except (TypeError, ValueError):
            conf = 0.5
        clean["confidence"] = min(1.0, max(0.0, conf))
        alts = ann.get("alternatives")
        if isinstance(alts, list) and alts:
            clean_alts = []
            for alt in alts:
                if isinstance(alt, dict) and alt.get("role") in ROLES:
                    try:
                        c = min(1.0, max(0.0, float(alt.get("confidence", 0.0))))
                    except (TypeError, ValueError):
                        c = 0.0
                    clean_alts.append({"role": alt["role"], "confidence": c})
            if clean_alts:
                clean["alternatives"] = clean_alts
        out.append(clean)
    return out


def compute_summary(annotations: list[dict], threshold: float = 0.7) -> dict:
    high = sum(1 for a in annotations if a.get("confidence", 0.5) >= threshold)
    return {
        "total": len(annotations),
        "high_confidence": high,
        "low_confidence": len(annotations) - high,
    }


def messages_fingerprint(messages: list[dict]) -> str:
    """Фингерпринт LLM-запроса: sha256 от склеенных сообщений.

    Внутри messages — таблица (markdown + merged-список), контекст документа и
    сами промпты, поэтому правка system.txt/шаблонов/примеров автоматически
    меняет ключ и кэш инвалидируется — версионировать промпты вручную не нужно.
    """
    blob = "\x1e".join(m.get("role", "") + "\x1f" + m.get("content", "") for m in messages)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _usage_tokens(data: dict) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) из usage-блока ответа; 0 при отсутствии."""
    usage = data.get("usage") or {}
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    return (pt if isinstance(pt, int) else 0, ct if isinstance(ct, int) else 0)


def compute_accuracy(feedback: list[dict]) -> dict | None:
    """Точность LLM-маппинга по журналу коррекций (4.6 → 4.7).

    Коррекция без user_role (пользователь снял роль) — тоже ошибка LLM.
    Панель спорных может приводить к двойному логу одной клетки (чип + подтверждение):
    уникализируем по (document_id, table_id, row, col), берём последнюю запись.
    """
    latest: dict[tuple[str, str, int, int], dict] = {}
    for entry in feedback:
        try:
            key = (entry["document_id"], entry["table_id"], int(entry["row"]), int(entry["col"]))
        except (KeyError, TypeError, ValueError):
            continue
        latest[key] = entry
    if not latest:
        return None
    total = len(latest)
    agreed = sum(1 for e in latest.values() if e.get("user_role") == e.get("llm_role"))
    return {
        "corrected_cells": total,
        "agreed": agreed,
        "accuracy": round(agreed / total, 4),
    }


# допустимые значения enum-полей ответа (схема system.txt)
_ORIENTATIONS = {"rows", "columns", "none"}
_STRUCTURES = {"matrix", "keyvalue", "list", "other"}
_COMPLEXITIES = {"low", "medium", "high"}


def _clean_enum(value: object, allowed: set[str]) -> str | None:
    if isinstance(value, str) and value.strip().lower() in allowed:
        return value.strip().lower()
    return None


def expand_range(table: Table, rng: list[int]) -> list[tuple[int, int]]:
    """Якоря блоков в прямоугольнике range (контракт system.txt: роль получают блоки,
    ЯКОРЬ которых попадает в прямоугольник). Диапазон клампится к сетке."""
    r0, c0, r1, c1 = rng
    r0, r1 = sorted((r0, r1))
    c0, c1 = sorted((c0, c1))
    r0, c0 = max(0, r0), max(0, c0)
    r1, c1 = min(r1, table.grid_rows - 1), min(c1, table.grid_cols - 1)
    if r0 > r1 or c0 > c1:
        return []
    out: dict[tuple[int, int], None] = {}  # ordered set
    for i in range(r0, r1 + 1):
        for j in range(c0, c1 + 1):
            cell = table.get_cell(i, j)
            if cell is None:
                continue  # дыра/битая ссылка merged
            if r0 <= cell.row <= r1 and c0 <= cell.col <= c1:
                out.setdefault((cell.row, cell.col), None)
    return list(out)


def validate_annotations(table: Table, annotations: list[dict]) -> list[dict]:
    """Сверка аннотаций с реальной сеткой таблицы (этап 4.4).

    - range-зоны разворачиваются в по-якорные аннотации (expand_range);
    - точечные координаты нормализуются к якорям merged (get_cell) и проверяются
      на границы сетки — несуществующие слоты отбрасываются;
    - при конфликте на якоре точечная аннотация побеждает развёрнутую из range
      (LLM явно уточнил клетку), внутри одного вида — последняя.
    """
    expanded: dict[tuple[int, int], dict] = {}
    explicit: dict[tuple[int, int], dict] = {}
    for ann in annotations:
        base = {k: v for k, v in ann.items() if k not in ("row", "col", "range")}
        if "range" in ann:
            for r, c in expand_range(table, ann["range"]):
                expanded[(r, c)] = {**base, "row": r, "col": c}
        else:
            cell = table.get_cell(ann["row"], ann["col"])
            if cell is None:
                continue
            explicit[(cell.row, cell.col)] = {**base, "row": cell.row, "col": cell.col}
    return list({**expanded, **explicit}.values())


class LLMMapperService:
    """Предложение маппинга таблицы через LLM (промпты этапа 4.1).

    Кэш (4.7): до CACHE_MAX_ITEMS последних ответов по фингерпринту запроса —
    повторное предложение той же таблицы не ходит в облако. refresh=True —
    принудительный обход кэша (после правки промптов/для сравнения ответов).
    Хранилище (storage) передаётся снаружи — синглтон из app.api.deps; без него
    сервис работает без кэша и метрик (удобно для тестов сервиса).
    """

    CACHE_MAX_ITEMS = 200

    def __init__(
        self,
        settings: Settings,
        prompts: dict | None = None,
        n_examples: int = 1,
        storage=None,  # Storage-синглтон (app.api.deps.storage); None — без кэша/метрик
    ):
        self.settings = settings
        self.prompts = prompts or load_prompts()
        self.n_examples = n_examples
        self.storage = storage

    def build_messages(self, table: Table, doc_name: str, context: dict | None) -> list[dict]:
        prompt_type = detect_prompt_type(table)
        ctx = context or {}
        keywords = [str(k) for k in ctx.get("keywords", []) if str(k).strip()]
        hint = ""
        if keywords:
            hint = "Контекст: ключевые слова: " + ", ".join(keywords[:20]) + ".\n"
        user_prompt = render_template(
            self.prompts[prompt_type],
            doc_context=build_doc_context(doc_name, str(ctx.get("doc_type") or "")),
            page=str((table.page + 1) if table.page is not None else "?"),
            table_meta=(
                f"{table.grid_rows} строк × {table.grid_cols} колонок, "
                f"шапка: {table.header_rows or 0} строк(и), "
                f"merged cells: {'да' if table.has_merged_cells else 'нет'}, "
                f"матрица: {table.orientation if table.is_matrix else 'нет'}"
            ),
            table_markdown=format_table_as_markdown(table),
            merged_cells=format_merged_cells(table),
            context_hint=hint,
        )
        messages: list[dict] = [{"role": "system", "content": self.prompts["system"]}]
        if self.n_examples > 0:
            example_key = (
                f"matrix_table:{table.orientation or 'rows'}"
                if prompt_type == "matrix_table"
                else prompt_type
            )
            example = self.prompts["examples"].get(example_key)
            if example:
                messages.append({"role": "user", "content": example["user"]})
                messages.append({"role": "assistant", "content": example["assistant"]})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    def suggest_mapping(
        self, table: Table, doc_name: str = "", context: dict | None = None, refresh: bool = False
    ) -> dict:
        """Вернуть предложение маппинга. LLMError — если все модели не ответили валидно.

        Кэшируется по фингерпринту запроса (сообщения целиком — таблица + промпты +
        контекст). Попадание отдаёт сохранённый ответ с processing_time_ms ≈ 0
        и «cached: true»; метрика попадания учитывается только при живом запросе.
        """
        if not self.settings.llm_enabled:
            raise LLMError("LLM не настроен: укажите YANDEXGPT_API_KEY (или LLM_API_KEY)")
        messages = self.build_messages(table, doc_name, context)
        cache_key = messages_fingerprint(messages)

        if not refresh and self.storage is not None:
            cached = self.storage.get_llm_suggest_cache(cache_key)
            if cached is not None:
                self.storage.log_llm_stats(cache_hit=True, ok=True, processing_ms=0)
                return {**cached, "cached": True, "processing_time_ms": 0}

        models = [self.settings.yandexgpt_rule_model] + [
            m for m in self.settings.yandexgpt_rule_models if m != self.settings.yandexgpt_rule_model
        ]
        errors: list[str] = []
        started = time.time()
        for model in models:
            try:
                data = chat_completions(
                    self.settings,
                    model,
                    messages,
                    temperature=0.1,
                    max_tokens=4000,
                )
                content = data["choices"][0]["message"]["content"]
                mapping = parse_llm_response(content)
                annotations = validate_annotations(
                    table, _sanitize_annotations(mapping["annotations"])
                )
                if mapping["annotations"] and not annotations:
                    # LLM что-то предложил, но всё вне сетки — считаем ответ невалидным
                    # и берём следующую модель (фолбэк)
                    raise LLMError("все аннотации вне сетки таблицы")
                prompt_tokens, completion_tokens = _usage_tokens(data)
                result = {
                    "orientation": _clean_enum(mapping.get("orientation"), _ORIENTATIONS),
                    "structure": _clean_enum(mapping.get("structure"), _STRUCTURES),
                    "complexity": _clean_enum(mapping.get("complexity"), _COMPLEXITIES),
                    "annotations": annotations,
                    "notes": (mapping.get("notes") or None) if isinstance(mapping.get("notes"), str) else None,
                    "summary": compute_summary(annotations),
                    "model": model,
                    "processing_time_ms": int((time.time() - started) * 1000),
                    "cached": False,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                if self.storage is not None:
                    self.storage.save_llm_suggest_cache(cache_key, result)
                    self.storage.log_llm_stats(
                        cache_hit=False,
                        ok=True,
                        processing_ms=result["processing_time_ms"],
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                return result
            except (httpx.HTTPError, KeyError, IndexError, TypeError, LLMError) as exc:
                detail = str(exc)
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    snippet = " ".join(exc.response.text.split())[:300]
                    if snippet:
                        detail = f"{detail} | {snippet}"
                errors.append(f"{model}: {detail}")
        if self.storage is not None:
            self.storage.log_llm_stats(
                cache_hit=False, ok=False, processing_ms=int((time.time() - started) * 1000)
            )
        raise LLMError("; ".join(errors))
