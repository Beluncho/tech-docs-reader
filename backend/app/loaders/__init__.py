"""Загрузчики документов в единую grid-модель."""
from __future__ import annotations

import os
from collections import OrderedDict

from app.loaders._base import LoadResult
from app.loaders import csv_loader, docx_loader, pdf_loader, txt_loader, xlsx_loader
from app.preset import Table

SUPPORTED = {
    "xlsx": "xlsx",
    "xls": "xlsx",
    "docx": "docx",
    "csv": "csv",
    "txt": "txt",
    "pdf": "pdf",
}

# сколько последних документов держим в кэше разбора (подэтап 2.9):
# повторные запросы к тому же файлу (extract после upload) не парсят его заново
CACHE_MAX_DOCUMENTS = 8
_LOAD_CACHE: "OrderedDict[tuple, LoadResult]" = OrderedDict()


def load_document(path: str, fmt: str, ocr_words: dict[int, list[tuple]] | None = None) -> LoadResult:
    if fmt in {"xlsx", "xls"}:
        return xlsx_loader.load(path, fmt)
    if fmt == "docx":
        return docx_loader.load(path)
    if fmt == "csv":
        return csv_loader.load(path)
    if fmt == "txt":
        return txt_loader.load(path)
    if fmt == "pdf":
        return pdf_loader.load(path, ocr_words=ocr_words)
    raise ValueError(f"unsupported format: {fmt}")


def load_document_cached(path: str, fmt: str, ocr_words: dict[int, list[tuple]] | None = None) -> LoadResult:
    """load_document с LRU-кэшем по (путь, формат, mtime, размер).

    Изменение файла (mtime/size) автоматически сбрасывает кэш. Запросы с
    ocr_words не кэшируются — результат зависит от внешнего OCR-сервиса.

    Возвращённый LoadResult разделяется между вызовами: потребители
    (extract/match/сериализация) таблицы только читают, мутировать нельзя.
    """
    if ocr_words is not None:
        return load_document(path, fmt, ocr_words=ocr_words)
    st = os.stat(path)
    key = (os.path.abspath(path), fmt, st.st_mtime_ns, st.st_size)
    hit = _LOAD_CACHE.pop(key, None)
    if hit is not None:
        _LOAD_CACHE[key] = hit  # обновить позицию в LRU
        return hit
    result = load_document(path, fmt)
    _LOAD_CACHE[key] = result
    if len(_LOAD_CACHE) > CACHE_MAX_DOCUMENTS:
        _LOAD_CACHE.popitem(last=False)
    return result