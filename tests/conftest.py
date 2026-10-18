"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def wisedb_data_dir(tmp_path, monkeypatch):
    from wisedb.server.config import get_settings
    from wisedb.server.deps import close_wiki
    from wisedb.server.sessions import reset_session_store

    close_wiki()
    reset_session_store()
    get_settings.cache_clear()
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path / "wisedb_data"))
    yield
    close_wiki()
    reset_session_store()
    get_settings.cache_clear()
