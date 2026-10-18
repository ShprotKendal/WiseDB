from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wisedb import Wiki
from wisedb.server.app import app
from wisedb.wql import WqlError, execute
from wisedb.wql.lexer import tokenize
from wisedb.wql.parser import parse

PAGE = """---
type: concept
slug: node-a
title: Node A
summary: first
---
Links to [[node-b]] and [[missing-node]].
"""


@pytest.fixture
def wiki(tmp_path: Path) -> Generator[Wiki, None, None]:
    instance = Wiki(tmp_path / "wiki")
    try:
        yield instance
    finally:
        instance.close()


def test_lexer_keywords():
    kinds = [t.value.upper() for t in tokenize("SELECT LINKS DEPTH DIRECTION OUTGOING FROM edges") if t.type.name == "KEYWORD"]
    assert "LINKS" in kinds
    assert "EDGES" in kinds


def test_wql_read_and_list(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    got = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-a'")
    direct = wiki.read("node-a")
    assert got == direct

    listed = execute(wiki, "SELECT * FROM pages")
    assert listed == wiki.list()


def test_wql_neighbors_and_search(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    wiki.remember(
        "node-b",
        markdown="""---
type: concept
slug: node-b
title: Node B
summary: second
---
Back to [[node-a]].
""",
    )
    neighbors = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-a' NEIGHBORS")
    assert neighbors == wiki.read("node-a", neighbors=True)

    hits = execute(wiki, "SEARCH 'node' LIMIT 5 BUDGET 256")
    assert hits == wiki.search("node", budget_tokens=256, top_k=5)


def test_wql_graph_depth_and_edges(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    wiki.remember(
        "node-b",
        markdown="""---
type: concept
slug: node-b
title: Node B
summary: second
---
Points to [[node-c]].
""",
    )
    wiki.remember(
        "node-c",
        markdown="""---
type: concept
slug: node-c
title: Node C
summary: third
---
Leaf.
""",
    )

    depth2 = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-a' LINKS DEPTH 2 DIRECTION OUTGOING")
    assert depth2["node"]["slug"] == "node-a"
    assert depth2["levels"][0][0]["slug"] == "node-b"
    assert depth2["levels"][1][0]["slug"] == "node-c"
    assert "missing-node" in depth2["unknown_slugs"]

    edge_rows = execute(wiki, "SELECT * FROM edges WHERE from_slug = 'node-a' AND type = 'links_to'")
    assert edge_rows == wiki.query_edges(from_slug="node-a", edge_type="links_to")


def test_wql_graph_incoming_both_and_cycle(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    wiki.remember(
        "node-b",
        markdown="""---
type: concept
slug: node-b
title: Node B
summary: second
---
Back to [[node-a]].
""",
    )
    wiki.remember(
        "node-c",
        markdown="""---
type: concept
slug: node-c
title: Node C
summary: third
---
Uses [[node-b]].
""",
    )
    incoming = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-b' LINKS DIRECTION INCOMING")
    assert incoming["levels"][0][0]["slug"] == "node-a"

    both = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-a' LINKS DIRECTION BOTH")
    read_neighbors = wiki.read("node-a", neighbors=True)["neighbors"]
    assert {item["slug"] for item in both["levels"][0]} == {item["slug"] for item in read_neighbors}

    cycle = execute(wiki, "SELECT * FROM pages WHERE slug = 'node-a' LINKS DEPTH 2 DIRECTION OUTGOING")
    level_slugs = [node["slug"] for level in cycle["levels"] for node in level]
    assert level_slugs.count("node-a") == 0
    assert len(cycle["edges"]) == len({(e["from_slug"], e["to_slug"]) for e in cycle["edges"]})


def test_wql_insert_update_edit_parity(wiki: Wiki):
    markdown = "---\ntype: concept\nslug: parity\ntitle: P\nsummary: s\n---\nbody"
    wql_markdown = markdown.replace("\n", "\\n")
    insert = execute(
        wiki,
        f"INSERT INTO pages (slug, markdown) VALUES ('parity', '{wql_markdown}')",
    )
    assert insert["status"] == "ok"
    assert insert["node"]["slug"] == "parity"

    updated = execute(wiki, "UPDATE pages SET title = 'New title' WHERE slug = 'parity'")
    assert updated["node"]["title"] == "New title"

    edited = execute(
        wiki,
        "UPDATE pages SET markdown = '---\\ntype: concept\\nslug: parity\\ntitle: New title\\nsummary: s\\n---\\nedited' WHERE slug = 'parity'",
    )
    assert edited == wiki.edit(
        "parity",
        "---\ntype: concept\nslug: parity\ntitle: New title\nsummary: s\n---\nedited",
    )


def test_wql_catalog_filter_order_limit(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    wiki.remember(
        "node-z",
        markdown="""---
type: concept
slug: node-z
title: Zed
summary: z
---
No links.
""",
    )
    filtered = execute(wiki, "SELECT * FROM pages WHERE type = 'concept' ORDER BY title DESC LIMIT 1")
    direct = wiki.list_catalog(
        filter_json='{"cmp":{"field":"type","op":"=","value":"concept"}}',
        order_field="title",
        order_asc=False,
        limit=1,
    )
    assert filtered == direct


def test_wql_validation_errors():
    with pytest.raises(WqlError, match="DEPTH must be between 1 and 8"):
        parse("SELECT * FROM pages WHERE slug = 'a' LINKS DEPTH 9")
    with pytest.raises(WqlError, match="unknown edge type"):
        parse("SELECT * FROM edges WHERE from_slug = 'a' AND type = 'other'")
    with pytest.raises(WqlError, match="NEIGHBORS and LINKS"):
        parse("SELECT * FROM pages WHERE slug = 'a' NEIGHBORS LINKS")
    with pytest.raises(WqlError, match="column count"):
        parse("INSERT INTO pages (slug, markdown) VALUES ('a', 'b', 'c')")
    with pytest.raises(WqlError, match="unknown UPDATE field"):
        parse("UPDATE pages SET foo = 'x' WHERE slug = 'a'")
    with pytest.raises(WqlError, match="line 1, column"):
        parse("SELECT FROM pages")


def test_wql_schema_layout_and_folders(wiki: Wiki):
    schema_result = execute(wiki, "SCHEMA")
    assert "schema" in schema_result
    wiki.remember("node-a", markdown=PAGE, rel_path="Notes/a.md")
    layout = execute(wiki, "SELECT * FROM layout")
    assert "Notes" in layout["folders"]
    assert execute(wiki, "CREATE FOLDER 'Archive'")["status"] == "ok"
    assert execute(wiki, "DROP FOLDER 'Archive'")["status"] == "ok"
    markdown = execute(wiki, "SELECT markdown FROM pages WHERE slug = 'node-a'")
    assert "node-a" in markdown["markdown"]


def test_wql_delete_returns_ok(wiki: Wiki):
    wiki.remember("node-a", markdown=PAGE)
    assert execute(wiki, "DELETE FROM pages WHERE slug = 'node-a'") == {"status": "ok"}


def test_wql_http_execute_and_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path / "wisedb_data"))
    monkeypatch.setenv("WISEDB_ROOT_PASSWORD", "root-pass")
    monkeypatch.delenv("WISEDB_ALLOW_ANONYMOUS", raising=False)

    from wisedb.server.config import get_settings
    from wisedb.server.catalog import reset_catalog
    from wisedb.server.deps import close_wiki
    from wisedb.server.sessions import reset_session_store

    get_settings.cache_clear()
    close_wiki()
    reset_session_store()
    reset_catalog()

    with TestClient(app, raise_server_exceptions=False) as client:
        denied = client.post(
            "/databases/wql/execute",
            json={"query": "SELECT * FROM pages WHERE slug = 'http-topic'"},
        )
        assert denied.status_code == 401

        login = client.post("/auth/login", json={"username": "root", "password": "root-pass"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert client.post("/databases", headers=headers, json={"name": "wql"}).status_code == 200

        wiki = Wiki(tmp_path / "wisedb_data" / "databases" / "wql")
        wiki.remember(
            "http-topic",
            markdown="---\ntype: concept\nslug: http-topic\ntitle: T\nsummary: s\n---\nbody",
        )
        wiki.close()

        read = client.post(
            "/databases/wql/execute",
            headers=headers,
            json={"query": "SELECT * FROM pages WHERE slug = 'http-topic'"},
        )
        assert read.status_code == 200
        assert read.json()["node"]["slug"] == "http-topic"
