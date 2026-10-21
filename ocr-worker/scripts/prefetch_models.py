#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from app.paddle_adapter import DETECTION_MODEL_NAME, RECOGNITION_MODEL_NAME


EngineFactory = Callable[..., Any]


def prefetch_models(
    model_root: Path,
    *,
    engine_factory: EngineFactory | None = None,
) -> Path:
    model_root = model_root.resolve()
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(model_root.parent)
    if engine_factory is None:
        from paddleocr import PaddleOCR

        engine_factory = PaddleOCR

    engine_factory(
        lang="ru",
        ocr_version="PP-OCRv5",
        device="cpu",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        text_detection_model_name=DETECTION_MODEL_NAME,
        text_recognition_model_name=RECOGNITION_MODEL_NAME,
        cpu_threads=1,
    )

    required_models = [DETECTION_MODEL_NAME, RECOGNITION_MODEL_NAME]
    missing = []
    for model_name in required_models:
        directory = model_root / model_name
        if not directory.is_dir() or not any(directory.iterdir()):
            missing.append(model_name)
    if missing:
        raise RuntimeError(
            "Paddle model prefetch did not materialize: " + ", ".join(missing)
        )

    manifest = model_root / ".prefetched.json"
    manifest.write_text(
        json.dumps(
            {
                "language": "ru",
                "ocr_version": "PP-OCRv5",
                "models": required_models,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    model_root = Path(
        os.environ.get(
            "PADDLE_MODEL_ROOT", "/opt/paddlex-cache/official_models"
        )
    )
    prefetch_models(model_root)


if __name__ == "__main__":
    main()
