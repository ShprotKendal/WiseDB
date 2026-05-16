"""Клиент OpenAI-совместимого API для LLM-фазы sleep (оптимизация чанков)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def _chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n...[обрезано]"


def optimize_sleep_chunks(
    *,
    api_key: str,
    base_url: str,
    model: str,
    chunks: list[dict[str, Any]],
    max_actions: int,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Запрос к OpenAI-совместимой модели: осторожные действия update/split по чанкам."""
    compact_chunks = [
        {
            "id": c["id"],
            "title": _truncate(str(c.get("title", "")), 180),
            "body": _truncate(str(c.get("body", "")), 3500),
            "tags": list(c.get("tags", []))[:12],
            "metadata": {
                k: v
                for k, v in dict(c.get("metadata", {})).items()
                if str(k).startswith("wisedb_") or str(k) in {"source", "kind", "topic"}
            },
        }
        for c in chunks
    ]
    schema = {
        "actions": [
            {
                "type": "update",
                "chunk_id": "<существующий uuid>",
                "title": "более ясный заголовок",
                "body": "сжатый самодостаточный текст",
                "tags": ["нормализованные", "теги"],
                "metadata": {"optional": "небольшие поля"},
                "reason": "зачем это улучшает поиск",
            },
            {
                "type": "split",
                "chunk_id": "<существующий uuid>",
                "chunks": [
                    {
                        "title": "атомарная подтема",
                        "body": "самодостаточное тело чанка",
                        "tags": ["подтема"],
                        "metadata": {"optional": "небольшие поля"},
                    }
                ],
                "reason": "почему чанк стоит разбить",
            },
        ]
    }
    payload = {
        "model": model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты оптимизируешь семантический граф памяти. Верни ТОЛЬКО валидный JSON. "
                    "Будь осторожен: не искажай факты, не выдумывай новые; если чанк уже атомарен — "
                    "лучше верни пустой список действий. "
                    "Используй type=update для ясности текста и split только если явно несколько идей. "
                    f"Не больше {max_actions} действий; схема подсказки: {json.dumps(schema)}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "max_actions": max_actions,
                        "chunks": compact_chunks,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _chat_url(base_url),
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"запрос LLM sleep: HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"запрос LLM sleep не удался: {e}") from e

    try:
        outer = json.loads(raw)
        content = outer["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("ответ LLM sleep: невалидный JSON") from e
    if not isinstance(parsed, dict) or not isinstance(parsed.get("actions", []), list):
        raise RuntimeError("ответ LLM sleep: нужен объект с полем actions[]")
    return parsed
