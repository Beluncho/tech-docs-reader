# -*- coding: utf-8 -*-
"""Подэтап 5.1: end-to-end тесты полного цикла через реальные API-эндпоинты.

В отличие от тестов отдельных модулей (2.x–4.x), здесь проверяются СЦЕНАРИИ:
сквозные цепочки upload → детекция → маппинг/шаблоны → извлечение → набор
из результата → повторный документ с автоподбором. LLM-контуры — с моками
(ключ не нужен); OCR — только ветка отказа без воркера.

"""
import io
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.api.deps import settings, storage  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload_bytes(name: str, data: bytes, mime: str, ocr: bool = False):
    files = {"file": (name, data, mime)}
    data_ = {"ocr": "true"} if ocr else None
    r = client.post("/api/documents", files=files, data=data_)
    assert r.status_code == 200, r.text
    return r.json()


def _upload_kv_doc() -> dict:
    """Синтетический key-value документ (csv): пары «метка;значение» + таблица.

    CSV даёт таблицу (нужна для from-extraction), в отличие от txt.
    """
    content = "Параметр;Значение\nПодача;12,5 м3/ч\nНапор;32 м\nМощность;7,5 кВт\n"
    return _upload_bytes("e2e_pasport.csv", content.encode("utf-8"), "text/csv")


def _upload_matrix_xlsx() -> dict:
    """Синтетическая матрица «модель × параметр» (xlsx, без merged — простая сетка).

    Используется для полного цикла шаблонов Visual Mapper на синтетической таблице.
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Модель", "Подача, м3/ч", "Напор, м"])
    ws.append(["НС 13-70", "13", "70"])
    ws.append(["НС 13-105", "13", "105"])
    ws.append(["НС 13-140", "13", "140"])
    buf = io.BytesIO()
    wb.save(buf)
    return _upload_bytes("e2e_matrix.xlsx", buf.getvalue(), XLSX_MIME)


# ============================ Сценарий A ============================
# Полный цикл Visual Mapper: upload → шаблон (save-from-mapping) → match →
# apply-template → validate (предпросмотр) → повторный документ → автоподбор.
# На синтетике — чтобы гонялся всегда.


def test_scenario_a_visual_mapper_template_lifecycle():
    doc = _upload_matrix_xlsx()
    assert doc["status"] == "PARSED", doc.get("error")
    tables = doc["tables"]
    assert tables, "в xlsx должна найтись таблица"
    table = tables[0]
    assert table["n_cols"] == 3

    # 1. Сохранить шаблон: роли в координатах исходной таблицы
    annotations = [
        {"row": i, "col": 0, "role": "model"} for i in range(1, 4)
    ] + [
        {"row": 0, "col": j, "role": "param"} for j in (1, 2)
    ] + [
        {"row": i, "col": j, "role": "value"} for i in range(1, 4) for j in (1, 2)
    ]
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "E2E матрица НС-синтетика",
        "document_id": doc["id"],
        "table_id": table["id"],
        "annotations": annotations,
    })
    assert r.status_code == 200, r.text
    tpl = r.json()
    template_id = tpl["id"]
    assert tpl["table_signature"]["cols"] == 3

    try:
        # 2. Матч той же таблицы — exact
        r = client.get("/api/mapping/match", params={
            "document_id": doc["id"], "table_id": table["id"]})
        assert r.status_code == 200
        m = r.json()
        assert m and m["method"] == "exact" and m["template"]["id"] == template_id

        # 3. Применить шаблон: таблица + нормализованные роли
        r = client.post("/api/mapping/apply-template", json={
            "document_id": doc["id"], "table_id": table["id"], "template_id": template_id})
        assert r.status_code == 200, r.text
        applied = r.json()
        assert applied["applied_ops"] == 0
        roles = {(a["row"], a["col"]): a["role"] for a in applied["annotations"]}
        assert roles[(1, 0)] == "model" and roles[(0, 1)] == "param" and roles[(3, 2)] == "value"

        # 4. Предпросмотр извлечения по ops + ролям (как из маппера перед сохранением)
        r = client.post("/api/mapping/validate", json={
            "document_id": doc["id"],
            "table_id": table["id"],
            "ops": [],
            "annotations": annotations,
        })
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["stats"]["models"] == 3, out
        # 3 модели × 2 параметра = 6 значений, без предупреждений
        assert out["stats"]["values"] == 6, out["stats"]
        assert not out["warnings"], out["warnings"]
        models = {c["model"] for c in out["cards"]}
        assert models == {"НС 13-70", "НС 13-105", "НС 13-140"}

        # 5. Повторная загрузка ТОГО ЖЕ файла — шаблон подбирается автоматически
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Модель", "Подача, м3/ч", "Напор, м"])
        ws.append(["НС 13-70", "13", "70"])
        ws.append(["НС 13-105", "13", "105"])
        ws.append(["НС 13-140", "13", "140"])
        buf = io.BytesIO()
        wb.save(buf)
        doc2 = _upload_bytes("e2e_matrix_again.xlsx", buf.getvalue(), XLSX_MIME)
        r = client.get("/api/mapping/match-document", params={"document_id": doc2["id"]})
        assert r.status_code == 200
        by_table = {m["table_id"]: m for m in r.json()["matches"]}
        matched = [m for m in by_table.values() if m["template_id"] == template_id]
        assert matched, "шаблон должен автоматически найтись для того же файла"
        assert matched[0]["method"] == "exact" and matched[0]["template_name"] == "E2E матрица НС-синтетика"
    finally:
        assert client.delete(f"/api/mapping/templates/{template_id}").status_code == 200


# ============================ Сценарий B ============================
# Цикл «прогон → набор»: extract → from-extraction → повторный файл → автоподбор набора.


def test_scenario_b_preset_from_extraction_and_reuse():
    doc = _upload_kv_doc()

    # 1. Извлечь встроенным набором
    r = client.post(f"/api/documents/{doc['id']}/extract",
                    json={"preset_id": "keyvalue_generic"})
    assert r.status_code == 200, r.text
    result = r.json()
    assert len(result["parameters"]) == 3
    assert all(p["evidence"]["quote"] for p in result["parameters"])
    assert result["source"] == "preset"

    # 2. Сохранить набор из результата (кнопка «Сохранить правило из результата»)
    r = client.post("/api/presets/from-extraction", json={
        "document_id": doc["id"],
        "name": "E2E правило из прогона",
        "preset_id": "keyvalue_generic",
    })
    assert r.status_code == 200, r.text
    new_preset = r.json()
    assert new_preset["id"] != "keyvalue_generic"

    # 3. Повторная загрузка того же содержимого — автоподбор находит НОВЫЙ набор
    content = "Параметр;Значение\nПодача;12,5 м3/ч\nНапор;32 м\nМощность;7,5 кВт\n"
    doc2 = _upload_bytes("e2e_pasport_copy.csv", content.encode("utf-8"), "text/csv")
    assert doc2["auto_preset_id"] == new_preset["id"], (
        f"автоподбор: {doc2['auto_preset_id']}, ожидался {new_preset['id']}")

    # 4. Извлечение без явного preset_id использует автоподобранный
    r = client.post(f"/api/documents/{doc2['id']}/extract", json={})
    assert r.status_code == 200, r.text
    assert r.json()["preset_id"] == new_preset["id"]
    assert len(r.json()["parameters"]) == 3

    # 5. reprocess — повторное извлечение того же документа
    r = client.post(f"/api/documents/{doc2['id']}/reprocess", json={})
    assert r.status_code == 200 and len(r.json()["parameters"]) == 3


# ============================ Сценарий C ============================
# LLM-контуры с моками: suggest-mapping → feedback → metrics; extract use_llm
# → from-extraction из LLM-результата. Ключ не нужен (chat_completions мокается).


_LLM_SUGGEST_ANSWER = """
{
  "orientation": "rows",
  "structure": "matrix",
  "complexity": "low",
  "annotations": [
    {"role": "model", "range": [1, 0, 3, 0], "confidence": 0.98},
    {"role": "param", "row": 0, "col": 1, "confidence": 0.95},
    {"role": "value", "range": [1, 1, 3, 2], "confidence": 0.9},
    {"role": "value", "row": 0, "col": 0, "confidence": 0.55,
     "alternatives": [{"role": "param", "confidence": 0.4}]}
  ],
  "notes": "заголовок оси спорный"
}
"""


def _mock_chat(answer: str):
    from app import llm_mapping

    def fake_chat(settings, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        return {"choices": [{"message": {"content": answer}}]}

    return fake_chat


def test_scenario_c_llm_mapping_assistant_flow(monkeypatch):
    from app import llm_mapping

    doc = _upload_matrix_xlsx()
    table_id = doc["tables"][0]["id"]

    monkeypatch.setattr(settings, "yandexgpt_api_key", "test-key")
    monkeypatch.setattr(llm_mapping, "chat_completions", _mock_chat(_LLM_SUGGEST_ANSWER))

    # 1. Предложение маппинга (как кнопка «🤖 Предложить маппинг»)
    r = client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"], "table_id": table_id,
        "context": {"doc_type": "passport", "keywords": ["насос"]},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # range-зоны развёрнуты по якорям 4×3 + одна спорная клетка (0,0)
    assert body["summary"]["total"] >= 6
    assert body["summary"]["low_confidence"] >= 1
    assert all(a.get("range") is None for a in body["annotations"])

    # 2. Повтор того же запроса — из кэша (4.7), LLM не вызывается
    calls = []

    def counting_chat(s, model, messages, attempt=0, temperature=0.0, max_tokens=None):
        calls.append(model)
        return {"choices": [{"message": {"content": _LLM_SUGGEST_ANSWER}}]}

    monkeypatch.setattr(llm_mapping, "chat_completions", counting_chat)
    r2 = client.post("/api/llm/suggest-mapping", json={
        "document_id": doc["id"], "table_id": table_id,
        "context": {"doc_type": "passport", "keywords": ["насос"]},
    })
    assert r2.status_code == 200 and r2.json()["cached"] is True
    assert not calls, "кэш-хит не должен вызывать LLM"
    assert r2.json()["annotations"] == body["annotations"]

    # 3. Пользовательские коррекции → журнал (панель спорных клеток 4.6)
    low = [a for a in body["annotations"] if a.get("confidence", 1) < 0.7]
    assert low, "в ответе должна быть хотя бы одна спорная клетка"
    cell = low[0]
    unique_doc = f"e2e-c-{time.time_ns()}"  # уникальные document_id/table_id — новые клетки в дедупе журнала
    corrections = [{
        "row": cell["row"], "col": cell["col"], "text": "исправлено",
        "llm_role": cell["role"], "llm_confidence": cell.get("confidence", 0.5),
        "alternatives": [{"role": "param", "confidence": 0.4}],
        "user_role": "param", "via": "alternative",
    }, {
        # вторая коррекция — «✓ верно» без правки
        "row": body["annotations"][0]["row"], "col": body["annotations"][0]["col"],
        "llm_role": body["annotations"][0]["role"],
        "llm_confidence": 0.98, "user_role": None, "via": "confirm",
    }]
    r = client.post("/api/llm/feedback", json={
        "document_id": unique_doc, "table_id": table_id,
        "model": body["model"], "corrections": corrections,
    })
    assert r.status_code == 200 and r.json()["logged"] == 2

    # 4. Метрики: запросы/кэш/точность из журнала коррекций
    # (счётчики и журнал глобальны на тестовую сессию — проверяем дельтами)
    r = client.get("/api/llm/metrics")
    assert r.status_code == 200
    m = r.json()
    assert m["requests"] >= 2 and m["cache_hits"] >= 1
    assert m["corrected_cells"] is not None and m["corrected_cells"] >= 2
    assert m["accuracy"] == round(m["agreed"] / m["corrected_cells"], 4)


def test_scenario_c_llm_extract_then_preset_from_result(monkeypatch):
    """«Извлечь через LLM» → «Сохранить правило из результата» → автоподбор."""
    import app.api.routes as routes
    from app.models import Evidence, Parameter

    def fake_extract_llm(load_result, settings, doc_name, preset=None, table_id=None):
        return (
            [Parameter(
                name="Подача", value="12,5", unit="м3/ч",
                evidence=Evidence(document=doc_name, quote="Подача: 12,5 м3/ч",
                                  fragment="lines0"),
                source="llm",
            )],
            "fake-model",
        )

    monkeypatch.setattr(routes, "extract_with_llm", fake_extract_llm)
    monkeypatch.setattr(settings, "yandexgpt_api_key", "test-key")

    doc = _upload_kv_doc()
    r = client.post(f"/api/documents/{doc['id']}/extract", json={"use_llm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["llm_used"] and body["source"] == "llm"
    assert len(body["parameters"]) == 1

    # набор из LLM-прогона
    r = client.post("/api/presets/from-extraction", json={
        "document_id": doc["id"], "name": "E2E правило из LLM",
    })
    assert r.status_code == 200, r.text
    assert "подача" in r.json()["labels"]

    # повторный тот же документ — автоподбор нового набора
    content = "Параметр;Значение\nПодача;12,5 м3/ч\nНапор;32 м\nМощность;7,5 кВт\n"
    doc2 = _upload_bytes("e2e_llm_copy.csv", content.encode("utf-8"), "text/csv")
    assert doc2["auto_preset_id"]


# ============================ Сценарий D ============================
# Граничные случаи: OCR без воркера, битый файл, несуществующие сущности.


def _make_pdf_bytes() -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Flow 12.5 m3/h  Head 32 m")
    data = doc.tobytes()
    doc.close()
    return data


def test_scenario_d_ocr_failed_without_worker():
    """PDF + ocr=true без OCR-воркера → статус OCR_FAILED, extract даёт 409."""
    r = client.post(
        "/api/documents",
        files={"file": ("scan.pdf", _make_pdf_bytes(), "application/pdf")},
        data={"ocr": "true"},
    )
    assert r.status_code == 200
    doc = r.json()
    if doc["status"] != "OCR_FAILED":
        pytest.skip("OCR-воркер оказался настроен — ветка отказа не проверяется")
    assert doc["error"]

    r = client.post(f"/api/documents/{doc['id']}/extract", json={"preset_id": "keyvalue_generic"})
    assert r.status_code == 409 and "OCR_FAILED" in r.json()["detail"]


def test_scenario_d_unsupported_and_broken_files():
    # неподдерживаемое расширение
    r = client.post("/api/documents", files={
        "file": ("img.jpg", b"\xff\xd8\xff", "image/jpeg")})
    assert r.status_code == 415
    # битый pdf (не PDF) → PARSE_FAILED, не 500
    r = client.post("/api/documents", files={
        "file": ("broken.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 200
    doc = r.json()
    assert doc["status"] == "PARSE_FAILED" and doc["error"]
    r = client.post(f"/api/documents/{doc['id']}/extract", json={"use_llm": False})
    assert r.status_code == 409 and "DOCUMENT_PARSE_FAILED" in r.json()["detail"]


def test_scenario_d_not_found_paths():
    # документ
    assert client.get("/api/documents/nope").status_code == 404
    assert client.post("/api/documents/nope/extract", json={}).status_code == 404
    # несуществующий набор
    doc = _upload_kv_doc()
    r = client.post(f"/api/documents/{doc['id']}/extract", json={"preset_id": "no-such"})
    assert r.status_code == 404 and "PRESET_NOT_FOUND" in r.json()["detail"]
    # LLM не настроен → 503 (ключа нет в тестовом окружении по умолчанию)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "yandexgpt_api_key", "")
    monkeypatch.setattr(settings, "llm_api_key", "")
    try:
        doc_t = _upload_matrix_xlsx()
        r = client.post("/api/llm/suggest-mapping", json={
            "document_id": doc_t["id"], "table_id": doc_t["tables"][0]["id"]})
        assert r.status_code == 503 and "LLM_NOT_CONFIGURED" in r.json()["detail"]
    finally:
        monkeypatch.undo()
