"""Multi-database host: catalog, passwords, admin routes."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from wisedb.server.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WISEDB_API_KEY", "admin-secret")
    monkeypatch.setenv("WISEDB_BOOTSTRAP_DEFAULT_DB", "false")
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_create_list_delete_database(client: TestClient) -> None:
    r = client.post(
        "/v1/databases",
        json={"id": "app1", "password": "pw1", "name": "App One"},
        headers={"X-API-Key": "admin-secret"},
    )
    assert r.status_code == 201
    assert r.json()["id"] == "app1"

    r2 = client.get("/v1/databases", headers={"X-API-Key": "admin-secret"})
    assert r2.status_code == 200
    ids = {d["id"] for d in r2.json()}
    assert "app1" in ids

    r3 = client.post(
        "/v1/chunks/upsert",
        json={
            "tenant_id": "default",
            "chunks": [{"title": "A", "body": "alpha", "embedding": _unit_vec()}],
        },
        headers={"X-WiseDB-Database": "app1", "X-WiseDB-Password": "pw1"},
    )
    assert r3.status_code == 200

    r_bad = client.post(
        "/v1/chunks/upsert",
        json={"tenant_id": "default", "chunks": [{"title": "X", "body": "x"}]},
        headers={"X-WiseDB-Database": "app1", "X-WiseDB-Password": "wrong"},
    )
    assert r_bad.status_code == 401

    r4 = client.delete("/v1/databases/app1", headers={"X-API-Key": "admin-secret"})
    assert r4.status_code == 200


def test_databases_isolated(client: TestClient) -> None:
    for db_id, pw, title in [("a", "pa", "A"), ("b", "pb", "B")]:
        client.post(
            "/v1/databases",
            json={"id": db_id, "password": pw},
            headers={"X-API-Key": "admin-secret"},
        )
        client.post(
            "/v1/chunks/upsert",
            json={
                "tenant_id": "default",
                "chunks": [{"title": title, "body": title, "embedding": _unit_vec()}],
            },
            headers={"X-WiseDB-Database": db_id, "X-WiseDB-Password": pw},
        )

    ra = client.post(
        "/v1/retrieve",
        json={
            "tenant_id": "default",
            "query": "A",
            "query_embedding": _unit_vec(),
            "return_markdown": False,
        },
        headers={"X-WiseDB-Database": "a", "X-WiseDB-Password": "pa"},
    )
    assert ra.status_code == 200
    assert ra.json()["chunks"][0]["title"] == "A"

    rb = client.post(
        "/v1/retrieve",
        json={
            "tenant_id": "default",
            "query": "B",
            "query_embedding": _unit_vec(),
            "return_markdown": False,
        },
        headers={"X-WiseDB-Database": "b", "X-WiseDB-Password": "pb"},
    )
    assert rb.json()["chunks"][0]["title"] == "B"


def test_bootstrap_default_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("WISEDB_API_KEY", raising=False)
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/v1/chunks/upsert",
            json={
                "tenant_id": "default",
                "chunks": [{"title": "d", "body": "default db", "embedding": _unit_vec()}],
            },
            headers={"X-WiseDB-Database": "default", "X-WiseDB-Password": ""},
        )
        assert r.status_code == 200


def _unit_vec() -> list[float]:
    v = [0.0] * 384
    v[0] = 1.0
    return v
