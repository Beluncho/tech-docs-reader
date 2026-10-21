"""Конфигурация сервиса из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: _env_str("DATA_DIR", "data"))
    presets_dir: str = field(default_factory=lambda: _env_str("PRESETS_DIR", ""))
    bundled_presets_dir: str = field(default_factory=lambda: _env_str("BUNDLED_PRESETS_DIR", ""))
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 50))
    page_render_zoom: float = field(default_factory=lambda: float(_env_str("PAGE_RENDER_ZOOM", "2.0")))
    ocr_service_url: str = field(default_factory=lambda: _env_str("OCR_SERVICE_URL", ""))
    ocr_auto: bool = field(default_factory=lambda: _env_bool("OCR_AUTO", False))

    # LLM (OpenAI-совместимый контур, например YandexGPT)
    llm_provider: str = field(default_factory=lambda: _env_str("LLM_PROVIDER", "yandexgpt"))
    llm_api_key: str = field(default_factory=lambda: _env_str("LLM_API_KEY", ""))
    yandexgpt_api_key: str = field(default_factory=lambda: _env_str("YANDEXGPT_API_KEY", ""))
    yandexgpt_folder_id: str = field(default_factory=lambda: _env_str("YANDEXGPT_FOLDER_ID", ""))
    yandexgpt_api_base_url: str = field(
        default_factory=lambda: _env_str("YANDEXGPT_API_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    )
    yandexgpt_rule_model: str = field(
        default_factory=lambda: _env_str("YANDEXGPT_RULE_MODEL", "qwen3-235b-a22b-fp8/latest")
    )
    yandexgpt_rule_models: list[str] = field(
        default_factory=lambda: _env_list(
            "YANDEXGPT_RULE_MODELS",
            [
                "qwen3-235b-a22b-fp8/latest",
                "qwen3.6-35b-a3b/latest",
                "deepseek-v4-flash/latest",
                "yandexgpt-5.1/latest",
            ],
        )
    )
    llm_timeout_sec: float = field(default_factory=lambda: float(_env_str("LLM_TIMEOUT_SEC", "120")))
    llm_retries: int = field(default_factory=lambda: _env_int("LLM_RETRIES", 2))
    llm_json_mode: bool = field(default_factory=lambda: _env_bool("LLM_JSON_MODE", False))
    llm_max_fragments: int = field(default_factory=lambda: _env_int("LLM_MAX_FRAGMENTS", 30))

    @property
    def api_key(self) -> str:
        """Общий ключ: сначала LLM_API_KEY, затем специфичный для провайдера."""
        return self.llm_api_key or self.yandexgpt_api_key

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key)

    def preset_dirs(self) -> list[str]:
        dirs = []
        if self.bundled_presets_dir:
            dirs.append(self.bundled_presets_dir)
        if self.presets_dir:
            dirs.append(self.presets_dir)
        return dirs


def load_settings() -> Settings:
    return Settings()
