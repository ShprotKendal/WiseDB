"""Per-database workspace settings (code root for span optics)."""

from __future__ import annotations

import json
from pathlib import Path

_WISEDB = "_wisedb"
_WORKSPACE_FILE = "workspace.json"


class WorkspaceError(Exception):
    """Invalid workspace configuration."""


def _workspace_path(wiki_root: Path) -> Path:
    return wiki_root / _WISEDB / _WORKSPACE_FILE


def read_workspace(wiki_root: Path) -> dict[str, str]:
    path = _workspace_path(wiki_root)
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    code_root = raw.get("code_root")
    if code_root is None:
        return {}
    if not isinstance(code_root, str) or not code_root.strip():
        return {}
    return {"code_root": code_root.strip()}


def code_root_path(wiki_root: Path) -> Path | None:
    data = read_workspace(wiki_root)
    root = data.get("code_root")
    if not root:
        return None
    path = Path(root).expanduser()
    if not path.is_absolute():
        raise WorkspaceError("code_root must be an absolute path")
    if not path.is_dir():
        raise WorkspaceError("code_root is not a directory")
    return path.resolve()


def set_code_root(wiki_root: Path, code_root: str | None) -> dict[str, str]:
    path = _workspace_path(wiki_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if code_root is None or code_root == "":
        if path.is_file():
            path.unlink()
        return {}
    normalized = str(Path(code_root).expanduser().resolve())
    if not Path(normalized).is_dir():
        raise WorkspaceError("code_root is not a directory")
    payload = {"code_root": normalized}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload
