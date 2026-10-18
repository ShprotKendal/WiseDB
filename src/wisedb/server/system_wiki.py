"""Seed and register the read-only system wiki database."""

from __future__ import annotations

import shutil

from wisedb.memory import Wiki
from wisedb.server import deps as deps_module
from wisedb.server.catalog import get_catalog
from wisedb.server.config import SYSTEM_WIKI_DATABASE, get_settings
from wisedb.system_wiki import iter_system_wiki_pages


def sync_system_wiki() -> None:
    """Register or unregister `_wiki` and refresh on-disk content from the package."""
    settings = get_settings()
    catalog = get_catalog()
    name = SYSTEM_WIKI_DATABASE
    root = settings.database_root(name)

    _close_cached_wiki(name)

    if not settings.system_wiki:
        if catalog.database_exists(name):
            catalog.delete_database(name)
        return

    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    wiki = Wiki(root)
    try:
        for page in iter_system_wiki_pages():
            wiki.remember(page.slug, markdown=page.markdown, rel_path=page.rel_path)
    finally:
        wiki.close()

    if not catalog.database_exists(name):
        catalog.create_database(name)


def _close_cached_wiki(name: str) -> None:
    with deps_module._wiki_lock:
        cached = deps_module._wiki_instances.pop(name, None)
        if cached is not None:
            cached.close()
