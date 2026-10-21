"""Общие типы для загрузчиков."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.preset import Table


@dataclass
class LoadResult:
    fmt: str
    loader: str
    pages: int
    tables: list[Table] = field(default_factory=list)
    text_lines: list[str] = field(default_factory=list)
    page_words: list[list[tuple]] = field(default_factory=list)