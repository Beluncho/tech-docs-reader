"""Хранилище: документы (файл + метаданные + превью) и наборы (JSON)."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings


def _slug(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9а-яё\-_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60] or "preset"


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.data_dir = Path(settings.data_dir)
        self.documents_dir = self.data_dir / "documents"
        self.presets_user_dir = (
            Path(settings.presets_dir) if settings.presets_dir else self.data_dir / "presets"
        )
        self.bundled_dir = (
            Path(settings.bundled_presets_dir)
            if settings.bundled_presets_dir
            else Path(__file__).resolve().parent / "presets"
        )
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.presets_user_dir.mkdir(parents=True, exist_ok=True)
        self.mappings_dir = self.data_dir / "mappings"
        self.mappings_dir.mkdir(parents=True, exist_ok=True)

    # --- документы ----------------------------------------------------------

    def new_document_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def document_dir(self, doc_id: str) -> Path:
        return self.documents_dir / doc_id

    def original_path(self, doc_id: str) -> Path:
        meta = self.load_document_meta(doc_id)
        return self.document_dir(doc_id) / f"original{meta.get('ext', '')}"

    def save_original(self, doc_id: str, filename: str, data: bytes) -> Path:
        d = self.document_dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower()
        path = d / f"original{ext}"
        path.write_bytes(data)
        return path

    def save_document_meta(self, doc_id: str, meta: dict) -> None:
        d = self.document_dir(doc_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_document_meta(self, doc_id: str) -> dict:
        p = self.document_dir(doc_id) / "meta.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def list_documents(self) -> list[dict]:
        out = []
        for d in self.documents_dir.iterdir():
            meta = self.load_document_meta(d.name)
            if meta:
                meta["id"] = d.name
                out.append(meta)
        out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return out

    def pages_dir(self, doc_id: str) -> Path:
        p = self.document_dir(doc_id) / "pages"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def render_page_cached(self, doc_id: str, page_index: int, pdf_path: Path, zoom: float) -> tuple[bytes, dict]:
        from app.render import render_page

        png = self.pages_dir(doc_id) / f"p{page_index}.png"
        info = self.pages_dir(doc_id) / f"p{page_index}.json"
        if png.exists() and info.exists():
            return png.read_bytes(), json.loads(info.read_text(encoding="utf-8"))
        data, w, h, pw, ph = render_page(str(pdf_path), page_index, zoom)
        png.write_bytes(data)
        info.write_text(json.dumps({"width": w, "height": h, "pdf_width": pw, "pdf_height": ph}, ensure_ascii=False), encoding="utf-8")
        return data, json.loads(info.read_text(encoding="utf-8"))

    # --- наборы -------------------------------------------------------------

    def list_presets(self) -> list[dict]:
        presets: list[dict] = []
        if self.bundled_dir.exists():
            for f in sorted(self.bundled_dir.glob("*.json")):
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                    p["id"] = p.get("id") or f.stem
                    p["builtin"] = True
                    presets.append(p)
                except (json.JSONDecodeError, OSError):
                    continue
        if self.presets_user_dir != self.bundled_dir and self.presets_user_dir.exists():
            for f in sorted(self.presets_user_dir.glob("*.json")):
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                    p["id"] = p.get("id") or f.stem
                    p["builtin"] = False
                    presets.append(p)
                except (json.JSONDecodeError, OSError):
                    continue
        return presets

    def get_preset(self, preset_id: str) -> dict | None:
        for p in self.list_presets():
            if p["id"] == preset_id:
                return p
        return None

    def save_preset(self, data: dict) -> str:
        preset_id = data.get("id") or _slug(data.get("name", "preset"))
        data["id"] = preset_id
        path = self.presets_user_dir / f"{preset_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return preset_id

    def delete_preset(self, preset_id: str) -> bool:
        path = self.presets_user_dir / f"{preset_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- шаблоны маппинга (этап 3.7) -----------------------------------------

    def list_mappings(self) -> list[dict]:
        out: list[dict] = []
        if self.mappings_dir.exists():
            for f in sorted(self.mappings_dir.glob("*.json")):
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return out

    def get_mapping(self, mapping_id: str) -> dict | None:
        return next((m for m in self.list_mappings() if m.get("id") == mapping_id), None)

    def save_mapping(self, data: dict) -> str:
        mapping_id = data.get("id") or _slug(data.get("name", "mapping"))
        data["id"] = mapping_id
        if not data.get("created_at"):
            data["created_at"] = datetime.now(timezone.utc).isoformat()
        path = self.mappings_dir / f"{mapping_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return mapping_id

    def delete_mapping(self, mapping_id: str) -> bool:
        path = self.mappings_dir / f"{mapping_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # --- лог коррекций LLM-маппинга (этап 4.6) -------------------------------

    def log_llm_feedback(self, entries: list[dict]) -> int:
        """Дописать коррекции пользователя в JSONL-журнал data/llm_feedback.jsonl
        (по строке на коррекцию; анализ — тюнинг промптов в 4.8)."""
        if not entries:
            return 0
        path = self.data_dir / "llm_feedback.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return len(entries)

    def load_llm_feedback(self) -> list[dict]:
        """Прочитать журнал коррекций (пустой/битые строки пропускаются)."""
        path = self.data_dir / "llm_feedback.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # --- кэш и метрики LLM-маппинга (этап 4.7) --------------------------------

    LLM_SUGGEST_CACHE_LIMIT = 200  # записей; самая старая вытесняется
    LLM_STATS_FILE = "llm_stats.json"

    def _llm_suggest_cache_path(self) -> Path:
        return self.data_dir / "llm_suggest_cache.json"

    def get_llm_suggest_cache(self, key: str) -> dict | None:
        """Сохранённый ответ LLM по фингерпринту запроса (None — промах)."""
        path = self._llm_suggest_cache_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return data.get(key)

    def save_llm_suggest_cache(self, key: str, result: dict) -> None:
        """Сохранить ответ; хранится не больше LLM_SUGGEST_CACHE_LIMIT последних."""
        path = self._llm_suggest_cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (json.JSONDecodeError, OSError):
            data = {}
        data[key] = result
        if len(data) > self.LLM_SUGGEST_CACHE_LIMIT:
            # порядок вставки Python-словаря: старые ключи впереди — их и выбрасываем
            for old in list(data)[: len(data) - self.LLM_SUGGEST_CACHE_LIMIT]:
                del data[old]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def log_llm_stats(
        self,
        *,
        cache_hit: bool,
        ok: bool,
        processing_ms: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Дописать счётчики запросов LLM-маппинга в data/llm_stats.json."""
        path = self.data_dir / self.LLM_STATS_FILE
        try:
            stats = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (json.JSONDecodeError, OSError):
            stats = {}
        stats["requests"] = stats.get("requests", 0) + 1
        if cache_hit:
            stats["cache_hits"] = stats.get("cache_hits", 0) + 1
        if not ok:
            stats["failures"] = stats.get("failures", 0) + 1
        stats["total_time_ms"] = stats.get("total_time_ms", 0) + max(0, int(processing_ms))
        stats["prompt_tokens"] = stats.get("prompt_tokens", 0) + max(0, int(prompt_tokens))
        stats["completion_tokens"] = stats.get("completion_tokens", 0) + max(0, int(completion_tokens))
        stats.setdefault("cache_hits", 0)
        stats.setdefault("failures", 0)
        stats.setdefault("total_time_ms", 0)
        stats.setdefault("prompt_tokens", 0)
        stats.setdefault("completion_tokens", 0)
        path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_llm_stats(self) -> dict:
        """Счётчики запросов LLM-маппинга (пустые — нули)."""
        path = self.data_dir / self.LLM_STATS_FILE
        if not path.exists():
            return {}
        try:
            stats = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return stats if isinstance(stats, dict) else {}