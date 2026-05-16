"""
MCP-сервер WiseDB — транспорт stdio (Cursor / Claude Desktop).

Нужен запущенный HTTP-сервер WiseDB. Переменные окружения:

  export WISEDB_API_BASE="http://127.0.0.1:8000"
  export WISEDB_DATABASE="myapp"
  export WISEDB_PASSWORD="..."
  export WISEDB_API_KEY="..."

Установка::

  pip install -e ".[mcp]"

Пример для Cursor::

  {
    "mcpServers": {
      "wisedb": {
        "command": "/ABS/PATH/WiseDB/.venv/bin/python",
        "args": ["-m", "wisedb.mcp_server"],
        "cwd": "/ABS/PATH/WiseDB",
        "env": {
          "WISEDB_API_BASE": "http://127.0.0.1:8000",
          "WISEDB_API_KEY": "secret"
        }
      }
    }
  }
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wisedb.client import WiseDBClient

_base = os.environ.get("WISEDB_API_BASE", "http://127.0.0.1:8000").rstrip("/")
_db = os.environ.get("WISEDB_DATABASE", "default")
_pw = os.environ.get("WISEDB_PASSWORD", "")
_key = os.environ.get("WISEDB_API_KEY")
_client_ctx = WiseDBClient(_base, database=_db, password=_pw, api_key=_key or None)


def _client() -> WiseDBClient:
    return _client_ctx


mcp = FastMCP(
    "WiseDB",
    instructions=(
        "WiseDB — гибридный RAG в ОЗУ: HNSW + лексика + рёбра графа. "
        "Для выдачи контекста вызывай get_context_bundle. "
        "Для изменений графа — add_context_node и link_context_nodes."
    ),
)


def _looks_like_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def _resolve_chunk_uuid(ref: str, *, tenant_id: str) -> str:
    if _looks_like_uuid(ref):
        return str(uuid.UUID(ref))
    data = _client().lookup_chunk(ref, tenant_id=tenant_id)
    return str(data["id"])


@mcp.tool()
def get_context_bundle(
    query: str,
    max_hops: int = 5,
    max_nodes: int = 48,
    tenant_id: str = "default",
    query_embedding: list[float] | None = None,
) -> str:
    """Markdown для модели: гибридный поиск + расширение по графу.

    Передайте ``query_embedding`` (длина 384), если на HTTP-сервере нет ``WISEDB_EMBEDDING_MODEL``;
    иначе сервер кодирует ``query`` сам.
    """
    payload: dict = {
        "tenant_id": tenant_id,
        "query": query,
        "max_hops": max_hops,
        "max_nodes": max_nodes,
        "return_markdown": True,
    }
    if query_embedding is not None:
        payload["query_embedding"] = query_embedding
    data = _client().retrieve(payload)
    return data.get("markdown") or ""


@mcp.tool()
def add_context_node(
    node_id: str,
    title: str,
    content: str,
    tags: str = "",
    tenant_id: str = "default",
) -> str:
    """Создать или обновить чанк. ``tags`` — через запятую; ``node_id`` — UUID или external_key."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    chunk: dict = {"title": title, "body": content, "tags": tag_list, "metadata": {}}
    if _looks_like_uuid(node_id):
        chunk["id"] = str(uuid.UUID(node_id))
    else:
        chunk["external_key"] = node_id
    _client().upsert_chunks(tenant_id=tenant_id, chunks=[chunk])
    return f"Чанк `{node_id}` записан (upsert)."


@mcp.tool()
def link_context_nodes(
    node_a: str,
    node_b: str,
    relatedness: float = 0.85,
    tenant_id: str = "default",
) -> str:
    """Добавить или обновить ненаправленное ребро между чанками (UUID или external_key)."""
    a = _resolve_chunk_uuid(node_a, tenant_id=tenant_id)
    b = _resolve_chunk_uuid(node_b, tenant_id=tenant_id)
    payload = {
        "source_chunk_id": a,
        "target_chunk_id": b,
        "weight": float(relatedness),
        "directed": False,
    }
    res = _client().upsert_edges(tenant_id=tenant_id, edges=[payload])
    eids = res.get("ids", [])
    eid = eids[0] if eids else "?"
    return f"Ребро `{eid}` между `{a}` ↔ `{b}` (вес={relatedness:.4f})."


@mcp.tool()
def delete_context_node(node_id: str, tenant_id: str = "default") -> str:
    """Мягкое удаление чанка (UUID или external_key)."""
    cid = _resolve_chunk_uuid(node_id, tenant_id=tenant_id)
    _client().delete_chunk(cid, tenant_id=tenant_id)
    return f"Удалён `{node_id}` → `{cid}`."


@mcp.tool()
def list_context_nodes(tenant_id: str = "default", limit: int = 200) -> str:
    """Список чанков: id, при наличии external_key, заголовок."""
    rows = _client().list_chunks(tenant_id=tenant_id, limit=limit)
    if not rows:
        return "(пусто)"
    lines: list[str] = []
    for r in rows:
        ek = r.get("external_key")
        suffix = f" (внешний ключ `{ek}`)" if ek else ""
        lines.append(f"- `{r['id']}`{suffix} — {r.get('title', '')}")
    return "\n".join(lines)


@mcp.tool()
def load_context_graph_json(path: str, tenant_id: str = "default") -> str:
    """Импорт legacy JSON (version 3 ``context_graph``) пакетным upsert на сервер."""
    p = Path(path).expanduser()
    raw = json.loads(p.read_text(encoding="utf-8"))
    if raw.get("version") != 3 or raw.get("model") != "context_graph":
        raise ValueError("ожидается JSON version 3 model context_graph")
    nodes = list(raw.get("nodes", []))
    chunks_payload: list[dict] = []
    for item in nodes:
        cid = str(item["id"])
        chunks_payload.append(
            {
                "external_key": cid,
                "title": item.get("title", ""),
                "body": item.get("content", ""),
                "tags": list(item.get("tags") or []),
                "metadata": dict(item.get("metadata") or {}),
            }
        )
    res = _client().upsert_chunks(tenant_id=tenant_id, chunks=chunks_payload)
    ids = list(res.get("ids") or [])
    if len(ids) != len(nodes):
        return f"Записано {len(ids)} чанков из {len(nodes)}; смотрите лог сервера."
    id_map = {str(nodes[i]["id"]): ids[i] for i in range(len(nodes))}
    edges_payload: list[dict] = []
    for item in raw.get("edges", []):
        a = id_map[str(item["node_a"])]
        b = id_map[str(item["node_b"])]
        edge = {
            "source_chunk_id": a,
            "target_chunk_id": b,
            "weight": float(item.get("relatedness", 0.85)),
            "directed": False,
            "metadata": dict(item.get("metadata") or {}),
        }
        if item.get("id"):
            try:
                edge["id"] = str(uuid.UUID(str(item["id"])))
            except ValueError:
                pass
        edges_payload.append(edge)
    if edges_payload:
        _client().upsert_edges(tenant_id=tenant_id, edges=edges_payload)
    return f"Импортировано узлов: {len(ids)}, рёбер: {len(edges_payload)} из `{p.resolve()}`."


@mcp.tool()
def save_context_graph_json(path: str) -> str:
    """Состояние в ОЗУ процесса API; снимок — свой экспорт (см. docs/DEPLOYMENT.md)."""
    return (
        "WiseDB держит состояние в ОЗУ на хосте API. "
        f"Автосохранения в `{path}` нет. Экспортируйте чанки/рёбра через HTTP API или свой снимок. "
        "См. docs/DEPLOYMENT.md."
    )


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
