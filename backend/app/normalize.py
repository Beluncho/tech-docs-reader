"""Нормализация текста: общая для фингерпринтов, маппинга и evidence."""
from __future__ import annotations

import re
import unicodedata


_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Нижний регистр, дефисные/неразрывные пробелы, схлопывание пробелов."""
    if not text:
        return ""
    text = text.replace("№", " ")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ").replace("\u2011", "-")
    text = text.replace("ё", "е").replace("Ё", "Е")
    return _WS.sub(" ", text).strip().lower()


def normalize_header(text: str) -> str:
    """Заголовок колонки: без пунктуации и служебных символов на краях."""
    t = normalize_text(text)
    t = re.sub(r"^[№\s.\-:;()\[\]«»\"']+|[№\s.\-:;()\[\]«»\"']+$", "", t)
    return t


def collapse_ws(text: str) -> str:
    """Только схлопывание пробелов, без lower-case (для цитат)."""
    if not text:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).replace("\u00a0", " ").strip()


def looks_numeric(text: str) -> bool:
    if not text:
        return False
    t = text.replace(",", ".").replace(" ", "").replace("\u00a0", "")
    return bool(re.fullmatch(r"[+-]?\d+(\.\d+)?", t))