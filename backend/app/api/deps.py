"""Синглтоны настроек и хранилища."""
from __future__ import annotations

from app.config import Settings, load_settings
from app.storage import Storage

settings: Settings = load_settings()
storage: Storage = Storage(settings)