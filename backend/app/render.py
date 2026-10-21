"""Рендер страниц PDF в PNG для превью."""
from __future__ import annotations

import fitz


def render_page(path: str, page_index: int, zoom: float = 2.0):
    doc = fitz.open(path)
    try:
        page = doc[page_index]
        pdf_w, pdf_h = page.rect.width, page.rect.height
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return pix.tobytes("png"), pix.width, pix.height, pdf_w, pdf_h
    finally:
        doc.close()