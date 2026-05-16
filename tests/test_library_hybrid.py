"""Hybrid persistence: chunk library on disk, edges in SQLite."""

from __future__ import annotations

import base64
import uuid

import pytest

from wisedb.server.constants import EMBEDDING_DIM
from wisedb.server.memory_engine import NeuralContextDB
from wisedb.server.schemas import ChunkUpsertItem, EdgeUpsertItem


@pytest.mark.asyncio
async def test_hybrid_chunk_files_and_graph_sqlite(tmp_path) -> None:
    lib = tmp_path / "lib"
    graph = tmp_path / "edges.sqlite"
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    png = b"\x89PNG\r\n\x1a\nxx"
    b64 = base64.b64encode(png).decode("ascii")

    db1 = NeuralContextDB(
        dim=EMBEDDING_DIM,
        max_elements=10_000,
        library_path=lib,
        sqlite_path=graph,
    )
    await db1.hydrate_from_sqlite()
    rec = await db1.upsert_chunk(
        "default",
        ChunkUpsertItem(
            external_key="doc1",
            title="A",
            body="body",
            embedding=vec,
            attachments=[{"slot": "p1", "mime": "image/png", "data_base64": b64}],
        ),
    )
    cid = rec.id
    eid = uuid.uuid4()
    await db1.upsert_edge(
        "default",
        EdgeUpsertItem(
            id=eid,
            source_chunk_id=cid,
            target_chunk_id=cid,
            weight=0.5,
            directed=False,
        ),
    )

    chunk_dir = lib / "default" / "chunks" / str(cid)
    assert (chunk_dir / "chunk.json").is_file()

    db2 = NeuralContextDB(
        dim=EMBEDDING_DIM,
        max_elements=10_000,
        library_path=lib,
        sqlite_path=graph,
    )
    await db2.hydrate_from_sqlite()
    got = await db2.get_chunk("default", cid)
    assert got is not None
    assert got.title == "A"
    assert len(got.attachments) == 1
    blob = await db2.read_attachment_bytes("default", cid, "p1")
    assert blob is not None
    assert blob[0] == png

    async with db2._lock:
        assert eid in db2.edges


@pytest.mark.asyncio
async def test_hybrid_default_graph_under_dot_wisedb(tmp_path) -> None:
    lib = tmp_path / "lib"
    db = NeuralContextDB(dim=EMBEDDING_DIM, max_elements=100, library_path=lib)
    assert db._sqlite_path == lib / ".wisedb" / "graph.sqlite"
