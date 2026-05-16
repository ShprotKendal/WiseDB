"""
WiseDoc: тело Markdown + опциональный TOML frontmatter (ограждения `+++` в стиле Hugo).

Для мультимодальных чанков: метаданные рисунков/файлов в frontmatter, ссылки в Markdown,
поисковый текст в ``body``. Для эмбеддингов вызывайте :func:`text_for_embedding`, чтобы
не потерять подписи.

Пример::

    +++
    wisedb_format = 1
    kind = "figure"
    caption = "Обзор архитектуры"

    [[wisedb.assets]]
    slot = "fig1"
    mime = "image/png"
    uri = "https://cdn.example.com/diagram.png"
    +++

    # Рисунок 1

    См. диаграмму: ![architecture](wisedb://fig1)

    Доп. текст для лексического поиска.
"""

from __future__ import annotations

import re
import sys
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef,unused-ignore]

# Альтернатива: один блок ```toml ... ``` в начале строки
_TOML_FENCE = re.compile(
    r"^\s*```\s*toml\s*\n(.*?)\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def parse_wisedoc(raw: str) -> tuple[dict[str, Any], str]:
    """
    Вернуть ``(словарь_frontmatter, markdown_тело)``.

    Форматы:

    - Ведущий блок ``+++`` … ``+++`` как TOML (:mod:`tomllib` на 3.11+, :mod:`tomli` раньше).
    - Или один fenced-блок ```toml в начале строки.
    - Иначе ``({}, raw)``.
    """
    text = raw.lstrip("\ufeff")
    if not text:
        return {}, ""

    if text.startswith("+++"):
        parts = text.split("+++", 2)
        if len(parts) >= 3:
            fm_raw = parts[1].strip()
            body = parts[2].lstrip("\n")
            try:
                data = tomllib.loads(fm_raw)
                if not isinstance(data, dict):
                    return {}, raw
                return dict(data), body
            except Exception:
                return {}, raw

    m = _TOML_FENCE.match(text)
    if m:
        try:
            data = tomllib.loads(m.group(1).strip())
            body = text[m.end() :].lstrip()
            if not isinstance(data, dict):
                return {}, raw
            return dict(data), body
        except Exception:
            return {}, raw

    return {}, text


def _frontmatter_assets(fm: dict[str, Any]) -> list[Any]:
    """Строки таблицы вложений из TOML (``[[wisedb.assets]]`` или вложенный ``wisedb.assets``)."""
    v = fm.get("wisedb.assets")
    if isinstance(v, list):
        return v
    v = fm.get("assets")
    if isinstance(v, list):
        return v
    w = fm.get("wisedb")
    if isinstance(w, dict):
        a = w.get("assets")
        if isinstance(a, list):
            return a
    return []


def text_for_embedding(*, title: str, body: str, frontmatter: dict[str, Any]) -> str:
    """
    Собрать плоскую строку для токенизатора / эмбеддинга.

    Заголовок, caption/description из frontmatter, заметки по assets, тело Markdown
    с упрощением ссылок/картинок (меньше шума).
    """
    bits: list[str] = []
    if title.strip():
        bits.append(title.strip())
    cap = frontmatter.get("caption")
    if isinstance(cap, str) and cap.strip():
        bits.append(cap.strip())
    desc = frontmatter.get("description")
    if isinstance(desc, str) and desc.strip():
        bits.append(desc.strip())
    assets = _frontmatter_assets(frontmatter)
    for a in assets:
        if isinstance(a, dict):
            for k in ("alt", "caption", "title", "uri"):
                v = a.get(k)
                if isinstance(v, str) and v.strip():
                    bits.append(v.strip())
    b = body
    b = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", b)
    b = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", b)
    if b.strip():
        bits.append(b.strip())
    return "\n\n".join(bits)
