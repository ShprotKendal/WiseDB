"""Shared helpers for HTTP server integration tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wisedb.server.app import app


def reset_server_env(monkeypatch: pytest.MonkeyPatch, tmp_path, *, root_password: str = "root-secret") -> None:
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WISEDB_ROOT_PASSWORD", root_password)
    monkeypatch.delenv("WISEDB_API_KEY", raising=False)
    monkeypatch.delenv("WISEDB_WIKI_ID", raising=False)
    monkeypatch.delenv("WISEDB_ALLOW_ANONYMOUS", raising=False)

    from wisedb.server.catalog import reset_catalog
    from wisedb.server.config import get_settings
    from wisedb.server.deps import close_wiki
    from wisedb.server.sessions import reset_session_store

    close_wiki()
    reset_session_store()
    reset_catalog()
    get_settings.cache_clear()


def server_client(monkeypatch: pytest.MonkeyPatch, tmp_path, *, root_password: str = "root-secret") -> TestClient:
    reset_server_env(monkeypatch, tmp_path, root_password=root_password)
    return TestClient(app, raise_server_exceptions=False)


def login(client: TestClient, username: str = "root", password: str = "root-secret") -> dict[str, str]:
    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def ensure_database(client: TestClient, headers: dict[str, str], name: str) -> None:
    resp = client.post("/databases", headers=headers, json={"name": name})
    assert resp.status_code in {200, 409}, resp.text


def execute(client: TestClient, headers: dict[str, str], database: str, query: str):
    return client.post(f"/databases/{database}/execute", headers=headers, json={"query": query})
