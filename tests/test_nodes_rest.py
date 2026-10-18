"""REST tests for nodes and nested database tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.server_helpers import ensure_database, login, server_client

PAGE = """---
type: concept
slug: rest-topic
title: REST Topic
summary: test
---
Body for REST tests.
"""


def test_nodes_rest_crud_and_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wiki_root = tmp_path / "databases" / "nodesdb"
    wiki_root.mkdir(parents=True)
    (wiki_root / "Notes").mkdir()

    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "nodesdb")

        created = client.post(
            "/databases/nodesdb/folders",
            headers=headers,
            json={"path": "Notes/2026"},
        )
        assert created.status_code == 200

        create_resp = client.post(
            "/databases/nodesdb/nodes",
            headers=headers,
            json={
                "slug": "rest-topic",
                "markdown": PAGE,
                "rel_path": "Notes/2026/topic.md",
            },
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["node"]["slug"] == "rest-topic"

        listed = client.get("/databases/nodesdb/nodes", headers=headers)
        assert listed.status_code == 200
        assert any(p["slug"] == "rest-topic" for p in listed.json()["pages"])

        got = client.get("/databases/nodesdb/nodes/rest-topic", headers=headers)
        assert got.status_code == 200
        assert "REST tests" in got.json()["node"]["body"]

        content = client.get("/databases/nodesdb/nodes/rest-topic/content", headers=headers)
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("text/markdown")
        assert "slug: rest-topic" in content.text

        patched = client.patch(
            "/databases/nodesdb/nodes/rest-topic",
            headers=headers,
            json={"title": "Updated title"},
        )
        assert patched.status_code == 200
        assert patched.json()["node"]["title"] == "Updated title"

        full_md = PAGE.replace("REST Topic", "Updated title").replace("Body for REST tests.", "Edited body.")
        put_resp = client.put(
            "/databases/nodesdb/nodes/rest-topic",
            headers=headers,
            json={"markdown": full_md},
        )
        assert put_resp.status_code == 200
        assert "Edited body." in put_resp.json()["node"]["body"]

        tree = client.get("/databases/nodesdb/tree", headers=headers)
        assert tree.status_code == 200
        root = tree.json()["tree"]
        notes = next(f for f in root["folders"] if f["path"] == "Notes")
        year = next(f for f in notes["folders"] if f["path"] == "Notes/2026")
        assert any(p["slug"] == "rest-topic" for p in year["pages"])

        deleted = client.delete("/databases/nodesdb/nodes/rest-topic", headers=headers)
        assert deleted.status_code == 200
        assert client.get("/databases/nodesdb/nodes/rest-topic", headers=headers).status_code == 404


def test_nodes_rest_write_requires_privilege(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "team")

        created = client.post(
            "/users",
            headers=root,
            json={"username": "reader", "password": "reader-pass"},
        )
        user_id = created.json()["id"]
        client.put(
            f"/users/{user_id}/grants/team",
            headers=root,
            json={"privileges": ["read"]},
        )
        reader = login(client, "reader", "reader-pass")

        post_denied = client.post(
            "/databases/team/nodes",
            headers=reader,
            json={"slug": "x", "markdown": PAGE},
        )
        assert post_denied.status_code == 403

        client.put(
            f"/users/{user_id}/grants/team",
            headers=root,
            json={"privileges": ["read", "write"]},
        )
        post_ok = client.post(
            "/databases/team/nodes",
            headers=reader,
            json={"slug": "rest-topic", "markdown": PAGE},
        )
        assert post_ok.status_code == 200


MATCH_PAGE = """---
type: concept
slug: search-hit
title: Alpha keyword
summary: Short
---
The keyword appears in body text.
"""

MISS_PAGE = """---
type: concept
slug: search-miss
title: Category overview
summary: Taxonomy
---
Everything about categories in the system.
"""


def test_nodes_rest_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "searchdb")

        assert client.post(
            "/databases/searchdb/nodes",
            headers=headers,
            json={"slug": "search-hit", "markdown": MATCH_PAGE},
        ).status_code == 200
        assert client.post(
            "/databases/searchdb/nodes",
            headers=headers,
            json={"slug": "search-miss", "markdown": MISS_PAGE},
        ).status_code == 200

        resp = client.get("/databases/searchdb/search", headers=headers, params={"q": "keyword"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["query"] == "keyword"
        slugs = {n["slug"] for n in payload["nodes"]}
        assert "search-hit" in slugs
        assert "search-miss" not in slugs
        hit = next(n for n in payload["nodes"] if n["slug"] == "search-hit")
        assert "keyword appears" in hit["body"]

        miss = client.get("/databases/searchdb/search", headers=headers, params={"q": "cat"})
        assert miss.status_code == 200
        assert "search-miss" not in {n["slug"] for n in miss.json()["nodes"]}

        assert client.get("/databases/searchdb/search", params={"q": "keyword"}).status_code == 401

        empty = client.get("/databases/searchdb/search", headers=headers, params={"q": "   "})
        assert empty.status_code == 422
