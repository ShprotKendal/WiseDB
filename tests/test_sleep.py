"""Sleep / maintenance API (decay, geometry refresh, journal)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from wisedb.server.app import create_app


def _db_headers() -> dict[str, str]:
    return {"X-WiseDB-Database": "default", "X-WiseDB-Password": ""}


def test_sleep_decay_prune_deletes_weak_edge() -> None:
    app = create_app()
    h = _db_headers()
    v = [0.0] * 384
    v[0] = 1.0
    with TestClient(app) as client:
        ra = client.post(
            "/v1/chunks/upsert",
            json={"tenant_id": "default", "chunks": [{"title": "A", "body": "a", "embedding": v}]},
            headers=h,
        )
        rb = client.post(
            "/v1/chunks/upsert",
            json={"tenant_id": "default", "chunks": [{"title": "B", "body": "b", "embedding": v}]},
            headers=h,
        )
        id_a = uuid.UUID(ra.json()["ids"][0])
        id_b = uuid.UUID(rb.json()["ids"][0])
        re = client.post(
            "/v1/edges/upsert",
            json={
                "tenant_id": "default",
                "edges": [
                    {
                        "source_chunk_id": str(id_a),
                        "target_chunk_id": str(id_b),
                        "weight": 1.0,
                        "directed": False,
                    }
                ],
            },
            headers=h,
        )
        assert re.status_code == 200
        rs = client.post(
            "/v1/sleep",
            json={
                "tenant_id": "default",
                "phase": "decay_prune",
                "dry_run": False,
                "decay_factor": 0.5,
                "min_weight": 0.6,
                "delete_orphan_chunks": False,
            },
            headers=h,
        )
        assert rs.status_code == 200
        data = rs.json()
        assert data["status"] == "completed"
        assert data["stats"]["edges_deleted"] >= 1
        assert "run_id" in data


def test_sleep_dry_run_no_status_failed() -> None:
    app = create_app()
    h = _db_headers()
    with TestClient(app) as client:
        r = client.post(
            "/v1/sleep",
            json={
                "tenant_id": "default",
                "phase": "decay_prune",
                "dry_run": True,
                "decay_factor": 0.9,
                "min_weight": 0.01,
            },
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


def test_sleep_invalid_phase_unprocessable() -> None:
    app = create_app()
    h = _db_headers()
    with TestClient(app) as client:
        r = client.post(
            "/v1/sleep",
            json={"tenant_id": "default", "phase": "invalid"},
            headers=h,
        )
        assert r.status_code == 422


def test_sleep_geometry_refresh_completes() -> None:
    app = create_app()
    h = _db_headers()
    with TestClient(app) as client:
        r = client.post(
            "/v1/sleep",
            json={"tenant_id": "default", "phase": "geometry_refresh", "dry_run": False},
            headers=h,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"


def test_sleep_llm_optimize_dry_run(monkeypatch: Any) -> None:
    def fake_optimize_sleep_chunks(**kwargs: Any) -> dict[str, Any]:
        chunks = kwargs["chunks"]
        return {
            "actions": [
                {
                    "type": "update",
                    "chunk_id": chunks[0]["id"],
                    "title": "Clean title",
                    "body": "Clean body",
                    "tags": ["clean"],
                    "reason": "cleanup",
                }
            ]
        }

    monkeypatch.setattr("wisedb.server.llm_sleep.optimize_sleep_chunks", fake_optimize_sleep_chunks)
    app = create_app()
    h = _db_headers()
    with TestClient(app) as client:
        rc = client.post(
            "/v1/chunks/upsert",
            json={"tenant_id": "default", "chunks": [{"title": "Messy", "body": "messy body"}]},
            headers=h,
        )
        assert rc.status_code == 200
        rs = client.post(
            "/v1/sleep",
            json={
                "tenant_id": "default",
                "phase": "llm_optimize",
                "dry_run": True,
                "llm_api_key": "test-key",
            },
            headers=h,
        )
        assert rs.status_code == 200
        data = rs.json()
        assert data["status"] == "completed"
        assert data["stats"]["llm_chunks_considered"] == 1
        assert data["stats"]["llm_actions_proposed"] == 1
        assert data["stats"]["llm_actions_preview"][0]["type"] == "update"
        assert "body" not in data["stats"]["llm_actions_preview"][0]


def test_sleep_llm_optimize_updates_and_splits(monkeypatch: Any) -> None:
    def fake_optimize_sleep_chunks(**kwargs: Any) -> dict[str, Any]:
        chunks = kwargs["chunks"]
        return {
            "actions": [
                {
                    "type": "update",
                    "chunk_id": chunks[0]["id"],
                    "title": "Optimized node",
                    "body": "Optimized body",
                    "tags": ["optimized"],
                    "reason": "clearer",
                },
                {
                    "type": "split",
                    "chunk_id": chunks[0]["id"],
                    "chunks": [
                        {"title": "Part A", "body": "Atomic fact A", "tags": ["part"]},
                        {"title": "Part B", "body": "Atomic fact B", "tags": ["part"]},
                    ],
                    "reason": "two ideas",
                },
            ]
        }

    monkeypatch.setattr("wisedb.server.llm_sleep.optimize_sleep_chunks", fake_optimize_sleep_chunks)
    app = create_app()
    h = _db_headers()
    with TestClient(app) as client:
        rc = client.post(
            "/v1/chunks/upsert",
            json={"tenant_id": "default", "chunks": [{"title": "Mixed", "body": "A and B"}]},
            headers=h,
        )
        assert rc.status_code == 200
        cid = rc.json()["ids"][0]
        rs = client.post(
            "/v1/sleep",
            json={
                "tenant_id": "default",
                "phase": "llm_optimize",
                "dry_run": False,
                "llm_api_key": "test-key",
            },
            headers=h,
        )
        assert rs.status_code == 200
        data = rs.json()
        assert data["status"] == "completed"
        assert data["stats"]["chunks_updated_by_llm"] == 1
        assert data["stats"]["chunks_created_by_llm_split"] == 2
        assert data["stats"]["edges_created_by_llm_split"] == 2

        got = client.get(f"/v1/chunks/{cid}", headers=h)
        assert got.status_code == 200
        assert got.json()["title"] == "Optimized node"

        listed = client.get("/v1/chunks?tenant_id=default", headers=h)
        assert listed.status_code == 200
        titles = {row["title"] for row in listed.json()}
        assert {"Optimized node", "Part A", "Part B"} <= titles
