"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def wisedb_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path / "wisedb_data"))
