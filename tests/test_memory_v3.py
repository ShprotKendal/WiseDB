"""Tests for WiseDB v3 agent memory."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from wisedb import Wiki, schema


@pytest.fixture
def wiki(tmp_path: Path) -> Generator[Wiki, None, None]:
    instance = Wiki(tmp_path / "wiki")
    try:
        yield instance
    finally:
        instance.close()


PAGE = """---
type: concept
slug: redis-cache
title: Redis Cache
summary: In-memory key-value store
---
Redis powers [[project-x]] caching.
"""


def test_schema_text():
    text = schema()
    assert "remember" in text
    assert "WikiLLM" in text or "wiki" in text.lower()


def test_remember_read_edit_search(wiki: Wiki):
    r = wiki.remember("redis-cache", markdown=PAGE)
    assert r["status"] == "ok"
    assert r["node"]["slug"] == "redis-cache"

    got = wiki.read("redis-cache", neighbors=True)
    assert got["status"] == "ok"
    assert "Redis" in got["node"]["body"]
    assert isinstance(got["neighbors"], list)

    edited = wiki.edit(
        "redis-cache",
        PAGE.replace("Redis powers", "Redis mainly powers"),
    )
    assert edited["status"] == "ok"

    wiki.remember(
        "project-x",
        markdown="""---
type: entity
slug: project-x
title: Project X
summary: Main app
---
Uses redis-cache for sessions.
""",
    )

    hits = wiki.search("redis cache", budget_tokens=512, top_k=5)
    assert "hits" in hits
    assert len(hits["hits"]) >= 1
    assert hits["used_tokens"] <= 512


def test_keyword_search_whole_words(wiki: Wiki):
    wiki.remember(
        "kw-hit",
        markdown="""---
type: concept
slug: kw-hit
title: Alpha keyword
summary: Short
---
The keyword appears in body text.
""",
    )
    wiki.remember(
        "kw-miss",
        markdown="""---
type: concept
slug: kw-miss
title: Category overview
summary: Taxonomy
---
Everything about categories in the system.
""",
    )
    wiki.remember(
        "kw-cyr",
        markdown="""---
type: concept
slug: kw-cyr
title: Поиск документов
summary: RU
---
В системе важен поиск.
""",
    )

    by_word = wiki.search("keyword", budget_tokens=0, top_k=20)
    slugs = {h["slug"] for h in by_word["hits"]}
    assert "kw-hit" in slugs
    assert "kw-miss" not in slugs

    cat_hits = wiki.search("cat", budget_tokens=0, top_k=20)
    assert "kw-miss" not in {h["slug"] for h in cat_hits["hits"]}

    cyr = wiki.search("поиск", budget_tokens=0, top_k=20)
    assert "kw-cyr" in {h["slug"] for h in cyr["hits"]}

    empty = wiki.search("--- !!!", budget_tokens=0)
    assert empty["hits"] == []

    hits_only = wiki.search("keyword", budget_tokens=0, top_k=5)
    assert hits_only["packet"] == []
    assert hits_only["used_tokens"] == 0
    for hit in hits_only["hits"]:
        assert hit["body"] == ""


def test_unknown_slug_in_remember(wiki: Wiki):
    r = wiki.remember("orphan", markdown="""---
type: concept
slug: orphan
title: Orphan
summary: lone
---
Links to [[missing-node]].
""")
    assert "missing-node" in r["unknown_slugs"]


def test_http_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.server_helpers import ensure_database, execute, login, server_client

    with server_client(monkeypatch, tmp_path) as client:
        assert client.get("/healthz").json()["version"] == "3.0.0"

        openapi = client.get("/openapi.json").json()
        bearer = openapi["components"]["securitySchemes"]["HTTPBearer"]
        assert bearer.get("description", "").strip()

        for tag in openapi.get("tags", []):
            assert tag.get("name")
            assert tag.get("description", "").strip(), f"missing tag description: {tag.get('name')}"

        http_methods = {"get", "post", "put", "patch", "delete", "head", "options"}
        for path, path_item in openapi.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in http_methods:
                    continue
                assert operation.get("summary", "").strip(), f"{method.upper()} {path}: missing summary"
                assert operation.get("description", "").strip(), f"{method.upper()} {path}: missing description"

        headers = login(client)
        ensure_database(client, headers, "api")
        schema_resp = execute(client, headers, "api", "SCHEMA")
        assert schema_resp.status_code == 200
        assert "schema" in schema_resp.json()

        page = PAGE.replace("redis-cache", "api-topic")
        wql_md = page.replace("\n", "\\n")
        remember_resp = execute(
            client,
            headers,
            "api",
            f"INSERT INTO pages (slug, markdown) VALUES ('api-topic', '{wql_md}')",
        )
        assert remember_resp.status_code == 200
        assert remember_resp.json()["status"] == "ok"

        read_resp = execute(client, headers, "api", "SELECT * FROM pages WHERE slug = 'api-topic'")
        assert read_resp.status_code == 200
        assert read_resp.json()["node"]["title"] == "Redis Cache"

        search_resp = execute(client, headers, "api", "SEARCH 'redis' BUDGET 256")
        assert search_resp.status_code == 200
        assert search_resp.json()["hits"]

        budget0 = execute(client, headers, "api", "SEARCH 'redis' LIMIT 20 BUDGET 0")
        assert budget0.status_code == 200
        payload = budget0.json()
        assert payload["hits"]
        assert payload["packet"] == []
        assert payload["used_tokens"] == 0
        assert all(hit.get("body") == "" for hit in payload["hits"])


def test_human_crud(wiki: Wiki):
    page = PAGE.replace("redis-cache", "human-page")
    wiki.remember("human-page", markdown=page)
    catalog = wiki.list()
    assert catalog["status"] == "ok"
    assert any(p["slug"] == "human-page" for p in catalog["pages"])

    md = wiki.markdown("human-page")
    assert "human-page" in md
    assert "Redis Cache" in md

    updated = wiki.update("human-page", title="Redis Cache v2", body="Updated body.")
    assert updated["status"] == "ok"
    assert updated["node"]["title"] == "Redis Cache v2"

    wiki.delete("human-page")
    with pytest.raises(RuntimeError):
        wiki.read("human-page")


def test_refresh_preserves_markdown_path_and_media_metadata(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    with Wiki(wiki_dir) as wiki:
        markdown = "---\ntype: concept\nslug: saved-page\ntitle: Saved page\n---\nBody [[linked-page]].\n"
        wiki.remember("saved-page", markdown=markdown, rel_path="Notes/saved.md")

        media_bytes = b"wisedb-media-payload"
        media_markdown = "---\ntype: media\nslug: cover-image\ntitle: Cover image\nsummary: Test caption\n---\nCaption links to [[saved-page]].\n"
        wiki.remember(
            "cover-image",
            file_bytes=media_bytes,
            markdown=media_markdown,
            rel_path="assets/cover.jpg",
        )
        wiki.remember("plain-image", file_bytes=media_bytes, rel_path="assets/plain.png")

        wiki.refresh()

        page = wiki.read("saved-page")["node"]
        assert page["rel_path"] == "Notes/saved.md"
        assert page["is_media"] is False

        media = wiki.read("cover-image")["node"]
        assert media["rel_path"] == "assets/cover.jpg"
        assert media["is_media"] is True
        assert media["mime"] == "image/jpeg"
        assert media["title"] == "Cover image"
        assert media["summary"] == "Test caption"
        assert "Caption links to [[saved-page]]." in wiki.markdown("cover-image")
        assert "mime: image/jpeg" in wiki.markdown("cover-image")

        plain_media = wiki.read("plain-image")["node"]
        assert plain_media["rel_path"] == "assets/plain.png"
        assert plain_media["is_media"] is True
        assert plain_media["mime"] == "image/png"

        wiki.update("saved-page", title="Updated after refresh")
        assert wiki.read("saved-page")["node"]["title"] == "Updated after refresh"
        wiki.delete("saved-page")
        wiki.delete("cover-image")
        wiki.delete("plain-image")


def test_json_boundaries_path_containment_and_media_edit(tmp_path: Path):
    wiki_dir = tmp_path / "wiki"
    wiki = Wiki(wiki_dir)

    body = "A\x01\b\f\n\t\\\"" + ("x" * 9000)
    markdown = f"---\nslug: boundary-page\ntitle: A \"quoted\" title\n---\n{body}"
    stored = wiki.remember("boundary-page", markdown=markdown)
    assert stored["node"]["title"] == 'A "quoted" title'
    assert stored["node"]["body"] == body
    assert wiki.read("boundary-page")["node"]["body"] == body
    assert any(page["slug"] == "boundary-page" for page in wiki.list()["pages"])
    assert wiki.search('"quoted"', budget_tokens=0)["used_tokens"] == 0

    outside = tmp_path / "outside.md"
    with pytest.raises(RuntimeError):
        wiki.remember(
            "outside-page",
            markdown="---\nslug: outside-page\n---\nunsafe",
            rel_path="../outside.md",
        )
    assert not outside.exists()
    symlink_target = tmp_path / "external"
    symlink_target.mkdir()
    (wiki_dir / "alias").symlink_to(symlink_target, target_is_directory=True)
    with pytest.raises(RuntimeError):
        wiki.remember(
            "symlink-page",
            markdown="---\nslug: symlink-page\n---\nunsafe",
            rel_path="alias/page.md",
        )
    (wiki_dir / "alias").unlink()
    unindexed_path = wiki_dir / "pages" / "unindexed.md"
    unindexed_path.write_text("unindexed user file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="conflicts"):
        wiki.remember(
            "unindexed-page",
            markdown="---\nslug: unindexed-page\n---\nunsafe overwrite",
            rel_path="pages/unindexed.md",
        )
    assert unindexed_path.read_text(encoding="utf-8") == "unindexed user file"
    wiki.remember(
        "path-owner",
        markdown="---\nslug: path-owner\n---\nfirst",
        rel_path="pages/path-owner.md",
    )
    with pytest.raises(RuntimeError, match="conflicts"):
        wiki.remember(
            "other-owner",
            markdown="---\nslug: other-owner\n---\nsecond",
            rel_path="pages/path-owner.md",
        )
    with pytest.raises(RuntimeError, match="slug does not match"):
        wiki.remember(
            "requested-slug",
            markdown="---\nslug: different-slug\n---\nbody",
        )
    with pytest.raises(ValueError, match="NUL"):
        wiki._engine.remember("nul-page", markdown="body", rel_path="pages/nul\x00.md")

    media_bytes = b"\x89PNG\r\n\x1a\noriginal-payload"
    media_path = wiki_dir / "assets" / "edit.png"
    wiki.remember("editable-media", file_bytes=media_bytes, rel_path="assets/edit.png")
    original_caption = wiki.markdown("editable-media")
    with pytest.raises(RuntimeError, match="payload already exists"):
        wiki.remember(
            "editable-media",
            markdown="---\nslug: editable-media\ntitle: Must not replace\n---\nnew caption",
            file_bytes=b"replacement-payload",
            rel_path="assets/edit.png",
        )
    assert media_path.read_bytes() == media_bytes
    assert wiki.markdown("editable-media") == original_caption
    edit_markdown = "---\nslug: editable-media\ntitle: Edited caption\n---\nnew caption"
    edited = wiki.edit("editable-media", edit_markdown)
    assert edited["node"]["title"] == "Edited caption"
    assert media_path.read_bytes() == media_bytes
    assert "new caption" in wiki.markdown("editable-media")

    wiki.close()

    reopened = Wiki(wiki_dir)
    assert reopened.read("boundary-page")["node"]["body"] == body
    assert reopened.read("editable-media")["node"]["is_media"] is True
    assert reopened.read("editable-media")["node"]["title"] == "Edited caption"
    assert media_path.read_bytes() == media_bytes
    reopened.close()

    stale_root = tmp_path / "stale-symlink-wiki"
    stale_root.mkdir()
    (stale_root / "pages").mkdir()
    stale_file = stale_root / "pages" / "old.md"
    stale_file.write_text("---\nslug: old\n---\nold body", encoding="utf-8")
    stale_wiki = Wiki(stale_root)
    stale_file.unlink()
    stale_file.symlink_to(tmp_path / "outside-target.md")
    with pytest.raises(RuntimeError, match="refresh failed"):
        stale_wiki.refresh()
    assert stale_wiki.list()["pages"][0]["slug"] == "old"
    stale_file.unlink()
    stale_wiki.close()


def test_owner_lock_refresh_failure_and_atomic_file_replace(tmp_path: Path):
    root = tmp_path / "owner-wiki"
    owner = Wiki(root)
    owner.remember("locked-page", markdown="---\nslug: locked-page\n---\nold")

    with pytest.raises(RuntimeError, match="already owned"):
        Wiki(root)

    source = root / "pages" / "locked-page.md"
    preserved = source.with_name("locked-page.preserved")
    source.rename(preserved)
    source.symlink_to(tmp_path / "outside.md")
    with pytest.raises(RuntimeError, match="refresh failed"):
        owner.refresh()
    assert any(page["slug"] == "locked-page" for page in owner.list()["pages"])
    source.unlink()
    preserved.rename(source)
    assert owner.read("locked-page")["node"]["body"] == "old"

    owner.edit("locked-page", "---\nslug: locked-page\n---\nnew")
    assert owner.read("locked-page")["node"]["body"] == "new"
    assert source.read_text(encoding="utf-8").endswith("new")
    assert not list(source.parent.glob(".wisedb-tmp-*"))

    owner.close()
    reopened = Wiki(root)
    assert reopened.read("locked-page")["node"]["body"] == "new"
    reopened.close()


def test_page_create_is_no_replace_and_metadata_limits_fail_closed(tmp_path: Path):
    root = tmp_path / "strict-wiki"
    page_path = root / "pages" / "safe.md"
    page_path.parent.mkdir(parents=True)
    page_path.write_text("user-owned bytes", encoding="utf-8")
    with Wiki(root) as wiki:
        with pytest.raises(RuntimeError, match="conflicts"):
            wiki.remember("safe", markdown="---\nslug: safe\n---\nnew", rel_path="pages/safe.md")
        assert page_path.read_text(encoding="utf-8") == "user-owned bytes"

    strict_root = tmp_path / "metadata-wiki"
    with Wiki(strict_root) as wiki:
        with pytest.raises(RuntimeError):
            wiki.remember("oversized-title", markdown="---\nslug: oversized-title\ntitle: " + ("t" * 1100) + "\n---\nbody")
        assert wiki.list()["pages"] == []


def test_parser_rejects_frontmatter_and_link_truncation(tmp_path: Path):
    wiki = Wiki(tmp_path / "parser-wiki")
    with pytest.raises(RuntimeError):
        wiki.remember("broken", markdown="---\nslug: broken\ntitle: unclosed")
    with pytest.raises(RuntimeError):
        wiki.remember("too-long", markdown="---\nslug: too-long\ntitle: " + ("x" * 1100) + "\n---\nbody")
    with pytest.raises(RuntimeError):
        wiki.remember("long-link", markdown="---\nslug: long-link\n---\n[[" + ("x" * 300) + "]]" )
    with pytest.raises(RuntimeError):
        wiki.remember("unknown-field", markdown="---\nslug: unknown-field\ncustom: lost\n---\nbody")
    for invalid in (
        "---\nslug: bad-space\ntitle:  leading\n---\nbody",
        "---\nslug: bad-tab\ntitle:\tleading\n---\nbody",
        "---\nslug: bad-tail\ntitle: trailing \n---\nbody",
        "---\nslug : bad-key\n---\nbody",
    ):
        with pytest.raises(RuntimeError):
            wiki.remember("invalid-frontmatter", markdown=invalid)
    valid_crlf = "---\r\nslug: crlf-page\r\ntitle: CRLF page\r\n---\r\nbody"
    assert wiki.remember("crlf-page", markdown=valid_crlf)["node"]["title"] == "CRLF page"
    assert [page["slug"] for page in wiki.list()["pages"]] == ["crlf-page"]
    wiki.close()


def test_engine_rejects_replaced_root_path(tmp_path: Path):
    root = tmp_path / "root-wiki"
    wiki = Wiki(root)
    wiki.remember("before-replace", markdown="---\nslug: before-replace\n---\nbody")
    detached = tmp_path / "detached-root"
    root.rename(detached)
    root.mkdir()
    try:
        with pytest.raises(RuntimeError, match="root pathname changed"):
            wiki.list()
    finally:
        wiki.close()


def test_api_auth_fails_closed_without_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.server_helpers import server_client

    with server_client(monkeypatch, tmp_path, root_password="") as client:
        assert client.get("/users/me").status_code == 503
        assert client.post("/auth/login", json={"username": "root", "password": "x"}).status_code == 503

    with server_client(monkeypatch, tmp_path) as client:
        assert client.get("/users/me").status_code == 401
        denied = client.post("/databases/main/execute", json={"query": "SCHEMA"})
        assert denied.status_code == 401


def test_mcp_data_tools_use_wiki_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("mcp.server.fastmcp")
    import wisedb.mcp_server as mcp_server

    monkeypatch.setenv("WISEDB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WISEDB_WIKI_ID", "mcp")
    monkeypatch.setattr(mcp_server, "_wiki_instance", None)
    markdown = "---\nslug: mcp-page\ntitle: MCP page\n---\nMCP body"
    try:
        remembered = json.loads(mcp_server.wisedb_remember("mcp-page", markdown))
        assert remembered["node"]["slug"] == "mcp-page"
        assert json.loads(mcp_server.wisedb_read("mcp-page"))["node"]["body"] == "MCP body"
        assert "mcp-page" in json.loads(mcp_server.wisedb_list())["pages"][0]["slug"]
        updated = json.loads(mcp_server.wisedb_update("mcp-page", body=""))
        assert updated["node"]["body"] == ""
    finally:
        if mcp_server._wiki_instance is not None:
            mcp_server._wiki_instance._engine.close()
        monkeypatch.setattr(mcp_server, "_wiki_instance", None)


def test_http_human_crud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from tests.server_helpers import ensure_database, execute, login, server_client

    page = PAGE.replace("redis-cache", "human-api")
    wql_md = page.replace("\n", "\\n")

    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "human")
        assert execute(
            client,
            headers,
            "human",
            f"INSERT INTO pages (slug, markdown) VALUES ('human-api', '{wql_md}')",
        ).status_code == 200

        list_resp = execute(client, headers, "human", "SELECT * FROM pages")
        assert list_resp.status_code == 200
        assert any(p["slug"] == "human-api" for p in list_resp.json()["pages"])

        md_resp = execute(client, headers, "human", "SELECT markdown FROM pages WHERE slug = 'human-api'")
        assert md_resp.status_code == 200
        assert "human-api" in md_resp.json()["markdown"]

        patch_resp = execute(
            client,
            headers,
            "human",
            "UPDATE pages SET title = 'Patched Title' WHERE slug = 'human-api'",
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["node"]["title"] == "Patched Title"

        del_resp = execute(client, headers, "human", "DELETE FROM pages WHERE slug = 'human-api'")
        assert del_resp.status_code == 200
        list_after = execute(client, headers, "human", "SELECT * FROM pages")
        assert list_after.status_code == 200
        assert not any(p["slug"] == "human-api" for p in list_after.json()["pages"])
