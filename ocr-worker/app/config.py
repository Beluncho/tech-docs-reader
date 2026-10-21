from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Settings:
    service_port: int = 8300
    max_concurrent_processes: int = 2
    max_upload_bytes: int = 52_428_800
    max_pdf_pages: int = 100
    pdf_dpi: int = 200
    paddle_model_root: Path = Path("/opt/paddlex-cache/official_models")
    paddle_cpu_threads: int = 2

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if environ is None else environ

        return cls(
            service_port=_integer(values, "SERVICE_PORT", 8300, 1, 65_535),
            max_concurrent_processes=_integer(
                values, "MAX_CONCURRENT_PROCESSES", 2, 1, 128
            ),
            max_upload_bytes=_integer(
                values, "MAX_UPLOAD_BYTES", 52_428_800, 1, 2_147_483_647
            ),
            max_pdf_pages=_integer(values, "MAX_PDF_PAGES", 100, 1, 10_000),
            pdf_dpi=_integer(values, "PDF_DPI", 200, 72, 600),
            paddle_model_root=Path(
                values.get(
                    "PADDLE_MODEL_ROOT", "/opt/paddlex-cache/official_models"
                )
            ),
            paddle_cpu_threads=_integer(values, "PADDLE_CPU_THREADS", 2, 1, 256),
        )


def _integer(
    environ: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value
