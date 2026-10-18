"""WiseDB agent long-term memory (v3)."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from wisedb._engine import Engine

__version__ = "3.0.0"
__all__ = ["Wiki", "schema", "__version__"]


def schema() -> str:
    """Return agent-facing WikiLLM schema text."""
    optics = (
        "\n\n## Optics (custom layers)\n"
        "- Built-in `content` optic is page markdown (`pages` table and REST content routes).\n"
        "- `CREATE OPTIC name [JSON|IMAGE|VIDEO|LINK|FILE|SPAN|MARKDOWN] ['description']` / `DROP OPTIC name` (database admin).\n"
        "- `SELECT * FROM optics [WHERE slug = '...' [AND name = '...']]` — stored layer text rows.\n"
        "- `UPDATE optics SET text = '...' WHERE slug = '...' AND name = '...'`\n"
        "- `DELETE FROM optics WHERE slug = '...' AND name = '...'`\n"
    )
    return Engine.schema() + optics


class Wiki:
    """Long-term memory for an LLM agent backed by a file-catalog wiki."""

    def __init__(self, root: str | Path) -> None:
        requested_root = Path(root)
        self._lock = threading.RLock()
        self._engine = Engine(str(requested_root))
        self.root = requested_root.resolve(strict=True)

    def __enter__(self) -> Wiki:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def remember(
        self,
        slug: str,
        *,
        markdown: str | None = None,
        file_bytes: bytes | None = None,
        rel_path: str | None = None,
    ) -> dict[str, Any]:
        """Store a markdown page or media file as a graph node."""
        with self._lock:
            raw = self._engine.remember(slug, markdown, file_bytes, rel_path)
            return json.loads(raw)

    def read(self, slug: str, *, neighbors: bool = False) -> dict[str, Any]:
        """Read a node by slug."""
        with self._lock:
            raw = self._engine.read(slug, neighbors)
            return json.loads(raw)

    def edit(self, slug: str, markdown: str) -> dict[str, Any]:
        """Edit an existing markdown page or media sidecar."""
        with self._lock:
            raw = self._engine.edit(slug, markdown)
            return json.loads(raw)

    def search(
        self,
        query: str,
        *,
        budget_tokens: int = 2048,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """Search wiki; optional context packet within token budget."""
        with self._lock:
            raw = self._engine.search(query, budget_tokens, top_k)
            return json.loads(raw)

    def list(self) -> dict[str, Any]:
        """List all pages (catalog metadata, no bodies)."""
        with self._lock:
            raw = self._engine.list()
            return json.loads(raw)

    def list_catalog(
        self,
        *,
        filter_json: str | None = None,
        order_field: str | None = None,
        order_asc: bool = True,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """List catalog pages with optional native filter, ordering, and limit."""
        with self._lock:
            raw = self._engine.list_catalog(
                filter_json,
                order_field,
                order_asc,
                limit if limit is not None else -1,
            )
            return json.loads(raw)

    def markdown(self, slug: str) -> str:
        """Read raw markdown (frontmatter + body) for a slug."""
        with self._lock:
            return self._engine.read_markdown(slug)

    def update(
        self,
        slug: str,
        *,
        type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        """Update selected fields of an existing page."""
        with self._lock:
            raw = self._engine.update(slug, type, title, summary, body)
            return json.loads(raw)

    def create_folder(self, path: str) -> None:
        from wisedb.server.engine.layout import create_folder

        with self.serialized():
            create_folder(self, path)

    def delete_folder(self, path: str) -> None:
        from wisedb.server.engine.layout import delete_folder

        with self.serialized():
            delete_folder(self, path)

    def move_folder(self, source: str, destination: str) -> dict[str, Any]:
        from wisedb.server.engine.layout import move_folder

        with self.serialized():
            return move_folder(self, source, destination)

    def move_page(self, slug: str, destination: str) -> dict[str, Any]:
        from wisedb.server.engine.layout import move_page

        with self.serialized():
            return move_page(self, slug, destination)

    def delete(self, slug: str) -> None:
        """Delete a page and its disk files."""
        with self._lock:
            from wisedb.optics import clear_slug_from_all_optics

            self._engine.delete(slug)
            clear_slug_from_all_optics(self.root, slug)

    def list_optics(self) -> list[dict[str, str]]:
        from wisedb.optics import CONTENT_OPTIC, list_custom_optics

        with self._lock:
            built_in = {
                "name": CONTENT_OPTIC,
                "description": "WikiLLM markdown page content",
                "kind": "markdown",
            }
            custom = [r.to_dict() for r in list_custom_optics(self.root)]
            return [built_in, *custom]

    def create_optic(
        self,
        name: str,
        *,
        description: str = "",
        kind: str = "markdown",
    ) -> dict[str, str]:
        from wisedb.optics import create_optic

        with self._lock:
            return create_optic(self.root, name, description=description, kind=kind).to_dict()

    def update_optic(self, name: str, *, description: str) -> dict[str, str]:
        from wisedb.optics import update_optic_description

        with self._lock:
            return update_optic_description(self.root, name, description).to_dict()

    def drop_optic(self, name: str) -> None:
        from wisedb.optics import drop_optic

        with self._lock:
            drop_optic(self.root, name)

    def optic_text(self, optic: str, slug: str) -> str:
        from wisedb.optics import read_optic_text

        with self._lock:
            return read_optic_text(self.root, optic, slug)

    def optic_value(self, optic: str, slug: str) -> dict[str, Any]:
        from wisedb.optics import read_optic_value

        with self._lock:
            return read_optic_value(self.root, optic, slug)

    def set_optic_text(self, optic: str, slug: str, text: str) -> None:
        from wisedb.optics import write_optic_text

        with self._lock:
            write_optic_text(self.root, optic, slug, text)

    def bind_optic_span(
        self,
        optic: str,
        slug: str,
        *,
        path: str,
        start_line: int,
        end_line: int,
    ) -> dict[str, Any]:
        from wisedb.optics import bind_optic_span

        with self._lock:
            return bind_optic_span(
                self.root,
                optic,
                slug,
                path=path,
                start_line=start_line,
                end_line=end_line,
            )

    def edit_optic_span(
        self,
        optic: str,
        slug: str,
        *,
        text: str,
        base_sha256: str,
    ) -> dict[str, Any]:
        from wisedb.optics import write_optic_span_edit

        with self._lock:
            return write_optic_span_edit(
                self.root,
                optic,
                slug,
                text=text,
                base_sha256=base_sha256,
            )

    def optic_bytes(self, optic: str, slug: str) -> tuple[bytes, dict[str, Any]]:
        from wisedb.optics import read_optic_bytes

        with self._lock:
            return read_optic_bytes(self.root, optic, slug)

    def set_optic_bytes(
        self,
        optic: str,
        slug: str,
        data: bytes,
        *,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        from wisedb.optics import write_optic_bytes

        with self._lock:
            return write_optic_bytes(
                self.root,
                optic,
                slug,
                data,
                filename=filename,
                media_type=media_type,
            )

    def clear_optic_text(self, optic: str, slug: str) -> None:
        from wisedb.optics import clear_optic_text

        with self._lock:
            clear_optic_text(self.root, optic, slug)

    def list_optic_texts(
        self,
        *,
        name: str | None = None,
        slug: str | None = None,
    ) -> list[dict[str, str]]:
        from wisedb.optics import list_optic_text_rows

        with self._lock:
            return list_optic_text_rows(self.root, name=name, slug=slug)

    def links(
        self,
        slug: str,
        *,
        depth: int = 1,
        direction: str = "outgoing",
    ) -> dict[str, Any]:
        """Walk links_to edges from a page with depth and direction."""
        with self._lock:
            raw = self._engine.links(slug, depth, direction)
            return json.loads(raw)

    def query_edges(
        self,
        *,
        from_slug: str | None = None,
        to_slug: str | None = None,
        edge_type: str | None = None,
    ) -> dict[str, Any]:
        """List graph edges matching optional endpoint filters."""
        with self._lock:
            raw = self._engine.query_edges(from_slug, to_slug, edge_type)
            return json.loads(raw)

    def query(self, source: str) -> dict[str, Any]:
        """Execute a WQL program and return the same JSON as the underlying API."""
        from wisedb.wql import execute

        return execute(self, source)

    def refresh(self) -> None:
        """Rescan disk after human-facing file operations."""
        with self._lock:
            self._engine.refresh()

    def settings(self) -> dict[str, Any]:
        """Return per-database settings (for example semantic linking)."""
        from wisedb.workspace import read_workspace

        with self._lock:
            raw = json.loads(self._engine.get_settings())
            raw.update(read_workspace(self.root))
            return raw

    def update_settings(self, *, semantics: bool) -> dict[str, Any]:
        """Update per-database settings and rebuild the in-memory graph."""
        from wisedb.workspace import read_workspace

        with self._lock:
            raw = self._engine.set_semantics(semantics)
            merged = json.loads(raw)
            merged.update(read_workspace(self.root))
            return merged

    def set_code_root(self, code_root: str | None) -> dict[str, Any]:
        from wisedb.workspace import read_workspace, set_code_root

        with self._lock:
            set_code_root(self.root, code_root)
            merged = json.loads(self._engine.get_settings())
            merged.update(read_workspace(self.root))
            return merged


    def close(self) -> None:
        """Close the exclusive native storage owner."""
        with self._lock:
            self._engine.close()

    @contextmanager
    def serialized(self) -> Iterator[None]:
        """Serialize complete operations on this in-process engine owner."""
        with self._lock:
            yield
