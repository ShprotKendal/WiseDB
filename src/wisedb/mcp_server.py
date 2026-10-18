"""MCP tools exposing remember / read / edit / search."""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wisedb.memory import Wiki, schema

mcp = FastMCP("wisedb")

_wiki_instance: Wiki | None = None


def _get_wiki() -> Wiki:
    global _wiki_instance
    if _wiki_instance is None:
        root = Path(os.environ.get("WISEDB_DATA_DIR", "./data")) / os.environ.get(
            "WISEDB_WIKI_ID", "default"
        )
        _wiki_instance = Wiki(root)
    return _wiki_instance


@mcp.tool()
def wisedb_schema() -> str:
    """Return WikiLLM page format and operation guide for agents."""
    return schema()


@mcp.tool()
def wisedb_remember(slug: str, markdown: str, rel_path: str = "") -> str:
    """Remember a WikiLLM markdown page under slug."""
    result = _get_wiki().remember(slug, markdown=markdown, rel_path=rel_path or None)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_read(slug: str, neighbors: bool = False) -> str:
    """Read a page by slug; optionally include 1-hop neighbor summaries."""
    result = _get_wiki().read(slug, neighbors=neighbors)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_edit(slug: str, markdown: str) -> str:
    """Edit an existing page."""
    result = _get_wiki().edit(slug, markdown)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_search(query: str, budget_tokens: int = 2048, top_k: int = 10) -> str:
    """Search wiki and return hits plus optional context packet."""
    result = _get_wiki().search(query, budget_tokens=budget_tokens, top_k=top_k)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_list() -> str:
    """List all wiki pages (catalog metadata)."""
    result = _get_wiki().list()
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_read_markdown(slug: str) -> str:
    """Read raw markdown (frontmatter + body) for a page."""
    return _get_wiki().markdown(slug)


@mcp.tool()
def wisedb_update(
    slug: str,
    type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
) -> str:
    """Update selected fields of an existing page."""
    result = _get_wiki().update(
        slug,
        type=type,
        title=title,
        summary=summary,
        body=body,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def wisedb_delete(slug: str) -> str:
    """Delete a page and its disk files."""
    _get_wiki().delete(slug)
    return json.dumps({"status": "ok"}, ensure_ascii=False)


@mcp.tool()
def wisedb_query(query: str) -> str:
    """Execute a WQL program and return JSON in the same shape as the HTTP API."""
    result = _get_wiki().query(query)
    return json.dumps(result, ensure_ascii=False)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
