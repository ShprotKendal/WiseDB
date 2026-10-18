"""Tests for session auth, users, databases, and WQL execute."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.server_helpers import ensure_database, execute, login, server_client

PAGE = """---
type: concept
slug: shell-topic
title: Shell Topic
summary: test
---
Body for shell tests.
"""


def test_import_app():
    from wisedb.server.app import app as application

    assert application.title == "WiseDB"


def test_session_required(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        assert client.get("/users/me").status_code == 401


def test_root_login_and_me(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        me = client.get("/users/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == "root"
        assert me.json()["is_root"] is True


def test_logout(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        assert client.delete("/auth/session", headers=headers).status_code == 200
        assert client.get("/users/me", headers=headers).status_code == 401


def test_user_grants_and_database_access(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        assert client.post("/databases", headers=root, json={"name": "team"}).status_code == 200
        assert client.post("/databases", headers=root, json={"name": "other"}).status_code == 200

        created = client.post(
            "/users",
            headers=root,
            json={"username": "agent1", "password": "agent1-pass"},
        )
        assert created.status_code == 200
        user_id = created.json()["id"]

        grants = client.put(
            f"/users/{user_id}/grants/team",
            headers=root,
            json={"privileges": ["read"]},
        )
        assert grants.status_code == 200

        agent = login(client, "agent1", "agent1-pass")
        from wisedb.server.config import SYSTEM_WIKI_DATABASE

        listed = client.get("/databases", headers=agent).json()["databases"]
        assert listed == sorted(["team", SYSTEM_WIKI_DATABASE])
        assert client.get("/databases", headers=root).json()["databases"] == sorted(
            ["other", "team", SYSTEM_WIKI_DATABASE]
        )

        denied = execute(client, agent, "other", "SELECT * FROM pages")
        assert denied.status_code == 403

        insert_denied = execute(
            client,
            agent,
            "team",
            "INSERT INTO pages (slug, markdown) VALUES ('x', '---\\ntype: concept\\nslug: x\\ntitle: T\\nsummary: s\\n---\\nbody')",
        )
        assert insert_denied.status_code == 403

        client.put(
            f"/users/{user_id}/grants/team",
            headers=root,
            json={"privileges": ["read", "write"]},
        )
        insert_ok = execute(
            client,
            agent,
            "team",
            "INSERT INTO pages (slug, markdown) VALUES ('x', '---\\ntype: concept\\nslug: x\\ntitle: T\\nsummary: s\\n---\\nbody')",
        )
        assert insert_ok.status_code == 200
        assert insert_ok.json()["node"]["slug"] == "x"


def test_wql_execute_read(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "main")
        wql_md = PAGE.replace("\n", "\\n")
        execute(
            client,
            root,
            "main",
            f"INSERT INTO pages (slug, markdown) VALUES ('shell-topic', '{wql_md}')",
        )
        read = execute(client, root, "main", "SELECT * FROM pages WHERE slug = 'shell-topic'")
        assert read.status_code == 200
        assert read.json()["node"]["slug"] == "shell-topic"


def test_wql_parse_and_batch(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        assert client.post("/databases/wql/parse", json={"query": "SCHEMA"}).status_code == 401

        root = login(client)
        ensure_database(client, root, "wql")

        parsed = client.post(
            "/databases/wql/parse",
            headers=root,
            json={"query": "SELECT * FROM pages WHERE slug = 'a'"},
        )
        assert parsed.status_code == 200
        assert parsed.json() == {
            "statement": "select",
            "privilege": "read",
            "allowed": True,
            "streaming": False,
        }

        invalid = client.post("/databases/wql/parse", headers=root, json={"query": "NOT WQL"})
        assert invalid.status_code == 422
        assert client.post("/databases/missing/parse", headers=root, json={"query": "SCHEMA"}).status_code == 404

        created = client.post(
            "/users",
            headers=root,
            json={"username": "reader", "password": "reader-pass"},
        )
        assert created.status_code == 200
        user_id = created.json()["id"]
        assert (
            client.put(
                f"/users/{user_id}/grants/wql",
                headers=root,
                json={"privileges": ["read"]},
            ).status_code
            == 200
        )
        reader = login(client, "reader", "reader-pass")
        write_parse = client.post(
            "/databases/wql/parse",
            headers=reader,
            json={"query": "DELETE FROM pages WHERE slug = 'a'"},
        )
        assert write_parse.status_code == 200
        assert write_parse.json() == {
            "statement": "delete",
            "privilege": "write",
            "allowed": False,
            "streaming": False,
        }

        markdown = "---\\ntype: concept\\nslug: batch-b\\ntitle: T\\nsummary: s\\n---\\nbody"
        rejected = client.post(
            "/databases/wql/batch",
            headers=root,
            json={
                "queries": [
                    f"INSERT INTO pages (slug, markdown) VALUES ('batch-b', '{markdown}')",
                    "NOT WQL",
                ]
            },
        )
        assert rejected.status_code == 422
        assert rejected.json()["detail"].startswith("query 2:")
        listed = execute(client, root, "wql", "SELECT * FROM pages")
        assert listed.status_code == 200
        assert "batch-b" not in listed.text

        denied = client.post(
            "/databases/wql/batch",
            headers=reader,
            json={"queries": ["SCHEMA", "DELETE FROM pages WHERE slug = 'a'"]},
        )
        assert denied.status_code == 403

        inserted = "---\\ntype: concept\\nslug: batch-a\\ntitle: T\\nsummary: s\\n---\\nbody"
        batch = client.post(
            "/databases/wql/batch",
            headers=root,
            json={
                "queries": [
                    f"INSERT INTO pages (slug, markdown) VALUES ('batch-a', '{inserted}')",
                    "SELECT * FROM pages WHERE slug = 'batch-a'",
                ]
            },
        )
        assert batch.status_code == 200, batch.text
        results = batch.json()["results"]
        assert results[0]["node"]["slug"] == "batch-a"
        assert results[1]["node"]["slug"] == "batch-a"


def test_users_persist_across_restart(tmp_path, monkeypatch):
    from tests.server_helpers import reset_server_env

    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        created = client.post(
            "/users",
            headers=root,
            json={"username": "persisted", "password": "persisted-pass"},
        )
        assert created.status_code == 200

    reset_server_env(monkeypatch, tmp_path)
    from wisedb.server.app import app

    with TestClient(app, raise_server_exceptions=False) as client:
        headers = login(client, "persisted", "persisted-pass")
        me = client.get("/users/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == "persisted"
