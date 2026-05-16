"""API tests against in-RAM NeuralContextDB (no PostgreSQL)."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from wisedb.server.app import create_app


def _db_headers() -> dict[str, str]:
    return {"X-WiseDB-Database": "default", "X-WiseDB-Password": ""}


def test_retrieve_with_vectors_roundtrip() -> None:
    app = create_app()
    with TestClient(app) as client:
        h = _db_headers()
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/readyz").json()["status"] == "ready"

        vec = [0.0] * 384
        vec[0] = 1.0
        r = client.post(
            "/v1/chunks/upsert",
            json={
                "tenant_id": "default",
                "chunks": [
                    {
                        "external_key": "k1",
                        "title": "Hello",
                        "body": "world of wise neural context",
                        "tags": ["t"],
                        "embedding": vec,
                    }
                ],
            },
            headers=h,
        )
        assert r.status_code == 200
        cid = r.json()["ids"][0]

        qvec = [0.0] * 384
        qvec[0] = 1.0
        r2 = client.post(
            "/v1/retrieve",
            json={
                "tenant_id": "default",
                "query": "wise",
                "query_embedding": qvec,
                "return_markdown": False,
                "top_k_dense": 5,
                "top_k_lexical": 5,
            },
            headers=h,
        )
        assert r2.status_code == 200
        data = r2.json()
        assert any(c["id"] == cid for c in data["chunks"])

        m = client.get("/metrics")
        assert m.status_code == 200
        assert b"wisedb_retrieve_seconds" in m.content


def test_attachment_inline_bytes_roundtrip() -> None:
    app = create_app()
    h = _db_headers()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    b64 = base64.b64encode(png).decode("ascii")
    with TestClient(app) as client:
        r = client.post(
            "/v1/chunks/upsert",
            headers=h,
            json={
                "tenant_id": "default",
                "chunks": [
                    {
                        "title": "Fig",
                        "body": "see shot",
                        "embedding": [0.0] * 384,
                        "attachments": [
                            {"slot": "shot", "mime": "image/png", "data_base64": b64},
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200, r.text
        cid = r.json()["ids"][0]
        g = client.get(f"/v1/chunks/{cid}", params={"tenant_id": "default"}, headers=h)
        assert g.status_code == 200
        atts = g.json()["attachments"]
        assert len(atts) == 1
        assert atts[0]["slot"] == "shot"
        assert atts[0]["byte_length"] == len(png)
        a = client.get(
            f"/v1/chunks/{cid}/attachments/shot",
            params={"tenant_id": "default"},
            headers=h,
        )
        assert a.status_code == 200
        assert a.content == png
        assert a.headers.get("content-type", "").startswith("image/png")
