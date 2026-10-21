"""LLM-контур: OpenAI-совместимый вызов (YandexGPT и др.) с проверкой цитат."""
from __future__ import annotations

import json
import time

import httpx

from app.config import Settings
from app.loaders._base import LoadResult
from app.models import Evidence, Parameter
from app.normalize import collapse_ws, normalize_text


class LLMError(Exception):
    pass


SYSTEM_PROMPT = (
    "Ты извлекаешь технические характеристики из фрагментов документов (паспорта изделий, "
    "сертификаты, ТЗ). Фрагменты даны списком с id. Верни строго JSON без пояснений в формате: "
    '{"parameters":[{"name":"...","value":"...","unit":"...","fragment_id":"...","quote":"..."}]}. '
    "quote — точная цитата из фрагмента, содержащая значение. Если значение в фрагменте не найдено — "
    "не выдумывай, пропусти параметр. name — короткое название характеристики без единиц."
)


def preset_guidance(preset: dict | None) -> str | None:
    """Подсказка LLM из набора (маппинга): какие характеристики ищем в первую очередь."""
    if not preset:
        return None
    labels = [collapse_ws(x) for x in preset.get("labels", []) if collapse_ws(x)]
    if not labels:
        return None
    return (
        "Ниже словарь характеристик, которые в первую очередь нужно найти "
        "(это подсказка, а не ограничение — другие характеристики тоже можно, "
        "если они точно есть в тексте): " + ", ".join(labels[:80]) + "."
    )


def reorder_table_first(fragments: list[dict], table_id: str | None) -> list[dict]:
    """Фрагмент таблицы из маппинга — первым, чтобы LLM смотрел на него в приоритете."""
    if not table_id or not fragments:
        return fragments
    frags = list(fragments)
    idx = next((i for i, f in enumerate(frags) if f.get("id") == table_id), None)
    if idx is None or idx == 0:
        return frags
    frag = frags.pop(idx)
    return [frag] + frags


def build_fragments(load_result: LoadResult, max_fragments: int = 30) -> list[dict]:
    frags: list[dict] = []
    for t in load_result.tables:
        text = "\n".join(" | ".join(c.text for c in row) for row in t.rows)
        if text.strip():
            frags.append({"id": t.id, "page": t.page, "text": text})
    if load_result.text_lines and len(frags) < max_fragments:
        chunk: list[str] = []
        size = 0
        for ln in load_result.text_lines:
            if chunk and size + len(ln) > 2200:
                frags.append({"id": f"lines{len(frags)}", "page": None, "text": "\n".join(chunk)})
                chunk, size = [], 0
            chunk.append(ln)
            size += len(ln)
        if chunk:
            frags.append({"id": f"lines{len(frags)}", "page": None, "text": "\n".join(chunk)})
    return frags[:max_fragments]


def _resolve_model_uri(settings: Settings, model: str) -> str:
    # YandexGPT (OpenAI-compatible) requires full URI: gpt://<folder_id>/<model>
    if model.startswith("gpt://") or not settings.yandexgpt_folder_id:
        return model
    return f"gpt://{settings.yandexgpt_folder_id}/{model.lstrip('/')}"


def chat_completions(
    settings: Settings,
    model: str,
    messages: list[dict],
    attempt: int = 0,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> dict:
    """OpenAI-совместимый вызов. Используется извлечением и LLM-маппингом (этап 4)."""
    url = f"{settings.yandexgpt_api_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    if settings.yandexgpt_folder_id:
        headers["X-Folder-Id"] = settings.yandexgpt_folder_id
        headers["OpenAI-Project"] = settings.yandexgpt_folder_id
    body: dict = {
        "model": _resolve_model_uri(settings, model),
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if settings.llm_json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=settings.llm_timeout_sec)
    except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as exc:
        # DNS/network flakiness: retry a few times before giving up
        if attempt < settings.llm_retries:
            time.sleep(1.0 + attempt)
            return chat_completions(
                settings, model, messages, attempt + 1, temperature, max_tokens
            )
        raise
    resp.raise_for_status()
    return resp.json()


def _parse_parameters(content: str) -> list[dict]:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: cut surrounding prose if the model returned extra text
        start, end = content.find("{"), content.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(content[start : end + 1])
    if isinstance(data, dict) and "parameters" in data:
        return data["parameters"]
    if isinstance(data, list):
        return data
    return []


def _validate(raw: list[dict], frags_by_id: dict[str, dict], doc_name: str) -> list[Parameter]:
    out: list[Parameter] = []
    for p in raw:
        frag = frags_by_id.get(str(p.get("fragment_id", "")))
        if frag is None:
            continue
        quote = collapse_ws(str(p.get("quote", "")))
        if not quote:
            continue
        if normalize_text(quote) not in normalize_text(frag["text"]):
            continue
        name = collapse_ws(str(p.get("name", "")))
        value = collapse_ws(str(p.get("value", "")))
        if not name or not value:
            continue
        out.append(
            Parameter(
                name=name,
                value=value,
                unit=(collapse_ws(str(p["unit"])) if p.get("unit") else None),
                evidence=Evidence(
                    document=doc_name,
                    page=(frag["page"] + 1) if frag.get("page") is not None else None,
                    quote=quote,
                    fragment=frag["id"],
                ),
                source="llm",
                verified=True,
            )
        )
    return out


def extract_with_llm(
    load_result: LoadResult,
    settings: Settings,
    doc_name: str,
    preset: dict | None = None,
    table_id: str | None = None,
) -> tuple[list[Parameter], str]:
    """Вернуть (параметры, модель). Ошибки накапливаются, кидаем LLMError в конце."""
    if not settings.llm_enabled:
        raise LLMError("LLM не настроен: укажите YANDEXGPT_API_KEY (или LLM_API_KEY)")
    fragments = reorder_table_first(
        build_fragments(load_result, settings.llm_max_fragments), table_id
    )
    if not fragments:
        raise LLMError("Нет фрагментов для LLM — документ пуст")
    frags_by_id = {f["id"]: f for f in fragments}
    user_payload = json.dumps(
        [
            {"id": f["id"], "page": f["page"], "text": f["text"]}
            for f in fragments
        ],
        ensure_ascii=False,
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    hint = preset_guidance(preset)
    if hint:
        messages.append({"role": "system", "content": hint})
    messages.append({"role": "user", "content": f"Фрагменты:\n{user_payload}"})
    models = [settings.yandexgpt_rule_model] + [
        m for m in settings.yandexgpt_rule_models if m != settings.yandexgpt_rule_model
    ]
    errors: list[str] = []
    for model in models:
        try:
            data = chat_completions(settings, model, messages)
            content = data["choices"][0]["message"]["content"]
            raw = _parse_parameters(content)
            params = _validate(raw, frags_by_id, doc_name)
            if params:
                return params, model
            errors.append(f"{model}: пустой результат после проверки цитат")
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            detail = str(exc)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                snippet = " ".join(exc.response.text.split())[:300]
                if snippet:
                    detail = f"{detail} | {snippet}"
            errors.append(f"{model}: {detail}")
    raise LLMError("; ".join(errors))
