"""Шаблоны маппинга из Visual Mapper (этап 3.7): сигнатуры, матчинг, API."""
from fastapi.testclient import TestClient

from app.api.deps import storage
from app.main import app
from app.mapping_template import (
    compute_table_fingerprint,
    match_templates,
    table_signature,
    template_from_mapping,
)
from app.preset import Cell, Table

client = TestClient(app)


def _mk_table(cols=3, header_rows=1, header_texts=("Параметр", "Модель", "Значение")):
    rows = [[Cell(text=t, row=0, col=j) for j, t in enumerate(header_texts)]]
    for i in range(1, 3):
        rows.append([
            Cell(text="Метка" if j == 0 else f"v{i}", row=i, col=j) for j in range(cols)
        ])
    return Table(id="t1", rows=rows, header_rows=header_rows)


def test_table_signature_from_header_rows():
    sig = table_signature(_mk_table())
    assert sig == {"cols": 3, "headers": ["параметр", "модель", "значение"]}


def test_table_signature_fallback_first_row():
    t = _mk_table(header_rows=0)
    sig = table_signature(t)
    # шапки нет — берём первую непустую строку
    assert sig == {"cols": 3, "headers": ["параметр", "модель", "значение"]}


def test_fingerprint_stable_and_sensitive_to_cols():
    sig = table_signature(_mk_table())
    assert compute_table_fingerprint(sig) == compute_table_fingerprint(table_signature(_mk_table()))
    other = table_signature(_mk_table(cols=4, header_texts=("A", "B", "C", "D")))
    assert compute_table_fingerprint(sig) != compute_table_fingerprint(other)


def test_match_exact_partial_and_miss():
    tpl = template_from_mapping(
        name="T", description="", document_id="d", table=_mk_table(),
        source={}, ops=[], annotations=[{"row": 1, "col": 1, "role": "value"}],
    )
    exact = match_templates([tpl], table_signature(_mk_table()))
    assert len(exact) == 1 and exact[0]["method"] == "exact" and exact[0]["score"] == 1.0

    # 4 колонки, одна ячейка шапки пуста (3 заголовка из 4) → partial (Jaccard 3/4)
    tpl4 = template_from_mapping(
        name="T4", description="", document_id="d",
        table=_mk_table(cols=4, header_texts=("Параметр", "Модель", "Значение", "Масса")),
        source={}, ops=[], annotations=[{"row": 1, "col": 1, "role": "value"}],
    )
    three_of_four = table_signature(_mk_table(cols=4, header_texts=("Параметр", "Модель", "Значение", "")))
    partial = match_templates([tpl4], three_of_four)
    assert len(partial) == 1 and partial[0]["method"] == "partial" and partial[0]["score"] >= 0.75

    # другое число колонок → нет матча
    wider = _mk_table(cols=4, header_texts=("A", "B", "C", "D"))
    assert match_templates([tpl], table_signature(wider)) == []

    # совсем другая шапка при том же числе колонок → нет матча
    other = _mk_table(header_texts=("Диаметр", "Масса", "Длина"))
    assert match_templates([tpl], table_signature(other)) == []


def test_match_sorted_by_score():
    target = table_signature(_mk_table(cols=4, header_texts=("Параметр", "Модель", "Значение", "")))
    t1 = template_from_mapping(
        name="A", description="", document_id="d",
        table=_mk_table(cols=4, header_texts=("Параметр", "Модель", "Значение", "")),
        source={}, ops=[], annotations=[{"row": 0, "col": 0, "role": "param"}],
    )
    t2 = template_from_mapping(
        name="B", description="", document_id="d",
        table=_mk_table(cols=4, header_texts=("Параметр", "Модель", "Значение", "Масса")),
        source={}, ops=[], annotations=[{"row": 0, "col": 0, "role": "param"}],
    )
    matches = match_templates([t2, t1], target)
    assert [m["template"]["name"] for m in matches] == ["A", "B"]
    assert matches[0]["method"] == "exact" and matches[1]["method"] == "partial"


def test_storage_crud():
    t1 = template_from_mapping(
        name="крч шаблон", description="d", document_id="d", table=_mk_table(),
        source={}, ops=[{"op": "transpose"}], annotations=[{"row": 0, "col": 0, "role": "param"}],
    )
    mid = storage.save_mapping(t1)
    assert mid == "крч-шаблон"
    got = storage.get_mapping(mid)
    assert got["name"] == "крч шаблон" and got["ops"] == [{"op": "transpose"}]
    assert got["created_at"]  # проставлен при сохранении
    assert any(m["id"] == mid for m in storage.list_mappings())
    assert storage.delete_mapping(mid) is True
    assert storage.get_mapping(mid) is None
    assert storage.delete_mapping(mid) is False


def _upload_table_doc():
    """Документ с одной таблицей (xlsx с двумя строками шапки-заголовков)."""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Параметр", "Модель", "Значение"])
    ws.append(["Подача", "НС 13-70", "70"])
    ws.append(["Напор", "НС 13-70", "45"])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(
        "/api/documents",
        files={"file": ("tab.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    return r.json()


def test_api_save_list_match_delete():
    doc = _upload_table_doc()
    table_id = doc["tables"][0]["id"]

    # сохранить шаблон
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "Мой шаблон",
        "document_id": doc["id"],
        "table_id": table_id,
        "ops": [{"op": "transpose"}],
        "annotations": [
            {"row": 0, "col": 0, "role": "param", "label": "Параметр"},
            {"row": 0, "col": 1, "role": "model"},
            {"row": 1, "col": 1, "role": "value"},
        ],
    })
    assert r.status_code == 200, r.text
    tpl = r.json()
    assert tpl["name"] == "Мой шаблон"
    assert [o["op"] for o in tpl["ops"]] == ["transpose"]
    assert tpl["annotations"][0]["label"] == "Параметр"
    assert tpl["table_signature"]["cols"] == 3
    assert tpl["table_fingerprint"]
    mid = tpl["id"]

    # список
    r = client.get("/api/mapping/templates")
    assert r.status_code == 200
    assert any(t["id"] == mid for t in r.json())

    # автоподбор: та же таблица → exact
    r = client.get("/api/mapping/match", params={"document_id": doc["id"], "table_id": table_id})
    assert r.status_code == 200
    m = r.json()
    assert m and m["method"] == "exact" and m["template"]["id"] == mid

    # повторное сохранение того же имени — тот же id (перезапись)
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "Мой шаблон",
        "document_id": doc["id"],
        "table_id": table_id,
        "annotations": [{"row": 0, "col": 0, "role": "param"}],
    })
    assert r.status_code == 200 and r.json()["id"] == mid

    # удаление → матч пуст
    r = client.delete(f"/api/mapping/templates/{mid}")
    assert r.status_code == 200
    r = client.get("/api/mapping/match", params={"document_id": doc["id"], "table_id": table_id})
    assert r.status_code == 200 and r.json() is None
    assert client.delete(f"/api/mapping/templates/{mid}").status_code == 404


def test_api_match_document():
    """Лёгкие мачи всех таблиц документа — для индикаторов основного UI (3.9)."""
    doc = _upload_table_doc()
    table_id = doc["tables"][0]["id"]

    # без сохранённых шаблонов — записи без template_id (быстрый путь)
    r = client.get("/api/mapping/match-document", params={"document_id": doc["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["document_id"] == doc["id"]
    assert [m["table_id"] for m in out["matches"]] == [t["id"] for t in doc["tables"]]
    assert all(m["template_id"] is None and m["template_name"] is None for m in out["matches"])

    # сохранить шаблон от таблицы → её запись получает матч, ответ лёгкий
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "Мой шаблон",
        "document_id": doc["id"],
        "table_id": table_id,
        "annotations": [
            {"row": 0, "col": 0, "role": "param"},
            {"row": 0, "col": 1, "role": "model"},
        ],
    })
    assert r.status_code == 200, r.text
    mid = r.json()["id"]

    r = client.get("/api/mapping/match-document", params={"document_id": doc["id"]})
    out = r.json()
    by_table = {m["table_id"]: m for m in out["matches"]}
    assert by_table[table_id]["template_id"] == mid
    assert by_table[table_id]["template_name"] == "Мой шаблон"
    assert by_table[table_id]["method"] == "exact" and by_table[table_id]["score"] == 1.0
    # лёгкий формат: без ops/аннотаций (их раздаёт /mapping/templates и apply-template)
    assert "ops" not in out["matches"][0] and "annotations" not in out["matches"][0]

    # удаление шаблона → матч пропадает
    assert client.delete(f"/api/mapping/templates/{mid}").status_code == 200
    r = client.get("/api/mapping/match-document", params={"document_id": doc["id"]})
    assert all(m["template_id"] is None for m in r.json()["matches"])

    # несуществующий документ
    r = client.get("/api/mapping/match-document", params={"document_id": "nope"})
    assert r.status_code == 404


def test_api_validation_errors():
    doc = _upload_table_doc()
    # несуществующий документ
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "x", "document_id": "nope", "table_id": "t",
        "annotations": [{"row": 0, "col": 0, "role": "param"}],
    })
    assert r.status_code == 404
    # несуществующая таблица
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "x", "document_id": doc["id"], "table_id": "nope",
        "annotations": [{"row": 0, "col": 0, "role": "param"}],
    })
    assert r.status_code == 404
    # пустые аннотации
    r = client.post("/api/presets/save-from-mapping", json={
        "name": "x", "document_id": doc["id"], "table_id": doc["tables"][0]["id"],
        "annotations": [],
    })
    assert r.status_code == 422
    # матч по несуществующей таблице
    r = client.get("/api/mapping/match", params={"document_id": doc["id"], "table_id": "nope"})
    assert r.status_code == 404
