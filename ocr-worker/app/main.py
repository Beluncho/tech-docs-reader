from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import PurePath
from typing import Any, Protocol

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.formparsers import MultiPartParser

from .config import Settings
from .errors import ApiError
from .service import ProcessOCRService


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class OCRService(Protocol):
    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def recognize(
        self, payload: bytes, media_type: str
    ) -> list[dict[str, Any]]: ...


ServiceFactory = Callable[[Settings], OCRService]


def create_app(
    *,
    settings: Settings | None = None,
    service_factory: ServiceFactory = ProcessOCRService,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        active_settings = settings or Settings.from_env()
        service = service_factory(active_settings)
        application.state.settings = active_settings
        application.state.ocr_service = service
        previous_spool_limit = MultiPartParser.spool_max_size
        MultiPartParser.spool_max_size = active_settings.max_upload_bytes + 1
        try:
            await service.start()
            try:
                yield
            finally:
                await service.close()
        finally:
            MultiPartParser.spool_max_size = previous_spool_limit

    application = FastAPI(
        title="Tech Docs Reader OCR Worker",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.exception_handler(ApiError)
    async def api_error_handler(_request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": error.message}},
            headers=error.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "bad_request",
                    "message": "Необходимо передать файл",
                }
            },
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "engine": "paddle"}

    @application.post("/ocr")
    async def ocr(request: Request, file: UploadFile | None = File(default=None)) -> dict[str, Any]:
        if file is None:
            raise ApiError(400, "bad_request", "Необходимо передать файл")
        active_settings: Settings = request.app.state.settings
        try:
            payload = await _read_upload(file, active_settings.max_upload_bytes)
        finally:
            await file.close()
        media_type = _detect_media_type(payload)
        source_hash = hashlib.sha256(payload).hexdigest()
        started = time.perf_counter()
        try:
            pages = await request.app.state.ocr_service.recognize(payload, media_type)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(
                500, "processing_failed", "Не удалось распознать документ"
            ) from exc
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return {
            "page_count": len(pages),
            "duration_ms": duration_ms,
            "pages": pages,
        }

    return application


async def _read_upload(file: UploadFile, maximum: int) -> bytes:
    output = bytearray()
    while True:
        chunk = await file.read(min(1024 * 1024, maximum + 1 - len(output)))
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > maximum:
            raise ApiError(413, "file_too_large", "Превышен допустимый размер файла")
    if not output:
        raise ApiError(400, "bad_request", "Файл пуст")
    return bytes(output)


def _detect_media_type(payload: bytes) -> str:
    if payload.startswith(PNG_SIGNATURE):
        return "image/png"
    if b"%PDF-" in payload[:1024]:
        return "application/pdf"
    raise ApiError(
        415, "unsupported_media", "Поддерживаются только PDF и PNG"
    )


app = create_app()
