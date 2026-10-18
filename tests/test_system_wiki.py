"""Tests for the bundled read-only _wiki system database."""

from __future__ import annotations

from wisedb.server.config import SYSTEM_WIKI_DATABASE, get_settings
from wisedb.server.system_wiki import sync_system_wiki

from tests.server_helpers import execute, login, server_client


def test_system_wiki_visible_and_readable_without_grant(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        created = client.post(
            "/users",
            headers=root,
            json={"username": "reader", "password": "reader-pass"},
        )
        assert created.status_code == 200
        agent = login(client, "reader", "reader-pass")

        listed = client.get("/databases", headers=agent).json()["databases"]
        assert SYSTEM_WIKI_DATABASE in listed

        read_index = execute(
            client,
            agent,
            SYSTEM_WIKI_DATABASE,
            "SELECT * FROM pages WHERE slug = 'index'",
        )
        assert read_index.status_code == 200
        assert read_index.json()["node"]["title"] == "WiseDB system wiki"


def test_system_wiki_rejects_writes_and_admin(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)

        insert = execute(
            client,
            root,
            SYSTEM_WIKI_DATABASE,
            "INSERT INTO pages (slug, markdown) VALUES ('hack', '---\\nslug: hack\\n---\\nx')",
        )
        assert insert.status_code == 403

        drop = client.delete(f"/databases/{SYSTEM_WIKI_DATABASE}", headers=root)
        assert drop.status_code == 403

        create_reserved = client.post("/databases", headers=root, json={"name": SYSTEM_WIKI_DATABASE})
        assert create_reserved.status_code == 422

        created_user = client.post(
            "/users",
            headers=root,
            json={"username": "grantuser", "password": "grantuser-pass"},
        )
        assert created_user.status_code == 200
        user = created_user.json()
        grant = client.put(
            f"/users/{user['id']}/grants/{SYSTEM_WIKI_DATABASE}",
            headers=root,
            json={"privileges": ["read"]},
        )
        assert grant.status_code == 422


def test_system_wiki_resync_from_package(tmp_path, monkeypatch):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        settings = get_settings()
        wiki_root = settings.database_root(SYSTEM_WIKI_DATABASE)
        tampered = wiki_root / "index.md"
        assert tampered.is_file()
        tampered.write_text("---\nslug: index\n---\ntampered\n", encoding="utf-8")

        from wisedb.server.deps import close_wiki

        close_wiki()
        sync_system_wiki()

        content = client.get(
            f"/databases/{SYSTEM_WIKI_DATABASE}/nodes/index/content",
            headers=root,
        )
        assert content.status_code == 200
        assert "tampered" not in content.text
        assert "WiseDB system wiki" in content.text


def test_system_wiki_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("WISEDB_SYSTEM_WIKI", "0")
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        listed = client.get("/databases", headers=root).json()["databases"]
        assert SYSTEM_WIKI_DATABASE not in listed

        missing = execute(client, root, SYSTEM_WIKI_DATABASE, "SCHEMA")
        assert missing.status_code == 404
