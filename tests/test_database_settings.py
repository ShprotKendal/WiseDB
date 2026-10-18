"""HTTP tests for per-database settings and semantic linking."""

from __future__ import annotations

import pytest

from tests.server_helpers import ensure_database, execute, login, server_client


def test_semantics_settings_and_edges(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "sem-db")

        resp = client.get("/databases/sem-db/settings", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"semantics": False, "code_root": None}

        patch = client.patch(
            "/databases/sem-db/settings",
            headers=headers,
            json={"semantics": True},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json() == {"semantics": True, "code_root": None}

        target_md = (
            "---\n"
            "type: concept\n"
            "slug: sem-target\n"
            "title: Semantic Target\n"
            "summary: s\n"
            "---\n"
            "Target body.\n"
        )
        source_md = (
            "---\n"
            "type: concept\n"
            "slug: sem-source\n"
            "title: Semantic Source\n"
            "summary: s\n"
            "---\n"
            "Points at {{Semantic Target}}.\n"
        )
        for slug, md in (("sem-target", target_md), ("sem-source", source_md)):
            ins = client.post(
                "/databases/sem-db/nodes",
                headers=headers,
                json={"slug": slug, "markdown": md},
            )
            assert ins.status_code == 200, ins.text

        edges = execute(
            client,
            headers,
            "sem-db",
            "SELECT * FROM edges WHERE from_slug = 'sem-source' AND type = 'links_to'",
        )
        assert edges.status_code == 200, edges.text
        payload = edges.json()
        rows = payload if isinstance(payload, list) else payload.get("edges", [])
        assert any(row.get("to_slug") == "sem-target" for row in rows)
