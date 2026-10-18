from __future__ import annotations

from pathlib import Path

import pytest

from wisedb import Wiki
from wisedb.server.engine.layout import LayoutError, safe_relative_path
from tests.server_helpers import ensure_database, execute, login, server_client

PAGE = """---\ntype: concept\nslug: stable-note\ntitle: Stable note\nsummary: Test\n---\nContent linking to [[other]].\n"""


def test_safe_relative_path_rejects_traversal_and_reserved_dirs(tmp_path: Path):
    for path in ("../outside.md", "_wisedb/data", "folder/../../escape.md", "/absolute.md", "C:/absolute.md"):
        with pytest.raises(LayoutError):
            safe_relative_path(tmp_path, path)


def test_safe_relative_path_rejects_symlink_components(tmp_path: Path):
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "real").mkdir()
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    with pytest.raises(LayoutError):
        safe_relative_path(root, "alias/page.md")


def test_tree_folders_pages_move_and_empty_folder_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wiki_root = tmp_path / "databases" / "files"
    wiki_root.mkdir(parents=True)
    (wiki_root / "Notes").mkdir()
    (wiki_root / "Archive").mkdir()
    wiki = Wiki(wiki_root)
    wiki.remember("stable-note", markdown=PAGE, rel_path="Notes/note.md")
    wiki.close()

    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "files")
        tree = execute(client, headers, "files", "SELECT * FROM layout")
        assert tree.status_code == 200
        assert set(tree.json()["folders"]) == {"Notes", "Archive"}
        assert tree.json()["pages"][0]["slug"] == "stable-note"

        moved = execute(
            client,
            headers,
            "files",
            "MOVE PAGE 'stable-note' TO 'Archive/note.md'",
        )
        assert moved.status_code == 200
        assert moved.json()["page"]["slug"] == "stable-note"
        assert (wiki_root / "Archive/note.md").is_file()
        assert execute(client, headers, "files", "DROP FOLDER 'Notes'").status_code == 200


def test_folder_creation_requires_existing_parent_and_delete_requires_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wiki_root = tmp_path / "databases" / "folders"
    wiki_root.mkdir(parents=True)
    (wiki_root / "Existing").mkdir()

    with server_client(monkeypatch, tmp_path) as client:
        headers = login(client)
        ensure_database(client, headers, "folders")
        assert execute(client, headers, "folders", "CREATE FOLDER 'Missing/Child'").status_code == 422
        assert execute(client, headers, "folders", "CREATE FOLDER 'Existing/Child'").status_code == 200
        assert execute(client, headers, "folders", "DROP FOLDER 'Existing'").status_code == 422
        assert execute(client, headers, "folders", "DROP FOLDER 'Existing/Child'").status_code == 200
