"""Точка входа FastAPI."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import load_settings

# Логирование (5.7): единый формат с меткой времени/уровнем/именем логгера.
# Уровень — LOG_LEVEL (INFO по умолчанию). uvicorn пишет свои логи доступа
# отдельными логгерами ("uvicorn.*") и этой настройкой не затрагивается.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings = load_settings()
app = FastAPI(title="Tech Docs Reader", version="0.1.0", docs_url="/api/docs")
app.include_router(router)

# Статика (для локальной разработки; в docker статику отдаёт nginx)
_frontend = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend.exists():
    app.mount("/ui", StaticFiles(directory=str(_frontend), html=True), name="ui")


@app.get("/")
def index():
    return {"service": "tech-docs-reader", "docs": "/api/docs", "ui": "/ui/"}