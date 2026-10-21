"""Загрузчик TXT: строки текста для key:value-извлечения."""
from __future__ import annotations

from app.loaders._base import LoadResult
from app.normalize import collapse_ws


def load(path: str) -> LoadResult:
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "cp1251", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    lines = [collapse_ws(ln) for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return LoadResult(fmt="txt", loader="txt", pages=1, text_lines=lines)