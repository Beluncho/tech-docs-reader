import os

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _upload_txt():
    content = "Подача: 12,5 м3/ч\nНапор: 32 м\nМощность: 7,5 кВт\n"
    r = client.post(
        "/api/documents",
        files={"file": ("pasport.txt", content.encode("utf-8"), "text/plain")},
    )
    assert r.status_code == 200
    return r.json()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_and_extract_keyvalue():
    doc = _upload_txt()
    assert doc["fmt"] == "txt"
    r = client.post(
        f"/api/documents/{doc['id']}/extract",
        json={"preset_id": "keyvalue_generic"},
    )
    assert r.status_code == 200
    params = r.json()["parameters"]
    assert len(params) >= 1
    assert params[0]["evidence"]["quote"]


def test_presets_crud():
    r = client.get("/api/presets")
    assert r.status_code == 200
    n = len(r.json())
    payload = {
        "name": "Тестовый набор",
        "columns": {"count": 2, "roles": {"label": 0, "value": 1}},
        "labels": ["подача"],
    }
    r = client.post("/api/presets", json=payload)
    assert r.status_code == 200
    pid = r.json()["id"]
    r = client.put(f"/api/presets/{pid}", json={**payload, "name": "Тестовый набор 2"})
    assert r.status_code == 200
    assert r.json()["name"] == "Тестовый набор 2"
    r = client.delete(f"/api/presets/{pid}")
    assert r.status_code == 200
    assert client.get("/api/presets").json().__len__() == n


def test_extract_without_preset_requires_mapping():
    doc = _upload_txt()
    r = client.post(f"/api/documents/{doc['id']}/extract", json={"use_llm": False})
    assert r.status_code in (200, 409)
    if r.status_code == 409:
        assert "NO_PRESET_MATCHED" in r.json()["detail"]


def _fake_llm(load_result, settings, doc_name, preset=None, table_id=None):
    from app.models import Evidence, Parameter

    return (
        [
            Parameter(
                name="Подача",
                value="12,5",
                unit="м3/ч",
                evidence=Evidence(document=doc_name, quote="Подача: 12,5 м3/ч", fragment="lines0"),
                source="llm",
            )
        ],
        "fake-model",
    )


def test_llm_result_not_erased_by_empty_deterministic(monkeypatch):
    import app.api.routes as routes

    monkeypatch.setattr(routes, "extract_with_llm", _fake_llm)
    doc = _upload_txt()

    r = client.post(f"/api/documents/{doc['id']}/extract", json={"use_llm": True})
    assert r.status_code == 200
    assert len(r.json()["parameters"]) == 1

    # детерминированный прогон с "чужим" набором пуст — прошлый результат не затирается
    r2 = client.post(f"/api/documents/{doc['id']}/extract", json={"preset_id": "certificate_2col"})
    assert r2.status_code == 200
    body = r2.json()
    assert len(body["parameters"]) == 1
    assert any("показан предыдущий" in w for w in body["warnings"])

    meta = client.get(f"/api/documents/{doc['id']}").json()
    assert len(meta["last_result"]["parameters"]) == 1


def test_llm_receives_preset_and_table(monkeypatch):
    import app.api.routes as routes

    captured = {}

    def spy_llm(load_result, settings, doc_name, preset=None, table_id=None):
        captured["preset_id"] = preset["id"] if preset else None
        captured["table_id"] = table_id
        return _fake_llm(load_result, settings, doc_name, preset=preset, table_id=table_id)

    monkeypatch.setattr(routes, "extract_with_llm", spy_llm)
    doc = _upload_txt()
    r = client.post(
        f"/api/documents/{doc['id']}/extract",
        json={"preset_id": "keyvalue_generic", "use_llm": True},
    )
    assert r.status_code == 200
    assert captured["preset_id"] == "keyvalue_generic"


def _upload_csv():
    content = "Параметр;Значение\nПодача;12,5 м3/ч\nНапор;32 м\n"
    r = client.post(
        "/api/documents",
        files={"file": ("pas.csv", content.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200
    return r.json()


def test_preset_from_extraction_reuses_source_preset():
    payload = {
        "name": "Тестовый csv набор",
        "columns": {"count": 2, "roles": {"label": 0, "value": 1}},
        "labels": ["подача", "напор"],
    }
    r = client.post("/api/presets", json=payload)
    assert r.status_code == 200
    pid = r.json()["id"]

    doc = _upload_csv()
    r = client.post(f"/api/documents/{doc['id']}/extract", json={"preset_id": pid})
    assert r.status_code == 200
    assert len(r.json()["parameters"]) == 2

    r2 = client.post(
        "/api/presets/from-extraction",
        json={"document_id": doc["id"], "preset_id": pid, "name": "Правило из csv"},
    )
    assert r2.status_code == 200
    out = r2.json()
    assert out["id"] != pid
    assert out["columns"] == {"count": 2, "roles": {"label": 0, "value": 1}}
    assert "подача" in out["labels"]
    assert "напор" in out["labels"]


def test_preset_from_extraction_picks_table_from_llm_fragment(monkeypatch):
    import app.api.routes as routes

    def fake_llm(load_result, settings, doc_name, preset=None, table_id=None):
        from app.models import Evidence, Parameter

        return (
            [
                Parameter(
                    name="Подача",
                    value="12,5",
                    unit="м3/ч",
                    evidence=Evidence(document=doc_name, quote="Подача: 12,5 м3/ч", fragment="csv"),
                    source="llm",
                )
            ],
            "fake-model",
        )

    monkeypatch.setattr(routes, "extract_with_llm", fake_llm)
    doc = _upload_csv()

    r = client.post(f"/api/documents/{doc['id']}/extract", json={"use_llm": True})
    assert r.status_code == 200
    assert len(r.json()["parameters"]) == 1

    r2 = client.post(
        "/api/presets/from-extraction",
        json={"document_id": doc["id"], "name": "Правило csv llm"},
    )
    assert r2.status_code == 200
    out = r2.json()
    assert out["columns"]["count"] == 2
    assert "подача" in out["labels"]
def _make_pdf_bytes():
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Flow 12.5 m3/h  Head 32 m")
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_extract_reloads_fresh_page_words(monkeypatch):
    import app.api.routes as routes

    pdf = _make_pdf_bytes()
    r = client.post(
        "/api/documents",
        files={"file": ("pas.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["fmt"] == "pdf"

    real_load = routes.load_document_cached
    captured = {}

    def spy_load(path, fmt, **kw):
        lr = real_load(str(path), fmt, **kw)
        captured["words"] = lr.page_words
        return lr

    monkeypatch.setattr(routes, "load_document_cached", spy_load)
    pid = client.post(
        "/api/presets",
        json={"name": "free walk test", "columns": {}, "include_all": True},
    ).json()["id"]
    r2 = client.post(
        f"/api/documents/{doc['id']}/extract",
        json={"preset_id": pid, "include_all": True},
    )
    assert r2.status_code == 200
    assert captured.get("words"), "PDF extract must re-load original to obtain page_words"
