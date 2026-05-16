"""Retrieve API with gaze-aware graph propagation (relation_type bearings)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from wisedb.server.app import create_app


def _db_headers() -> dict[str, str]:
    return {"X-WiseDB-Database": "default", "X-WiseDB-Password": ""}


def test_retrieve_gaze_suppresses_contradicts_hop() -> None:
    app = create_app()
    h = _db_headers()
    vec_a = [0.0] * 384
    vec_a[0] = 1.0
    vec_b = [0.0] * 384
    vec_b[1] = 1.0
    with TestClient(app) as client:
        ra = client.post(
            "/v1/chunks/upsert",
            json={
                "tenant_id": "default",
                "chunks": [{"title": "A", "body": "alpha", "embedding": vec_a}],
            },
            headers=h,
        )
        rb = client.post(
            "/v1/chunks/upsert",
            json={
                "tenant_id": "default",
                "chunks": [{"title": "B", "body": "beta", "embedding": vec_b}],
            },
            headers=h,
        )
        assert ra.status_code == 200 and rb.status_code == 200
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
                        "relation_type": "contradicts",
                    }
                ],
            },
            headers=h,
        )
        assert re.status_code == 200

        qv = list(vec_a)
        r_sup = client.post(
            "/v1/retrieve",
            json={
                "tenant_id": "default",
                "query": "alpha",
                "query_embedding": qv,
                "gaze_aware": True,
                "suppress_behind": True,
                "include_opposition": False,
                "return_markdown": False,
                "max_hops": 3,
                "max_nodes": 8,
                "top_k_dense": 1,
                "top_k_lexical": 1,
            },
            headers=h,
        )
        r_opp = client.post(
            "/v1/retrieve",
            json={
                "tenant_id": "default",
                "query": "alpha",
                "query_embedding": qv,
                "gaze_aware": True,
                "suppress_behind": True,
                "include_opposition": True,
                "return_markdown": False,
                "max_hops": 3,
                "max_nodes": 8,
                "top_k_dense": 1,
                "top_k_lexical": 1,
            },
            headers=h,
        )
        assert r_sup.status_code == 200 and r_opp.status_code == 200
        score_b_sup = next(
            (c["score"] for c in r_sup.json()["chunks"] if uuid.UUID(c["id"]) == id_b),
            None,
        )
        score_b_opp = next(
            (c["score"] for c in r_opp.json()["chunks"] if uuid.UUID(c["id"]) == id_b),
            None,
        )
        assert score_b_sup is not None and score_b_opp is not None
        assert score_b_opp > score_b_sup
