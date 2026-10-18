"""Bundled read-only system wiki pages for the WiseDB HTTP server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath

_SLUG_RE = re.compile(r"^slug:\s*(\S+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SystemWikiPage:
    slug: str
    markdown: str
    rel_path: str


def _iter_markdown_files(base) -> list:
    found = []
    for item in base.iterdir():
        if item.name.startswith("_") or item.name == "__init__.py":
            continue
        if item.is_dir():
            found.extend(_iter_markdown_files(item))
        elif item.name.endswith(".md"):
            found.append(item)
    return found


def iter_system_wiki_pages() -> list[SystemWikiPage]:
    pages: list[SystemWikiPage] = []
    root = resources.files("wisedb.system_wiki")
    for md in _iter_markdown_files(root):
        rel = str(PurePosixPath(*md.relative_to(root).parts))
        text = md.read_text(encoding="utf-8")
        slug = _slug_from_markdown(text)
        pages.append(SystemWikiPage(slug=slug, markdown=text, rel_path=rel))
    pages.sort(key=lambda p: p.rel_path)
    return pages


def _slug_from_markdown(text: str) -> str:
    match = _SLUG_RE.search(text)
    if not match:
        raise ValueError("system wiki page missing slug in frontmatter")
    return match.group(1)
