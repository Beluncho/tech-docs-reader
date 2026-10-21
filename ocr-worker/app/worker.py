from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from .paddle_adapter import PaddleEngineConfig, adapt_paddle_to_tdr, create_paddle_engine


_ENGINE: Any | None = None


@dataclass(frozen=True, slots=True)
class RenderedPage:
    page_number: int
    width: int
    height: int
    image: Any


class DocumentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def initialize_worker(model_root: str, cpu_threads: int) -> None:
    global _ENGINE
    _ENGINE = create_paddle_engine(
        PaddleEngineConfig(model_root=Path(model_root), cpu_threads=cpu_threads)
    )


def worker_probe() -> int:
    if _ENGINE is None:
        raise RuntimeError("PaddleOCR worker was not initialized")
    return os.getpid()


def render_document(
    payload: bytes,
    *,
    media_type: str,
    max_pdf_pages: int,
    pdf_dpi: int,
) -> list[RenderedPage]:
    return list(
        iter_rendered_pages(
            payload,
            media_type=media_type,
            max_pdf_pages=max_pdf_pages,
            pdf_dpi=pdf_dpi,
        )
    )


def iter_rendered_pages(
    payload: bytes,
    *,
    media_type: str,
    max_pdf_pages: int,
    pdf_dpi: int,
) -> Iterator[RenderedPage]:
    if media_type == "image/png":
        yield _render_png(payload)
        return
    if media_type == "application/pdf":
        yield from _iter_pdf(
            payload, max_pdf_pages=max_pdf_pages, pdf_dpi=pdf_dpi
        )
        return
    raise DocumentError("bad_request", "Неподдерживаемый тип документа")


def recognize_document(
    payload: bytes,
    media_type: str,
    max_pdf_pages: int,
    pdf_dpi: int,
) -> dict[str, Any]:
    try:
        if _ENGINE is None:
            raise RuntimeError("PaddleOCR worker was not initialized")
        rendered_pages = iter_rendered_pages(
            payload,
            media_type=media_type,
            max_pdf_pages=max_pdf_pages,
            pdf_dpi=pdf_dpi,
        )
        pages = []
        for rendered in rendered_pages:
            results = list(_ENGINE.predict(rendered.image))
            raw_result = results[0] if results else {}
            pages.append(
                adapt_paddle_to_tdr(
                    raw_result,
                    rendered.page_number,
                    rendered.width,
                    rendered.height,
                    pdf_dpi,
                )
            )
        return {"pages": pages, "error": None}
    except DocumentError as exc:
        return {
            "pages": [],
            "error": {"code": exc.code, "message": exc.message},
        }
    except Exception:
        return {
            "pages": [],
            "error": {
                "code": "processing_failed",
                "message": "Не удалось распознать документ",
            },
        }


def _render_png(payload: bytes) -> RenderedPage:
    try:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(payload)) as source:
            source.load()
            image = np.array(source.convert("RGB"), copy=True)
    except Exception as exc:
        raise DocumentError(
            "bad_request", "Документ пуст, повреждён или зашифрован"
        ) from exc
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        raise DocumentError("bad_request", "Документ не содержит изображения")
    return RenderedPage(page_number=1, width=width, height=height, image=image)


def _render_pdf(
    payload: bytes,
    *,
    max_pdf_pages: int,
    pdf_dpi: int,
) -> list[RenderedPage]:
    return list(
        _iter_pdf(
            payload,
            max_pdf_pages=max_pdf_pages,
            pdf_dpi=pdf_dpi,
        )
    )


def _iter_pdf(
    payload: bytes,
    *,
    max_pdf_pages: int,
    pdf_dpi: int,
) -> Iterator[RenderedPage]:
    document = None
    try:
        import numpy as np
        import pypdfium2

        document = pypdfium2.PdfDocument(payload)
        page_count = len(document)
        if page_count <= 0:
            raise DocumentError("bad_request", "PDF не содержит страниц")
        if page_count > max_pdf_pages:
            raise DocumentError(
                "too_many_pages", "Превышено допустимое число страниц"
            )

        for index in range(page_count):
            page = document[index]
            bitmap = None
            try:
                bitmap = page.render(scale=pdf_dpi / 72.0)
                image = np.array(bitmap.to_pil().convert("RGB"), copy=True)
                height, width = image.shape[:2]
                yield RenderedPage(
                    page_number=index + 1,
                    width=width,
                    height=height,
                    image=image,
                )
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(
            "bad_request", "Документ пуст, повреждён или зашифрован"
        ) from exc
    finally:
        if document is not None:
            document.close()
