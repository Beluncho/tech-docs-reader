"""Загрузчик PDF: текстовый слой через PyMuPDF + детекция таблиц."""
from __future__ import annotations

import fitz

from app.detect import _group_lines, detect_tables_from_grid, detect_tables_on_page, merge_page_tables
from app.loaders._base import LoadResult, Table

MIN_TEXT_CHARS = 40


def _page_words(page) -> list[tuple]:
    return [w for w in page.get_text("words") if w[4].strip()]


def _lines_from_words(words: list[tuple]) -> list[str]:
    lines = _group_lines(words)
    out = []
    for line in lines:
        line.sort(key=lambda w: w[0])
        out.append(" ".join(w[4] for w in line))
    return out


def load(path: str, ocr_words: dict[int, list[tuple]] | None = None) -> LoadResult:
    doc = fitz.open(path)
    page_count = doc.page_count
    tables: list[Table] = []
    text_lines: list[str] = []
    page_words: list[list[tuple]] = []
    used_ocr = False
    for pno in range(page_count):
        page = doc[pno]
        words = _page_words(page)
        text_chars = sum(len(w[4]) for w in words)
        if text_chars < MIN_TEXT_CHARS and ocr_words and pno in ocr_words:
            ocr_list = [tuple(w) for w in ocr_words[pno]]
            if sum(len(w[4]) for w in ocr_list) > text_chars:
                words = ocr_list
                used_ocr = True
        page_words.append(words)
        if not words:
            continue
        # ПЛАН-1 (5.3.3): словесный детектор + сеточный, объединённые консервативным
        # merge_page_tables (grid-дубли словесных таблиц отбрасываются, куски слов,
        # покрытые оставшейся сеткой, убираются). Регресс по контрольным кейсам — scanB.
        word = detect_tables_on_page(words, pno)
        grid = detect_tables_from_grid(words, _page_rects(page), pno)
        merged = merge_page_tables(pno, grid, word)
        tables.extend(merged)
        text_lines.extend(_lines_from_words(words))
    doc.close()
    return LoadResult(fmt="pdf", loader="pdf_ocr" if used_ocr else "pdf", pages=page_count, tables=tables, text_lines=text_lines, page_words=page_words)
def _page_rects(page):
    """Border segments of the page: thin drawing rects and lines."""
    rects = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "re":
                r = item[1]
                rects.append((r.x0, r.y0, r.x1, r.y1))
            elif item[0] == "l":
                p1, p2 = item[1], item[2]
                rects.append(
                    (min(p1.x, p2.x), min(p1.y, p2.y), max(p1.x, p2.x), max(p1.y, p2.y))
                )
    return rects
