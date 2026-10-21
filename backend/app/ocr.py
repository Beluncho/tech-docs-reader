"""OCR-адаптер: HTTP-сервис (штатно, как PADDLEOCR_BASE_URL) или локальный движок."""
from __future__ import annotations

import io
import os

import fitz
import httpx


class OCRNotConfigured(Exception):
    pass


def _local_engine():
    try:
        from paddleocr import PaddleOCR

        return ("paddle", PaddleOCR(use_angle_cls=True, lang="ru", show_log=False))
    except Exception:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR

        return ("rapid", RapidOCR())
    except Exception:
        pass
    return None


def ocr_pdf(pdf_path: str, ocr_service_url: str = "", timeout: float = 300.0) -> dict[int, list[tuple]]:
    """Распознать сканированный PDF → {page: [(x0, y0, x1, y1, text), ...]}."""
    if ocr_service_url:
        return _ocr_via_http(pdf_path, ocr_service_url, timeout)
    engine = _local_engine()
    if engine is None:
        raise OCRNotConfigured(
            "OCR не настроен: задайте OCR_SERVICE_URL или установите локальный движок (paddleocr/rapidocr)"
        )
    return _ocr_local(pdf_path, engine)


def _ocr_via_http(pdf_path: str, service_url: str, timeout: float) -> dict[int, list[tuple]]:
    with open(pdf_path, "rb") as fh:
        resp = httpx.post(
            f"{service_url.rstrip('/')}/ocr",
            files={"file": (os.path.basename(pdf_path), fh, "application/pdf")},
            timeout=timeout,
        )
    resp.raise_for_status()
    data = resp.json()
    out: dict[int, list[tuple]] = {}
    for page in data.get("pages", []):
        words = []
        for w in page.get("words", []):
            words.append((float(w["x0"]), float(w["y0"]), float(w["x1"]), float(w["y1"]), w["text"]))
        out[int(page["page"])] = words
    return out


def _ocr_local(pdf_path: str, engine) -> dict[int, list[tuple]]:
    kind, ocr = engine
    doc = fitz.open(pdf_path)
    out: dict[int, list[tuple]] = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        pix = page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5))
        img_bytes = pix.tobytes("png")
        words = []
        try:
            if kind == "paddle":
                res = ocr.ocr(io.BytesIO(img_bytes))
                for line in res or []:
                    for box, (text, _conf) in line:
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        words.append((min(xs) / 2.5, min(ys) / 2.5, max(xs) / 2.5, max(ys) / 2.5, text))
            else:
                result = ocr(img_bytes)
                if result and result[0]:
                    for box, text, _conf in result[0]:
                        xs = [p[0] for p in box]
                        ys = [p[1] for p in box]
                        words.append((min(xs) / 2.5, min(ys) / 2.5, max(xs) / 2.5, max(ys) / 2.5, text))
        except Exception:
            continue
        if words:
            out[pno] = words
    doc.close()
    return out