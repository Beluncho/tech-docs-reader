from __future__ import annotations

import asyncio
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from .capacity import BusyError, PoolDispatcher
from .config import Settings
from .errors import ApiError
from .worker import initialize_worker, recognize_document, worker_probe


class ProcessOCRService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._executor: ProcessPoolExecutor | None = None
        self._dispatcher: PoolDispatcher | None = None

    async def start(self) -> None:
        context = multiprocessing.get_context("spawn")
        self._executor = ProcessPoolExecutor(
            max_workers=self._settings.max_concurrent_processes,
            mp_context=context,
            initializer=initialize_worker,
            initargs=(
                str(self._settings.paddle_model_root),
                self._settings.paddle_cpu_threads,
            ),
        )
        self._dispatcher = PoolDispatcher(
            self._executor,
            max_concurrency=self._settings.max_concurrent_processes,
        )
        try:
            await self._dispatcher.run(worker_probe)
        except Exception:
            await self.close()
            raise

    async def recognize(self, payload: bytes, media_type: str) -> list[dict[str, Any]]:
        if self._dispatcher is None:
            raise RuntimeError("OCR process pool is not running")
        try:
            outcome = await self._dispatcher.run(
                recognize_document,
                payload,
                media_type,
                self._settings.max_pdf_pages,
                self._settings.pdf_dpi,
            )
        except BusyError as exc:
            raise ApiError(
                429,
                "service_busy",
                "Все OCR-процессы заняты",
                {"Retry-After": "1"},
            ) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ApiError(
                500, "processing_failed", "Не удалось распознать документ"
            ) from exc

        error = outcome.get("error")
        if error:
            code = error.get("code", "processing_failed")
            status_code = {
                "bad_request": 400,
                "too_many_pages": 413,
                "processing_failed": 500,
            }.get(code, 500)
            message = str(error.get("message") or "Не удалось распознать документ")
            raise ApiError(status_code, code, message)
        return list(outcome.get("pages", []))

    async def close(self) -> None:
        executor, self._executor = self._executor, None
        self._dispatcher = None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=False)
