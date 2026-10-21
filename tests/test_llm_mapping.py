"""LLM-ассистент маппинга (этап 4.2): промпты, форматирование, парсинг, API."""
from __future__ import annotations

import io
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm_mapping
from app.api.deps import settings, storage
from app.config import Settings
from app.llm import LLMError
from app.llm_mapping import (
    LLMMapperService,
    build_doc_context,
    compute_accuracy,
    compute_summary,
    detect_prompt_type,
    expand_range,
    format_merged_cells,
    format_table_as_markdown,
    load_prompts,
    messages_fingerprint,
    parse_example,
    parse_llm_response,
    render_template,
    _sanitize_annotations,
    _shown_rows,
    validate_annotations,
)
from app.main import app
from app.preset import Cell, Table

client = TestClient(app)


def _mk_table(rows_spec: list[list[str]], header_rows: int = 1, **kw) -> Table:
    rows = [
        [Cell(text=t, row=i, col=j) for j, t in enumerate(spec)]
        for i, spec in enumerate(rows_spec)
    ]
    return Table(id="t1", rows=rows, header_rows=header_rows, **kw)


# ---------- промпты ----------

def test_load_prompts_structure():
    prompts = load_prompts()
    for key in ("system", "matrix_table", "keyvalue_table", "complex_table", "examples"):
        assert prompts[key], f"нет {key}"
    assert set(prompts["examples"]) == {
        "matrix_table:rows",
        "matrix_table:columns",
        "complex_table",
        "keyvalue_table",
    }
    for ex in prompts["examples"].values():
        assert ex["user"].startswith("Документ:") and ex["assistant"].startswith("{")


def test_parse_example_errors():
    with pytest.raises(ValueError):
        parse_example("нет секций")


# ---------- тип промпта ----------

def test_detect_prompt_type():
    matrix = _mk_table([["Модель", "Подача"], ["НС 13-70", "13"]], is_matrix=True, orientation="rows")
    assert detect_prompt_type(matrix) == "matrix_table"
    merged = _mk_table([["A", "B"], ["x", "y"]], header_rows=0)
    merged.rows[0][0].colspan = 2
    merged.rows[1][0].is_merged = True
    merged.rows[1][1].is_merged = True
    merged.has_merged_cells = True
    assert detect_prompt_type(merged) == "complex_table"
    plain = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    assert detect_prompt_type(plain) == "keyvalue_table"


# ---------- форматирование таблицы ----------

def _mk_merged_table() -> Table:
    t = _mk_table([
        ["Модель", "Подача, м3/ч", "Напор, м"],
        ["НС 13-70", "13", "70"],
        ["НС 13-105", "13", "105"],
    ])
    # подача rowspan=2: якорь (1,1), продолжение (2,1)
    t.rows[1][1].rowspan = 2
    t.rows[2][1].is_merged = True
    t.rows[2][1].merged_with = (1, 1)
    return t


def test_format_table_markdown_contract():
    md = format_table_as_markdown(_mk_merged_table())
    lines = md.split("\n")
    assert lines[0] == "| # | 0 | 1 | 2 |"
    assert lines[1] == "|---|---|---|---|"
    assert lines[2].startswith("| 0 | Модель |")
    # продолжение merged-блока — «·», якорь несёт текст
    assert "| 1 | НС 13-70 | 13 | 70 |" in md
    row2 = [ln for ln in lines if ln.startswith("| 2 |")][0]
    cells2 = [c.strip() for c in row2.split("|")]
    assert cells2[3] == "·"  # col 1 — продолжение блока «13»


def test_format_table_truncates_and_limits_rows():
    long_text = "очень длинный текст ячейки " * 10
    spec = [[f"метка{i}", long_text] for i in range(30)]
    t = _mk_table(spec, header_rows=0)
    md = format_table_as_markdown(t, max_rows=10, max_chars=40)
    assert "…" in md
    for line in md.split("\n"):
        assert len(line) < 200


def test_format_table_head_tail_compression():
    """Большие таблицы: голова + хвост, середина — маркер; координаты сквозные."""
    t = _mk_table([[f"метка{i}", str(i)] for i in range(50)], header_rows=0)
    md = format_table_as_markdown(t, max_rows=10, tail_rows=3)
    assert "| 6 | метка6 | 6 |" in md          # голова 0..6 (10 - 3 хвоста)
    assert "| 47 | метка47 | 47 |" in md       # хвост 47..49
    assert "| 49 | метка49 | 49 |" in md
    assert "| 7 |" not in md and "| 46 |" not in md
    assert "... (пропущены строки 7–46 из 50: продолжение той же структуры) ..." in md
    # маркер между головой и хвостом
    lines = md.split("\n")
    assert lines.index("| 6 | метка6 | 6 |") < lines.index("... (пропущены строки 7–46 из 50: продолжение той же структуры) ...") < lines.index("| 47 | метка47 | 47 |")


def test_format_table_no_compression_on_exact_fit():
    t = _mk_table([[f"м{i}", str(i)] for i in range(10)], header_rows=0)
    md = format_table_as_markdown(t, max_rows=10, tail_rows=3)
    assert "пропущены" not in md
    assert "| 9 | м9 | 9 |" in md


def test_format_table_wide_table_shrinks_cell_budget():
    """Узкий бюджет на широких таблицах: 20 колонок → ~36 симв. на ячейку."""
    long_text = "т" * 200
    spec = [[long_text] + ["x"] * 19 for _ in range(3)]
    t = _mk_table(spec, header_rows=0)
    md = format_table_as_markdown(t, max_rows=10)  # max_chars по умолчанию 60
    cell = md.split("\n")[2].split("|")[2].strip()
    assert len(cell) == 36 and cell.endswith("…")
    # узкая таблица — полный лимит
    narrow = _mk_table([[long_text, "x"] for _ in range(3)], header_rows=0)
    cell2 = format_table_as_markdown(narrow, max_rows=10).split("\n")[2].split("|")[2].strip()
    assert len(cell2) == 60 and cell2.endswith("…")


def test_shown_rows_edges():
    assert _shown_rows(5, 10, 3) == (list(range(5)), None)
    shown, skipped = _shown_rows(50, 10, 3)
    assert shown == list(range(7)) + list(range(47, 50)) and skipped == (7, 47)
    # одна строка сверх лимита — в скрытом диапазоне одна строка
    _, skipped = _shown_rows(41, 40, 3)
    assert skipped == (37, 38)
    # вырожденный лимит: хвост не больше лимита, головы хватает хотя бы на строку
    shown, skipped = _shown_rows(50, 2, 3)
    assert shown[0] == 0 and skipped[0] == 1 and len(shown) <= 4


def test_format_table_empty():
    t = _mk_table([["", ""]], header_rows=0)
    t.grid_cols = 2
    assert "| # | 0 | 1 |" in format_table_as_markdown(t)


def test_format_merged_cells_list():
    out = format_merged_cells(_mk_merged_table())
    assert out.startswith("(1, 1) 2x1 — «13»")
    assert format_merged_cells(_mk_table([["a", "b"]], header_rows=0)) == "нет"


def test_format_merged_cells_hidden_anchor_excluded():
    """При сжатии большой таблицы в списке merged — только якоря показанных строк."""
    t = _mk_table([[f"м{i}", str(i)] for i in range(20)], header_rows=0)
    # якорь в голове (0), в скрытой середине (10) и в хвосте (18)
    for r, span in ((0, 2), (10, 2), (18, 2)):
        t.rows[r][1].rowspan = span
        for k in range(1, span):
            t.rows[r + k][1].is_merged = True
            t.rows[r + k][1].merged_with = (r, 1)
    out = format_merged_cells(t, max_rows=10, tail_rows=2)
    assert "(0, 1) 2x1" in out and "(18, 1) 2x1" in out
    assert "(10, 1) 2x1" not in out


def test_format_merged_cells_truncation_unified():
    """Обрезка якорей merged — тем же лимитом 60, что и ячейки таблицы."""
    t = _mk_table([["м", "д" * 100], ["x", "y"]], header_rows=0)
    t.rows[0][1].rowspan = 2
    t.rows[1][1].is_merged = True
    t.rows[1][1].merged_with = (0, 1)
    out = format_merged_cells(t)
    assert f"(0, 1) 2x1 — «{'д' * 59}…»" in out


# ---------- контекст документа ----------

def test_build_doc_context():
    assert build_doc_context("НС 13-38-60.pdf", "паспорт насоса") == "НС 13-38-60 (паспорт насоса)"
    assert build_doc_context("СМ (2).xlsx", "") == "СМ (2)"
    # расширение-цифры не считаем расширением
    assert build_doc_context("версия 2.2024", "") == "версия 2.2024"
    assert build_doc_context("", "паспорт") == "паспорт"
    assert build_doc_context("", "") == "документ"
    assert build_doc_context("  ", None) == "документ"


# ---------- рендер шаблона ----------

def test_render_template_replace_semantics():
    tpl = "Документ: {doc_context}\nСхема: {\"a\": 1}\n{page}"
    out = render_template(tpl, doc_context="паспорт", page="6")
    assert out == 'Документ: паспорт\nСхема: {"a": 1}\n6'


# ---------- парсинг ответа ----------

def test_parse_llm_response_variants():
    good = {"orientation": "rows", "annotations": [{"role": "model", "row": 1, "col": 0}]}
    assert parse_llm_response(json.dumps(good))["orientation"] == "rows"
    assert parse_llm_response("```json\n" + json.dumps(good) + "\n```") == good
    assert parse_llm_response("Вот ответ:\n" + json.dumps(good) + "\nСпасибо.") == good
    with pytest.raises(LLMError):
        parse_llm_response("совсем не JSON")
    with pytest.raises(LLMError):
        parse_llm_response('{"no_annotations": []}')
    with pytest.raises(LLMError):
        parse_llm_response('[1, 2, 3]')


def test_sanitize_annotations():
    out = _sanitize_annotations([
        {"role": "model", "row": 1, "col": 0, "confidence": 0.9},
        {"role": "хлам", "row": 2, "col": 0},                      # неизвестная роль
        {"role": "value", "range": [1, 1, 5, 3]},                  # range-форма
        {"role": "param", "row": "1", "col": 0},                   # не-int координаты
        {"role": "value", "row": 3, "col": 2, "confidence": 7},    # confidence вне [0,1]
        "не словарь",
        {"role": "condition", "row": 4, "col": 0,
         "alternatives": [{"role": "ignore", "confidence": 0.3}, {"role": "???"}]},
    ])
    roles = [a["role"] for a in out]
    assert roles == ["model", "value", "value", "condition"]
    assert out[0]["confidence"] == 0.9
    assert out[1]["range"] == [1, 1, 5, 3] and "row" not in out[1]
    assert out[2]["confidence"] == 1.0
    assert out[3]["alternatives"] == [{"role": "ignore", "confidence": 0.3}]


def test_compute_summary():
    anns = [{"confidence": 0.9}, {"confidence": 0.7}, {"confidence": 0.5}, {}]
    assert compute_summary(anns) == {"total": 4, "high_confidence": 2, "low_confidence": 2}


# ---------- валидация против сетки таблицы (4.4) ----------

def test_expand_range_anchor_semantics():
    """Range — по якорям: продолжения резолвятся в якорь, дубли схлопываются,
    блок с якорем вне прямоугольника не входит."""
    t = _mk_merged_table()
    assert expand_range(t, [1, 1, 2, 1]) == [(1, 1)]            # продолжение → якорь, дедуп
    assert expand_range(t, [0, 0, 2, 2]) == [
        (0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2), (2, 0), (2, 2),
    ]
    assert expand_range(t, [2, 1, 2, 1]) == []                  # якорь (1,1) вне прямоугольника
    assert expand_range(t, [0, 0, 99, 99]) == expand_range(t, [0, 0, 2, 2])  # кламп к сетке
    assert expand_range(t, [5, 0, 6, 1]) == []                  # целиком вне сетки


def test_validate_annotations():
    t = _mk_merged_table()
    out = validate_annotations(t, [
        {"role": "value", "range": [1, 1, 2, 1], "confidence": 0.9},   # → якорь (1,1)
        {"role": "param", "row": 2, "col": 1, "label": "Подача"},      # продолжение → якорь (1,1), точечная побеждает
        {"role": "model", "row": 9, "col": 0},                         # вне сетки — отброшено
        {"role": "model", "row": 0, "col": 0},                         # last-wins внутри вида
        {"role": "section", "row": 0, "col": 0},
    ])
    by_anchor = {(a["row"], a["col"]): a for a in out}
    assert set(by_anchor) == {(0, 0), (1, 1)}
    assert by_anchor[(0, 0)]["role"] == "section"
    assert by_anchor[(1, 1)]["role"] == "param" and by_anchor[(1, 1)]["label"] == "Подача"
    assert all("range" not in a for a in out)


def test_validate_annotations_empty_range_dropped():
    t = _mk_merged_table()
    assert validate_annotations(t, [{"role": "value", "range": [9, 9, 9, 9]}]) == []


# ---------- сервис ----------

def _settings() -> Settings:
    return Settings(yandexgpt_api_key="test-key")


def _ok_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


GOOD_ANSWER = """
{
  "orientation": "rows",
  "structure": "matrix",
  "complexity": "low",
  "annotations": [
    {"role": "model", "range": [1, 0, 3, 0], "confidence": 0.98},
    {"role": "param", "row": 0, "col": 1, "label": "Подача", "unit": "м3/ч", "confidence": 0.95},
    {"role": "value", "range": [1, 1, 3, 2], "confidence": 0.9}
  ],
  "notes": "всё чисто"
}
"""


def test_build_messages_with_example():
    svc = LLMMapperService(_settings())
    table = _mk_table(
        [["Модель", "Подача, м3/ч"], ["НС 13-70", "13"], ["НС 13-105", "13"]],
        is_matrix=True, orientation="rows",
    )
    msgs = svc.build_messages(table, "Паспорт НС", {"doc_type": "passport", "keywords": ["НС"]})
    assert msgs[0]["role"] == "system" and msgs[0]["content"].startswith("Ты — эксперт")
    # few-shot пример по типу: matrix rows → cns_pump
    assert msgs[1]["role"] == "user" and "НС 13-70" in msgs[1]["content"]
    assert msgs[2]["role"] == "assistant" and '"orientation"' in msgs[2]["content"]
    user = msgs[3]["content"]
    # doc_context: имя + тип; тип документа больше не в context_hint
    assert user.startswith("Документ: Паспорт НС (passport)")
    assert "тип документа" not in user
    assert "Страница:" in user and "| # | 0 | 1 |" in user
    assert "ключевые слова: НС" in user


def test_build_messages_no_example_and_no_context():
    svc = LLMMapperService(_settings(), n_examples=0)
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    msgs = svc.build_messages(table, "", None)
    assert len(msgs) == 2
    assert msgs[1]["content"].startswith("Документ: документ")  # doc_name пуст → дефолт
    assert "Контекст:" not in msgs[1]["content"]


def test_suggest_mapping_success(monkeypatch):
    svc = LLMMapperService(_settings())
    table = _mk_table([["Модель", "Подача"], ["НС 13-70", "13"]], is_matrix=True, orientation="rows")
    calls = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append((model, temperature, max_tokens))
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    result = svc.suggest_mapping(table, "паспорт", {"doc_type": "passport"})
    assert result["orientation"] == "rows"
    # range-зоны развёрнуты по якорям сетки 2×2 (кламп к границам)
    assert {(a["row"], a["col"], a["role"]) for a in result["annotations"]} == {
        (1, 0, "model"), (0, 1, "param"), (1, 1, "value"),
    }
    assert all("range" not in a for a in result["annotations"])
    assert result["summary"] == {"total": 3, "high_confidence": 3, "low_confidence": 0}
    assert result["notes"] == "всё чисто"
    assert result["model"] == settings.yandexgpt_rule_model
    assert result["processing_time_ms"] >= 0
    assert calls[0][1] == 0.1 and calls[0][2] == 4000


def test_suggest_mapping_enum_cleaning(monkeypatch):
    """enum-поля ответа нормализуются: регистр/пробелы, мусор → None."""
    svc = LLMMapperService(_settings())
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    answer = GOOD_ANSWER.replace('"orientation": "rows"', '"orientation": "Rows"')
    answer = answer.replace('"structure": "matrix"', '"structure": "матрица"')
    answer = answer.replace('"complexity": "low"', '"complexity": "HIGH"')

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        return _ok_response(answer)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    result = svc.suggest_mapping(table)
    assert result["orientation"] == "rows"
    assert result["structure"] is None
    assert result["complexity"] == "high"


def test_suggest_mapping_out_of_grid_falls_back(monkeypatch):
    """Ответ, где все аннотации вне сетки, — невалидный: пробуем следующую модель."""
    svc = LLMMapperService(_settings())
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    bad = '{"orientation": "rows", "annotations": [{"role": "model", "row": 100, "col": 0}]}'
    models_seen = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        models_seen.append(model)
        return _ok_response(bad if len(models_seen) == 1 else GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    result = svc.suggest_mapping(table)
    assert len(models_seen) == 2
    assert result["model"] == settings.yandexgpt_rule_models[1]
    assert result["annotations"]


def test_suggest_mapping_empty_answer_is_valid(monkeypatch):
    """Пустой список аннотаций — легитимный ответ (мусорная таблица), не фолбэк."""
    svc = LLMMapperService(_settings())
    table = _mk_table([["", ""]], header_rows=0)
    empty = '{"orientation": "none", "annotations": []}'

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        return _ok_response(empty)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    result = svc.suggest_mapping(table)
    assert result["annotations"] == [] and result["summary"]["total"] == 0


def test_suggest_mapping_model_fallback(monkeypatch):
    svc = LLMMapperService(_settings())
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        if model == settings.yandexgpt_rule_model:
            raise httpx.ConnectError("boom")
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    result = svc.suggest_mapping(table)
    assert result["model"] == settings.yandexgpt_rule_models[1]


def test_suggest_mapping_all_models_fail(monkeypatch):
    svc = LLMMapperService(_settings())
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    with pytest.raises(LLMError):
        svc.suggest_mapping(table)


def test_suggest_mapping_disabled_llm():
    svc = LLMMapperService(Settings())  # без ключа
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    with pytest.raises(LLMError, match="не настроен"):
        svc.suggest_mapping(table)


# ---------- API ----------

def _upload_table_doc():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Метка", "Значение"])
    ws.append(["Подача, м3/ч", "13"])
    ws.append(["Напор, м", "70"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/documents",
        files={"file": ("kv.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    return r.json()


def test_api_suggest_mapping(monkeypatch):
    doc = _upload_table_doc()
    table_id = doc["tables"][0]["id"]

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(settings, "yandexgpt_api_key", "test-key")
    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    r = client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"],
        "table_id": table_id,
        "context": {"doc_type": "passport", "keywords": ["насос"]},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # таблица 3×2: range-зоны GOOD_ANSWER клампятся и разворачиваются по якорям
    assert body["summary"]["total"] == 5
    got = {(a["row"], a["col"], a["role"]) for a in body["annotations"]}
    assert got == {(1, 0, "model"), (2, 0, "model"), (0, 1, "param"),
                   (1, 1, "value"), (2, 1, "value")}
    assert all(a.get("range") is None for a in body["annotations"])
    assert body["model"] and body["processing_time_ms"] >= 0


def test_api_suggest_mapping_errors():
    doc = _upload_table_doc()
    # 404: документ
    assert client.post("/api/llm/suggest-mapping", json={
        "document_id": "nope", "table_id": "x"}).status_code == 404
    # 404: таблица
    assert client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"], "table_id": "p99-t9"}).status_code == 404


def test_api_suggest_mapping_not_configured(monkeypatch):
    doc = _upload_table_doc()
    monkeypatch.setattr(settings, "yandexgpt_api_key", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    r = client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"], "table_id": doc["tables"][0]["id"]})
    assert r.status_code == 503
    assert "LLM_NOT_CONFIGURED" in r.json()["detail"]


def test_api_suggest_mapping_llm_failure(monkeypatch):
    doc = _upload_table_doc()
    monkeypatch.setattr(settings, "yandexgpt_api_key", "test-key")

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    r = client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"], "table_id": doc["tables"][0]["id"]})
    assert r.status_code == 502
    assert "LLM_FAILED" in r.json()["detail"]


# ---------- feedback: лог коррекций пользователя (4.6) ----------

def _feedback_body(doc_id, table_id, **correction):
    c = {
        "row": 1, "col": 0, "text": "НС 13-70",
        "llm_role": "model", "llm_confidence": 0.55,
        "alternatives": [{"role": "param", "confidence": 0.4}],
        "user_role": "param", "via": "alternative",
    }
    c.update(correction)
    return {"document_id": doc_id, "table_id": table_id, "model": "test-model", "corrections": [c]}


def test_api_llm_feedback_logged():
    doc = _upload_table_doc()
    storage.load_llm_feedback  # warm-up импорта
    before = len(storage.load_llm_feedback())
    r = client.post("/api/llm/feedback", json=_feedback_body(doc["id"], doc["tables"][0]["id"]))
    assert r.status_code == 200, r.text
    assert r.json() == {"logged": 1}
    lines = storage.load_llm_feedback()[before:]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["document_id"] == doc["id"]
    assert entry["table_id"] == doc["tables"][0]["id"]
    assert entry["model"] == "test-model"
    assert entry["timestamp"]
    assert entry["llm_role"] == "model" and entry["user_role"] == "param"
    assert entry["via"] == "alternative"
    assert entry["alternatives"] == [{"role": "param", "confidence": 0.4}]


def test_api_llm_feedback_user_role_none():
    # снятие роли — user_role=null валиден
    doc = _upload_table_doc()
    before = len(storage.load_llm_feedback())
    body = _feedback_body(doc["id"], doc["tables"][0]["id"], user_role=None, via="role-button")
    r = client.post("/api/llm/feedback", json=body)
    assert r.status_code == 200
    entry = storage.load_llm_feedback()[before:][0]
    assert entry["user_role"] is None


def test_api_llm_feedback_errors():
    doc = _upload_table_doc()
    # пустой список коррекций
    r = client.post("/api/llm/feedback", json={
        "document_id": doc["id"], "table_id": "t", "corrections": []})
    assert r.status_code == 422
    assert "NO_CORRECTIONS" in r.json()["detail"]
    # неверная роль LLM
    body = _feedback_body(doc["id"], "t", llm_role="bogus")
    assert client.post("/api/llm/feedback", json=body).status_code == 422
    # неверная роль пользователя
    body = _feedback_body(doc["id"], "t", user_role="bogus")
    assert client.post("/api/llm/feedback", json=body).status_code == 422
    # документ не обязан существовать — лог независим от документов
    r = client.post("/api/llm/feedback", json=_feedback_body("deleted-doc", "t"))
    assert r.status_code == 200


# ---------- кэш и метрики (4.7) ----------------------------------------------

@pytest.fixture()
def iso_storage(tmp_path):
    """Свежий Storage во временном каталоге — кэш/счётчики не текут между тестами."""
    from app.storage import Storage

    return Storage(Settings(data_dir=str(tmp_path)))


def test_messages_fingerprint():
    """Ключ меняется от содержимого запроса: таблица, контекст, промпты."""
    svc = LLMMapperService(_settings(), n_examples=0)
    t1 = _mk_table([["Модель", "Подача"], ["НС 13-70", "13"]], header_rows=0)
    t2 = _mk_table([["Модель", "Напор"], ["НС 13-70", "70"]], header_rows=0)
    k1 = messages_fingerprint(svc.build_messages(t1, "док", None))
    assert messages_fingerprint(svc.build_messages(t1, "док", None)) == k1  # стабилен
    assert messages_fingerprint(svc.build_messages(t2, "док", None)) != k1  # другая таблица
    assert messages_fingerprint(svc.build_messages(t1, "другой", None)) != k1  # другой контекст


def test_suggest_mapping_cache_hit(monkeypatch, iso_storage):
    """Второй вызов с той же таблицей — из кэша: LLM не вызывается, cached=true."""
    svc = LLMMapperService(_settings(), storage=iso_storage)
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    calls = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append(model)
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    first = svc.suggest_mapping(table, "паспорт", {"doc_type": "passport"})
    assert first["cached"] is False and len(calls) == 1
    second = svc.suggest_mapping(table, "паспорт", {"doc_type": "passport"})
    assert second["cached"] is True and len(calls) == 1  # LLM не ходил
    assert second["annotations"] == first["annotations"]
    assert second["processing_time_ms"] == 0
    # другая таблица — другой ключ, снова живой запрос
    other = _mk_table([["Другая", "таблица"], ["x", "y"]], header_rows=0)
    svc.suggest_mapping(other, "паспорт", {"doc_type": "passport"})
    assert len(calls) == 2


def test_suggest_mapping_refresh_bypasses_cache(monkeypatch, iso_storage):
    """refresh=True — принудительный обход кэша + перезапись записи."""
    svc = LLMMapperService(_settings(), storage=iso_storage)
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    calls = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append(model)
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    svc.suggest_mapping(table)
    fresh = svc.suggest_mapping(table, refresh=True)
    assert fresh["cached"] is False and len(calls) == 2
    assert svc.suggest_mapping(table)["cached"] is True and len(calls) == 2  # снова из кэша


def test_suggest_mapping_cache_invalidated_by_prompt_change(monkeypatch, iso_storage):
    """Правка промптов меняет фингерпринт — кэш автоматически инвалидируется."""
    prompts = load_prompts()
    svc = LLMMapperService(_settings(), prompts=prompts, storage=iso_storage)
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    calls = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append(model)
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    svc.suggest_mapping(table)
    svc.suggest_mapping(table)
    assert len(calls) == 1
    # «правим» system prompt — тот же сервис, другой ключ
    prompts["system"] = prompts["system"] + "\nНовое правило."
    svc.suggest_mapping(table)
    assert len(calls) == 2


def test_suggest_mapping_no_storage_no_cache(monkeypatch):
    """Без хранилища (storage=None) кэша нет — каждый запрос живой (удобно для тестов)."""
    svc = LLMMapperService(_settings())  # storage не передан
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    calls = []

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append(model)
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    a = svc.suggest_mapping(table)
    b = svc.suggest_mapping(table)
    assert len(calls) == 2 and a["cached"] is False and b["cached"] is False


def test_llm_stats_counters(monkeypatch, iso_storage):
    """Счётчики: успешный запрос (время+токены), кэш-хит (только requests), отказ."""
    svc = LLMMapperService(_settings(), storage=iso_storage)
    table = _mk_table([["Метка", "Значение"], ["Подача", "13"]], header_rows=0)
    good = {"choices": [{"message": {"content": GOOD_ANSWER}}],
            "usage": {"prompt_tokens": 3500, "completion_tokens": 800}}

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        if model == settings.yandexgpt_rule_model:
            return good
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    svc.suggest_mapping(table)          # успех: requests=1, токены посчитаны
    svc.suggest_mapping(table)          # кэш: requests=2, хиты=1
    failing = LLMMapperService(_settings(), storage=iso_storage)
    monkeypatch.setattr(
        llm_mapping, "chat_completions",
        lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("boom")))
    other = _mk_table([["Другая", "таблица"], ["x", "y"]], header_rows=0)  # не из кэша
    with pytest.raises(LLMError):
        failing.suggest_mapping(other)  # отказ: requests=3, failures=1
    stats = iso_storage.load_llm_stats()
    assert stats["requests"] == 3
    assert stats["cache_hits"] == 1
    assert stats["failures"] == 1
    assert stats["prompt_tokens"] == 3500 and stats["completion_tokens"] == 800
    assert stats["total_time_ms"] >= 0


def test_storage_llm_suggest_cache_eviction(iso_storage):
    """Лимит записей кэша: старые вытесняются, новые остаются."""
    for i in range(iso_storage.LLM_SUGGEST_CACHE_LIMIT + 5):
        iso_storage.save_llm_suggest_cache(f"key-{i}", {"model": f"m{i}"})
    # последние 200 сохранённых читаются, самые первые — вытеснены
    assert iso_storage.get_llm_suggest_cache("key-5")["model"] == "m5"
    assert iso_storage.get_llm_suggest_cache(f"key-{iso_storage.LLM_SUGGEST_CACHE_LIMIT + 4}")["model"]
    assert iso_storage.get_llm_suggest_cache("key-0") is None
    assert iso_storage.get_llm_suggest_cache("no-such-key") is None


def test_storage_llm_stats_file_roundtrip(iso_storage):
    """log_llm_stats аккумулирует и переживает перезапись файла; битый файл → нули."""
    iso_storage.log_llm_stats(cache_hit=False, ok=True, processing_ms=100, prompt_tokens=10, completion_tokens=5)
    iso_storage.log_llm_stats(cache_hit=True, ok=True, processing_ms=0)
    stats = iso_storage.load_llm_stats()
    assert stats["requests"] == 2 and stats["cache_hits"] == 1 and stats["failures"] == 0
    assert stats["total_time_ms"] == 100
    assert stats["prompt_tokens"] == 10 and stats["completion_tokens"] == 5
    (iso_storage.data_dir / "llm_stats.json").write_text("не json", encoding="utf-8")
    assert iso_storage.load_llm_stats() == {}


def test_compute_accuracy():
    """Точность из журнала: подтверждения считаются, правки/снятия — нет; дедуп по клетке."""
    assert compute_accuracy([]) is None
    feedback = [
        {"document_id": "d", "table_id": "t", "row": 1, "col": 0, "llm_role": "model", "user_role": "model"},    # ✓
        {"document_id": "d", "table_id": "t", "row": 2, "col": 0, "llm_role": "model", "user_role": "param"},    # правка
        {"document_id": "d", "table_id": "t", "row": 3, "col": 0, "llm_role": "value", "user_role": None},       # снятие
        {"document_id": "d", "table_id": "t", "row": 1, "col": 0, "llm_role": "model", "user_role": "param"},    # дубль клетки — берётся последняя
        {"document_id": "d", "table_id": "t", "row": "bad", "col": 0, "llm_role": "x", "user_role": "x"},        # битая строка
    ]
    acc = compute_accuracy(feedback)
    assert acc["corrected_cells"] == 3
    assert acc["agreed"] == 0  # дубль (1,0) перезаписал подтверждение правкой
    assert acc["accuracy"] == 0.0
    ok = compute_accuracy([
        {"document_id": "d", "table_id": "t", "row": 1, "col": 0, "llm_role": "model", "user_role": "model"},
        {"document_id": "d", "table_id": "t", "row": 2, "col": 0, "llm_role": "model", "user_role": "model"},
    ])
    assert ok == {"corrected_cells": 2, "agreed": 2, "accuracy": 1.0}


def test_api_llm_metrics(monkeypatch):
    doc = _upload_table_doc()
    table_id = doc["tables"][0]["id"]

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        return _ok_response(GOOD_ANSWER)

    monkeypatch.setattr(settings, "yandexgpt_api_key", "test-key")
    monkeypatch.setattr(llm_mapping, "chat_completions", fake_chat)
    before = storage.load_llm_stats()
    body = {"document_id": doc["id"], "table_id": table_id}
    client.post("/api/llm/suggest-mapping", json=body)               # живой запрос
    r2 = client.post("/api/llm/suggest-mapping", json=body)          # из кэша
    assert r2.json()["cached"] is True
    # refresh обходит кэш
    r3 = client.post("/api/llm/suggest-mapping", json={**body, "refresh": True})
    assert r3.json()["cached"] is False

    r = client.get("/api/llm/metrics")
    assert r.status_code == 200
    m = r.json()
    delta = lambda key: m[key] - before.get(key, 0)  # дельты: счётчики глобальные, копятся между тестами
    assert delta("requests") == 3 and delta("cache_hits") == 1 and delta("failures") == 0
    assert m["cache_hit_rate"] == round(m["cache_hits"] / m["requests"], 4)
    assert m["avg_time_ms"] >= 0
    # точность — из журнала коррекций; до/после проверки через дельту по свежей коррекции
    # (журнал глобальный на тестовую сессию, пустым он быть не обязан)
    before_cells = m["corrected_cells"] or 0
    unique = f"metrics-{time.time_ns()}"  # уникальный document_id — точно новая клетка в дедупе
    client.post("/api/llm/feedback", json=_feedback_body(unique, table_id))
    m = client.get("/api/llm/metrics").json()
    assert m["corrected_cells"] == before_cells + 1
    assert m["accuracy"] == round(m["agreed"] / m["corrected_cells"], 4)
