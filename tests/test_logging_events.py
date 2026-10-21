# -*- coding: utf-8 -*-
"""Подэтап 5.7: события мониторинга в логах (upload/extract с doc_id и таймингами).

Контракт событий фиксирован: grep по "event=upload" в логах стенда должен находить
каждый upload с doc_id/статусом/временем. Здесь проверяем появление и ключевые
поля событий через caplog.
"""
import io
import logging
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app  # noqa: E402

client = TestClient(app)

LOGGER = "app.api"


def _event(caplog, event: str, level: int | None = None):
    """Запись события event=... из логгера app.api (последняя, если несколько)."""
    recs = [r for r in caplog.records
            if r.name == LOGGER and f"event={event}" in r.getMessage()
            and (level is None or r.levelno == level)]
    assert recs, f"событие event={event} не найдено в логах: {[r.getMessage() for r in caplog.records]}"
    return recs[-1].getMessage()


def test_upload_event_logged(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        r = client.post(
            "/api/documents",
            files={"file": ("log.txt", io.BytesIO("Параметр: значение\n".encode("utf-8")), "text/plain")},
        )
    assert r.status_code == 200
    doc = r.json()
    msg = _event(caplog, "upload")
    assert f"doc_id={doc['id']}" in msg
    assert "fmt=txt" in msg
    assert "status=PARSED" in msg
    assert "ms=" in msg


def test_upload_parse_failed_event_with_traceback(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        r = client.post(
            "/api/documents",
            files={"file": ("broken.pdf", io.BytesIO(b"%PDF-not-really-a-pdf"), "application/pdf")},
        )
    assert r.status_code == 200
    assert r.json()["status"] == "PARSE_FAILED"
    msg = _event(caplog, "upload")
    assert "status=PARSE_FAILED" in msg
    rec = next(r for r in caplog.records if "status=PARSE_FAILED" in r.getMessage())
    assert rec.exc_info, "ошибка разбора должна логироваться с traceback"


def test_extract_event_logged(caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        up = client.post(
            "/api/documents",
            files={"file": ("log2.txt", io.BytesIO("Подача: 13 м3/ч\n".encode("utf-8")), "text/plain")},
        ).json()
        r = client.post(
            f"/api/documents/{up['id']}/extract",
            json={"preset_id": "keyvalue_generic"},
        )
    assert r.status_code == 200
    msg = _event(caplog, "extract")
    assert f"doc_id={up['id']}" in msg
    assert "preset=keyvalue_generic" in msg
    assert "source=preset" in msg
    assert "params=" in msg and "ms=" in msg
