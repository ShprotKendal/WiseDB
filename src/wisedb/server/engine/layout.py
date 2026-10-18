"""Filesystem layout operations for a database root."""

from __future__ import annotations

import re
import shutil
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from wisedb.memory import Wiki


class LayoutError(Exception):
    """Invalid path or layout mutation failure."""

_RESERVED = "_wisedb"
_MAX_SLUG = 256


def _wiki_root(wiki: Wiki) -> Path:
    """Fail closed if the configured pathname no longer names the opened root."""
    if not wiki._engine.root_is_current():
        raise LayoutError("wiki root pathname changed; reopen the server")
    try:
        current = wiki.root.resolve(strict=True)
    except OSError as exc:
        raise LayoutError("wiki root is unavailable") from exc
    if current != wiki.root:
        raise LayoutError("wiki root pathname changed; reopen the server")
    return current


def safe_relative_path(root: Path, value: str, *, allow_empty: bool = False) -> tuple[str, Path]:
    """Normalize a user-supplied wiki-relative path and reject traversal/symlinks."""
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise LayoutError("path must be relative to the wiki root")
    if not normalized:
        if allow_empty:
            return "", root.resolve()
        raise LayoutError("path must not be empty")

    pure = PurePosixPath(normalized)
    if pure.is_absolute() or any(part in {".", ".."} or part.casefold().startswith(_RESERVED) for part in pure.parts):
        raise LayoutError("invalid wiki-relative path")
    if any(not part or "\x00" in part for part in pure.parts):
        raise LayoutError("invalid wiki-relative path")

    relative = pure.as_posix()
    try:
        base = root.resolve(strict=True)
    except OSError as exc:
        raise LayoutError("wiki root is unavailable") from exc
    candidate = base / relative
    current = base
    for part in pure.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise LayoutError("path cannot be inspected") from exc
        if stat.S_ISLNK(mode):
            raise LayoutError("symbolic links are not supported")
    return relative, candidate


def _page_by_slug(wiki: Wiki, slug: str) -> dict[str, Any]:
    if not slug or len(slug) > _MAX_SLUG or "\x00" in slug or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", slug):
        raise LayoutError("invalid slug")
    for page in wiki.list()["pages"]:
        if page["slug"] == slug:
            return page
    raise LayoutError("page not found")


def _with_stable_slug(markdown: str, slug: str) -> str:
    """Ensure path changes don't alter the canonical slug used by wiki links."""
    lines = markdown.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            slug_line = next((i for i in range(1, end) if re.match(r"^\s*slug\s*:", lines[i])), None)
            if slug_line is None:
                lines.insert(1, f"slug: {slug}\n")
            else:
                lines[slug_line] = f"slug: {slug}\n"
            return "".join(lines)
    return f"---\nslug: {slug}\n---\n{markdown}"


def create_folder(wiki: Wiki, path: str) -> dict[str, str]:
    root = _wiki_root(wiki)
    relative, target = safe_relative_path(root, path)
    if target.exists():
        raise LayoutError("path already exists")
    if not target.parent.is_dir():
        raise LayoutError("parent folder not found")
    try:
        target.mkdir(exist_ok=False, mode=0o700)
    except OSError as exc:
        raise LayoutError("folder could not be created") from exc
    return {"status": "ok", "path": relative}


def delete_folder(wiki: Wiki, path: str) -> dict[str, str]:
    return _delete_folder(wiki, path)


def _delete_folder(wiki: Wiki, path: str) -> dict[str, str]:
    root = _wiki_root(wiki)
    relative, target = safe_relative_path(root, path)
    if not target.is_dir():
        raise LayoutError("folder not found")
    try:
        target.rmdir()
    except OSError as exc:
        raise LayoutError("folder is not empty") from exc
    wiki.refresh()
    return {"status": "ok", "path": relative}


def move_folder(wiki: Wiki, source: str, destination: str) -> dict[str, str]:
    return _move_folder(wiki, source, destination)


def _move_folder(wiki: Wiki, source: str, destination: str) -> dict[str, str]:
    if source.strip("/\\").split("/")[0] == "_wisedb" or destination.strip("/\\").split("/")[0] == "_wisedb":
        raise LayoutError("reserved folders cannot be moved")
    root = _wiki_root(wiki)
    source_rel, source_path = safe_relative_path(root, source)
    destination_rel, destination_path = safe_relative_path(root, destination)
    if source_path.is_symlink() or not source_path.is_dir():
        raise LayoutError("folder not found")
    if destination_path.exists() or destination_path.is_symlink():
        raise LayoutError("destination already exists")
    if destination_path.is_relative_to(source_path):
        raise LayoutError("cannot move a folder into itself")
    if not destination_path.parent.is_dir() or destination_path.parent.is_symlink():
        raise LayoutError("destination parent folder not found")

    pages = wiki.list()["pages"]
    try:
        for page in pages:
            page_path = page["rel_path"]
            if not page_path.startswith(f"{source_rel}/"):
                continue
            _, file_path = safe_relative_path(root, page_path)
            if page["is_media"]:
                markdown = _with_stable_slug(wiki.markdown(page["slug"]), page["slug"])
                sidecar = file_path.with_name(file_path.name + ".md")
                sidecar.write_text(markdown, encoding="utf-8", newline="\n")
            else:
                markdown = file_path.read_text(encoding="utf-8")
                stable = _with_stable_slug(markdown, page["slug"])
                if stable != markdown:
                    file_path.write_text(stable, encoding="utf-8", newline="\n")
    except (OSError, UnicodeError) as exc:
        raise LayoutError("folder contents cannot be safely prepared for move") from exc

    try:
        shutil.move(str(source_path), str(destination_path))
    except OSError as exc:
        raise LayoutError("folder move failed") from exc
    wiki.refresh()
    return {"status": "ok", "source": source_rel, "destination": destination_rel}


def move_page(wiki: Wiki, slug: str, destination: str) -> dict[str, Any]:
    return _move_page(wiki, slug, destination)


def _move_page(wiki: Wiki, slug: str, destination: str) -> dict[str, Any]:
    root = _wiki_root(wiki)
    page = _page_by_slug(wiki, slug)
    source_rel, source_path = safe_relative_path(root, page["rel_path"])
    destination_rel, destination_path = safe_relative_path(root, destination)
    if destination_path.exists():
        raise LayoutError("destination already exists")
    if source_path == destination_path:
        raise LayoutError("source and destination are identical")
    if source_path.is_symlink() or destination_path.is_symlink():
        raise LayoutError("symbolic links are not supported")
    if not destination_path.parent.is_dir() or destination_path.parent.is_symlink():
        raise LayoutError("destination parent folder not found")

    if page["is_media"]:
        if not wisedb_media_extension(destination_path):
            raise LayoutError("destination must use a supported media extension")
        markdown = _with_stable_slug(wiki.markdown(slug), slug)
        old_sidecar = source_path.with_name(source_path.name + ".md")
        new_sidecar = destination_path.with_name(destination_path.name + ".md")
        if new_sidecar.exists():
            raise LayoutError("destination sidecar already exists")
        sidecar_renamed = False
        created_sidecar = False
        try:
            if old_sidecar.exists():
                old_sidecar.rename(new_sidecar)
                sidecar_renamed = True
            else:
                created_sidecar = True
                new_sidecar.write_text(markdown, encoding="utf-8", newline="\n")
            source_path.rename(destination_path)
        except OSError as exc:
            # Best-effort compensation; report conflict because the filesystem operation may be partial.
            if sidecar_renamed and new_sidecar.exists():
                try:
                    new_sidecar.rename(old_sidecar)
                except OSError:
                    pass
            elif created_sidecar and new_sidecar.exists():
                try:
                    new_sidecar.unlink()
                except OSError:
                    pass
            raise LayoutError("media move failed; inspect source and destination files") from exc
    else:
        try:
            markdown = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LayoutError("source page cannot be read") from exc
        stable = _with_stable_slug(markdown, slug)
        try:
            if stable != markdown:
                source_path.write_text(stable, encoding="utf-8", newline="\n")
            source_path.rename(destination_path)
        except OSError as exc:
            raise LayoutError("page move failed; inspect source and destination files") from exc

    wiki.refresh()
    moved = _page_by_slug(wiki, slug)
    return {"status": "ok", "source": source_rel, "destination": destination_rel, "page": moved}


def database_tree(wiki: Wiki) -> dict[str, Any]:
    folders: list[str] = []
    for directory in wiki.root.rglob("*"):
        if directory.is_symlink() or not directory.is_dir() or any(part.casefold().startswith(_RESERVED) for part in directory.relative_to(wiki.root).parts):
            continue
        folders.append(directory.relative_to(wiki.root).as_posix())
    return {"status": "ok", "folders": sorted(folders), "pages": wiki.list()["pages"]}


def _page_folder_path(rel_path: str) -> str:
    parent = PurePosixPath(rel_path).parent
    if parent == PurePosixPath("."):
        return ""
    return parent.as_posix()


def _folder_direct_children(parent_path: str, folder_paths: set[str]) -> list[str]:
    children: list[str] = []
    for path in sorted(folder_paths):
        if parent_path:
            prefix = f"{parent_path}/"
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix) :]
            if "/" in rest:
                continue
            children.append(path)
        elif "/" not in path:
            children.append(path)
    return children


def _build_folder_node(path: str, folder_paths: set[str], pages: list[dict[str, Any]]) -> dict[str, Any]:
    name = path.rsplit("/", 1)[-1] if path else ""
    return {
        "path": path,
        "name": name,
        "folders": [_build_folder_node(child, folder_paths, pages) for child in _folder_direct_children(path, folder_paths)],
        "pages": [page for page in pages if _page_folder_path(page.get("rel_path", "")) == path],
    }


def nested_database_tree(wiki: Wiki) -> dict[str, Any]:
    """Nested folder tree plus page catalog entries grouped by `rel_path` parent."""
    flat = database_tree(wiki)
    folder_paths: set[str] = set(flat["folders"])
    for page in flat["pages"]:
        parent = _page_folder_path(page.get("rel_path", ""))
        if parent:
            parts = PurePosixPath(parent).parts
            for index in range(1, len(parts) + 1):
                folder_paths.add(PurePosixPath(*parts[:index]).as_posix())
    pages = flat["pages"]
    tree = {
        "folders": [_build_folder_node(child, folder_paths, pages) for child in _folder_direct_children("", folder_paths)],
        "pages": [page for page in pages if _page_folder_path(page.get("rel_path", "")) == ""],
    }
    return {"status": "ok", "tree": tree}


def media_path(wiki: Wiki, slug: str) -> tuple[dict[str, Any], Path]:
    root = _wiki_root(wiki)
    page = _page_by_slug(wiki, slug)
    if not page["is_media"]:
        raise LayoutError("page is not media")
    _, path = safe_relative_path(root, page["rel_path"])
    if path.is_symlink() or not path.is_file():
        raise LayoutError("media file not found")
    return page, path


def wisedb_media_extension(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".avi"}
