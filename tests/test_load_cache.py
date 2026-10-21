# -*- coding: utf-8 -*-
"""Подэтап 2.9: тесты LRU-кэша разбора документов (load_document_cached).

Попадание (тот же объект, один вызов load_document), инвалидация по
mtime/размеру, обход кэша при ocr_words, вытеснение LRU.
"""
import os

import app.loaders as loaders
from app.loaders import load_document_cached
from app.loaders._base import LoadResult


def _fake_loader(monkeypatch) -> list[str]:
    """Заменить load_document фейком, возвращающим новый LoadResult на вызов."""
    calls: list[str] = []

    def fake(path, fmt, ocr_words=None):
        calls.append(path)
        return LoadResult(fmt=fmt, loader="fake", pages=1,
                          tables=[], text_lines=[], page_words=[[("w", 0, 0, 1, 1)]])

    monkeypatch.setattr(loaders, "load_document", fake)
    return calls


def _touch(path, mtime_ns: int) -> None:
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_cache_hit_same_object(monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"pdf")
    calls = _fake_loader(monkeypatch)
    r1 = load_document_cached(str(f), "pdf")
    r2 = load_document_cached(str(f), "pdf")
    assert r1 is r2
    assert calls == [str(f)]  # второй вызов — из кэша


def test_cache_invalidate_on_mtime(monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"pdf")
    _touch(f, 1000)
    calls = _fake_loader(monkeypatch)
    load_document_cached(str(f), "pdf")
    _touch(f, 2000)  # файл изменился — кэш сбрасывается
    load_document_cached(str(f), "pdf")
    assert len(calls) == 2


def test_cache_invalidate_on_size(monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"pdf")
    calls = _fake_loader(monkeypatch)
    load_document_cached(str(f), "pdf")
    f.write_bytes(b"pdf-longer-content")  # mtime тот же не гарантирован, но размер другой
    load_document_cached(str(f), "pdf")
    assert len(calls) == 2


def test_cache_bypassed_with_ocr_words(monkeypatch, tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"pdf")
    calls = _fake_loader(monkeypatch)
    ocr = {0: [("w", 0.0, 0.0, 1.0, 1.0, "сл", 0, 0, 0)]}
    r1 = load_document_cached(str(f), "pdf", ocr_words=ocr)
    r2 = load_document_cached(str(f), "pdf", ocr_words=ocr)
    assert r1 is not r2
    assert len(calls) == 2  # OCR-разбор не кэшируется


def test_cache_lru_eviction(monkeypatch, tmp_path):
    calls = _fake_loader(monkeypatch)
    results = []
    for i in range(loaders.CACHE_MAX_DOCUMENTS + 1):
        f = tmp_path / f"doc{i}.pdf"
        f.write_bytes(b"pdf")
        _touch(f, 1000 + i)
        results.append(load_document_cached(str(f), "pdf"))
    # первый документ вытеснен — его повторный запрос парсит заново
    first_again = load_document_cached(str(tmp_path / "doc0.pdf"), "pdf")
    assert first_again is not results[0]
    assert len(calls) == loaders.CACHE_MAX_DOCUMENTS + 2
    # последний ещё в кэше — повторный запрос не парсит
    last_again = load_document_cached(str(tmp_path / f"doc{loaders.CACHE_MAX_DOCUMENTS}.pdf"), "pdf")
    assert last_again is results[-1]
    assert len(calls) == loaders.CACHE_MAX_DOCUMENTS + 2
