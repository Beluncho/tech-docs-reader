from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DETECTION_MODEL_NAME = "PP-OCRv5_mobile_det"
RECOGNITION_MODEL_NAME = "eslav_PP-OCRv5_mobile_rec"


@dataclass(frozen=True, slots=True)
class PaddleEngineConfig:
    model_root: Path
    cpu_threads: int


def create_paddle_engine(config: PaddleEngineConfig) -> Any:
    detection_dir = config.model_root / DETECTION_MODEL_NAME
    recognition_dir = config.model_root / RECOGNITION_MODEL_NAME
    missing = [path for path in (detection_dir, recognition_dir) if not path.is_dir()]
    if missing:
        raise RuntimeError(
            "Paddle models were not prefetched: "
            + ", ".join(path.name for path in missing)
        )

    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang="ru",
        ocr_version="PP-OCRv5",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name=DETECTION_MODEL_NAME,
        text_detection_model_dir=str(detection_dir),
        text_recognition_model_name=RECOGNITION_MODEL_NAME,
        text_recognition_model_dir=str(recognition_dir),
        cpu_threads=config.cpu_threads,
    )


def adapt_paddle_to_tdr(
    raw_result: Any,
    page_number: int,
    width: int,
    height: int,
    dpi: int,
) -> dict[str, Any]:
    """Преобразует результат PaddleOCR в формат tech-docs-reader.

    Координаты конвертируются из пикселей (DPI-рендер) в пункты PDF,
    страница нумеруется с 0.
    """
    payload = _json_payload(raw_result)
    result = payload.get("res", payload)
    texts = _as_list(result.get("rec_texts"))
    boxes = _as_list(result.get("rec_boxes"))
    polygons = _as_list(result.get("rec_polys"))

    scale = dpi / 72.0
    words: list[dict[str, Any]] = []
    for index, value in enumerate(texts):
        text = str(value)
        if not text:
            continue
        source_box = boxes[index] if index < len(boxes) else None
        source_polygon = polygons[index] if index < len(polygons) else None
        x_min, y_min, x_max, y_max = _bbox(source_box, source_polygon, width, height)
        words.append(
            {
                "x0": round(x_min / scale, 2),
                "y0": round(y_min / scale, 2),
                "x1": round(x_max / scale, 2),
                "y1": round(y_max / scale, 2),
                "text": text,
            }
        )

    return {
        "page": page_number - 1,
        "words": words,
    }


def _json_payload(raw_result: Any) -> dict[str, Any]:
    value = getattr(raw_result, "json", raw_result)
    if callable(value):
        value = value()
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _bbox(
    box: Any,
    polygon: Any,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    coordinates = _as_list(box)
    if len(coordinates) >= 4 and all(not isinstance(item, (list, tuple)) for item in coordinates[:4]):
        x_min, y_min, x_max, y_max = coordinates[:4]
    else:
        points = _as_list(polygon)
        normalized_points = [_as_list(point) for point in points]
        valid_points = [point for point in normalized_points if len(point) >= 2]
        if not valid_points:
            return (0, 0, width, height)
        x_values = [point[0] for point in valid_points]
        y_values = [point[1] for point in valid_points]
        x_min, y_min, x_max, y_max = (
            min(x_values),
            min(y_values),
            max(x_values),
            max(y_values),
        )
    return (
        _pixel(x_min, width),
        _pixel(y_min, height),
        _pixel(x_max, width),
        _pixel(y_max, height),
    )


def _pixel(value: Any, upper_bound: int) -> int:
    try:
        pixel = round(float(value))
    except (TypeError, ValueError):
        return 0
    return min(upper_bound, max(0, pixel))
