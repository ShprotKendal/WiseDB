"""E2E в стиле MCP: те же HTTP-вызовы, что и инструменты ``mcp_server`` (приложение в процессе + lifespan)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from wisedb.server.app import create_app

_DB_HEADERS = {"X-WiseDB-Database": "default", "X-WiseDB-Password": ""}


def test_e2e_upsert_link_retrieve_markdown() -> None:
    app = create_app()
    with TestClient(app) as client:
        h = _DB_HEADERS
        vec_a = [0.0] * 384
        vec_a[0] = 1.0
        vec_b = [0.0] * 384
        vec_b[1] = 1.0
        qvec = [0.0] * 384
        qvec[0] = 0.95

        r = client.post(
            "/v1/chunks/upsert",
            headers=h,
            json={
                "tenant_id": "default",
                "chunks": [
                    {
                        "external_key": "alpha",
                        "title": "Alpha module",
                        "body": "Hydrazine fuel assembly and manifold.",
                        "tags": ["propulsion"],
                        "embedding": vec_a,
                    },
                    {
                        "external_key": "beta",
                        "title": "Beta telemetry",
                        "body": "Monitors hydrazine pressure sensors.",
                        "tags": ["telemetry"],
                        "embedding": vec_b,
                    },
                ],
            },
        )
        assert r.status_code == 200

        id_a = client.get(
            "/v1/chunks/lookup",
            params={"tenant_id": "default", "external_key": "alpha"},
            headers=h,
        ).json()["id"]
        id_b = client.get(
            "/v1/chunks/lookup",
            params={"tenant_id": "default", "external_key": "beta"},
            headers=h,
        ).json()["id"]

        r2 = client.post(
            "/v1/edges/upsert",
            headers=h,
            json={
                "tenant_id": "default",
                "edges": [
                    {
                        "source_chunk_id": id_a,
                        "target_chunk_id": id_b,
                        "weight": 0.92,
                        "directed": False,
                    }
                ],
            },
        )
        assert r2.status_code == 200

        r3 = client.post(
            "/v1/retrieve",
            headers=h,
            json={
                "tenant_id": "default",
                "query": "hydrazine pressure",
                "query_embedding": qvec,
                "return_markdown": True,
                "top_k_dense": 8,
                "top_k_lexical": 8,
                "max_hops": 4,
                "max_nodes": 24,
            },
        )
        assert r3.status_code == 200, r3.text
        data = r3.json()
        md = data.get("markdown") or ""
        assert "Пакет контекста WiseDB" in md
        assert "hydrazine" in md.lower()
        assert len(data.get("chunks") or []) >= 1
