"""Optics: custom layers, REST, grants, and WQL."""

from __future__ import annotations

from tests.server_helpers import ensure_database, execute, login, server_client


def _create_user(client, root_headers, username: str, password: str) -> str:
    resp = client.post(
        "/users",
        headers=root_headers,
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_optic_registry_and_text(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")

        resp = client.post(
            "/databases/notes/optics",
            headers=root,
            json={"name": "metatags", "description": "client metadata"},
        )
        assert resp.status_code == 200, resp.text

        execute(
            client,
            root,
            "notes",
            "INSERT INTO pages (slug, markdown) VALUES ('a', '# A\\ntitle: A\\n---\\nbody')",
        )
        put = client.put(
            "/databases/notes/nodes/a/optics/metatags",
            headers=root,
            json={"text": '{"tags":["x"]}'},
        )
        assert put.status_code == 200, put.text
        assert put.json()["text"] == '{"tags":["x"]}'

        get = client.get("/databases/notes/nodes/a/optics/metatags", headers=root)
        assert get.status_code == 200
        assert get.json()["text"] == '{"tags":["x"]}'

        listed = client.get("/databases/notes/optics", headers=root)
        assert listed.status_code == 200
        names = {item["name"] for item in listed.json()["optics"]}
        assert names == {"content", "metatags"}


def test_optic_text_cleared_on_node_delete(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")
        client.post("/databases/notes/optics", headers=root, json={"name": "layer"})
        execute(
            client,
            root,
            "notes",
            "INSERT INTO pages (slug, markdown) VALUES ('gone', '# G\\ntitle: G\\n---\\n')",
        )
        client.put(
            "/databases/notes/nodes/gone/optics/layer",
            headers=root,
            json={"text": "keep"},
        )
        execute(client, root, "notes", "DELETE FROM pages WHERE slug = 'gone'")
        rows = execute(client, root, "notes", "SELECT * FROM optics WHERE name = 'layer'").json()
        assert rows["rows"] == []


def test_optic_grants_inherit_and_override(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "team")
        client.post("/databases/team/optics", headers=root, json={"name": "meta"})
        execute(
            client,
            root,
            "team",
            "INSERT INTO pages (slug, markdown) VALUES ('p', '# P\\ntitle: P\\n---\\n')",
        )

        user_id = _create_user(client, root, "reader", "reader-pass-1")
        client.put(
            f"/users/{user_id}/grants/team",
            headers=root,
            json={"privileges": ["read"]},
        )
        reader = login(client, "reader", "reader-pass-1")

        assert client.get("/databases/team/nodes/p/optics/meta", headers=reader).status_code == 200
        assert client.put(
            "/databases/team/nodes/p/optics/meta",
            headers=reader,
            json={"text": "nope"},
        ).status_code == 403

        client.put(
            f"/users/{user_id}/grants/team/optics/meta",
            headers=root,
            json={"privileges": ["view", "edit"]},
        )
        client.put("/databases/team/nodes/p/optics/meta", headers=reader, json={"text": "ok"})
        assert client.get("/databases/team/nodes/p/optics/meta", headers=reader).json()["text"] == "ok"

        client.put(
            f"/users/{user_id}/grants/team/optics/meta",
            headers=root,
            json={"privileges": []},
        )
        assert client.get("/databases/team/nodes/p/optics/meta", headers=reader).status_code == 403


def test_content_optic_rejected_on_custom_routes(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")
        execute(
            client,
            root,
            "notes",
            "INSERT INTO pages (slug, markdown) VALUES ('a', '# A\\ntitle: A\\n---\\n')",
        )
        resp = client.get("/databases/notes/nodes/a/optics/content", headers=root)
        assert resp.status_code == 422


def test_wql_optics_flow(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")
        execute(client, root, "notes", "CREATE OPTIC tags 'tag layer'")
        execute(
            client,
            root,
            "notes",
            "INSERT INTO pages (slug, markdown) VALUES ('n', '# N\\ntitle: N\\n---\\n')",
        )
        upd = execute(
            client,
            root,
            "notes",
            "UPDATE optics SET text = 't1' WHERE slug = 'n' AND name = 'tags'",
        )
        assert upd.status_code == 200, upd.text
        sel = execute(client, root, "notes", "SELECT * FROM optics WHERE slug = 'n' AND name = 'tags'")
        assert sel.json()["rows"] == [{"slug": "n", "name": "tags", "kind": "markdown", "text": "t1"}]
        execute(client, root, "notes", "DELETE FROM optics WHERE slug = 'n' AND name = 'tags'")
        assert execute(client, root, "notes", "SELECT * FROM optics WHERE name = 'tags'").json()["rows"] == []
        execute(client, root, "notes", "DROP OPTIC tags")


def test_optic_kinds_json_and_registry(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")
        resp = client.post(
            "/databases/notes/optics",
            headers=root,
            json={"name": "meta", "kind": "json", "description": "metadata"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["kind"] == "json"
        execute(
            client,
            root,
            "notes",
            "INSERT INTO pages (slug, markdown) VALUES ('j', '# J\\ntitle: J\\n---\\n')",
        )
        bad = client.put(
            "/databases/notes/nodes/j/optics/meta",
            headers=root,
            json={"text": "not-json"},
        )
        assert bad.status_code == 422
        ok = client.put(
            "/databases/notes/nodes/j/optics/meta",
            headers=root,
            json={"text": '{"a":1}'},
        )
        assert ok.status_code == 200


def test_optic_span_live_edit(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "code")
        client.patch(
            "/databases/code/settings",
            headers=root,
            json={"code_root": str(repo.resolve())},
        )
        client.post("/databases/code/optics", headers=root, json={"name": "slice", "kind": "span"})
        execute(
            client,
            root,
            "code",
            "INSERT INTO pages (slug, markdown) VALUES ('n', '# N\\ntitle: N\\n---\\n')",
        )
        bind = client.put(
            "/databases/code/nodes/n/optics/slice",
            headers=root,
            json={"path": "src/app.py", "start_line": 2, "end_line": 3},
        )
        assert bind.status_code == 200, bind.text
        body = bind.json()
        assert body["text"] == "line2\nline3\n"
        sha = body["sha256"]

        edit = client.put(
            "/databases/code/nodes/n/optics/slice",
            headers=root,
            json={"text": "changed\n", "base_sha256": sha},
        )
        assert edit.status_code == 200, edit.text
        assert source.read_text(encoding="utf-8") == "line1\nchanged\nline4\n"


def test_wql_create_optic_with_kind(monkeypatch, tmp_path):
    with server_client(monkeypatch, tmp_path) as client:
        root = login(client)
        ensure_database(client, root, "notes")
        execute(client, root, "notes", "CREATE OPTIC frames IMAGE 'shots'")
        listed = client.get("/databases/notes/optics", headers=root).json()["optics"]
        frames = next(item for item in listed if item["name"] == "frames")
        assert frames["kind"] == "image"
