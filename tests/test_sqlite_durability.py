from __future__ import annotations

import base64

import pytest

from wisedb.server.memory_engine import NeuralContextDB
from wisedb.server.schemas import ChunkUpsertItem
from wisedb.server.constants import EMBEDDING_DIM


@pytest.mark.asyncio
async def test_sqlite_reload_keeps_chunks(tmp_path) -> None:
    p = tmp_path / "store.sqlite"
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0

    db1 = NeuralContextDB(dim=EMBEDDING_DIM, max_elements=10_000, sqlite_path=p)
    await db1.hydrate_from_sqlite()
    rec = await db1.upsert_chunk(
        "default",
        ChunkUpsertItem(external_key="k", title="t", body="b", embedding=vec),
    )
    cid = rec.id

    db2 = NeuralContextDB(dim=EMBEDDING_DIM, max_elements=10_000, sqlite_path=p)
    await db2.hydrate_from_sqlite()
    got = await db2.get_chunk("default", cid)
    assert got is not None
    assert got.title == "t"
    assert got.embedding is not None
    assert got.embedding[0] == pytest.approx(1.0)

    dense = await db2.search_dense_ids("default", vec, top_k=3)
    assert str(cid) in dense


@pytest.mark.asyncio
async def test_sqlite_reload_keeps_attachment_meta_and_blob(tmp_path) -> None:
    p = tmp_path / "store.sqlite"
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    png = b"fakepng"
    b64 = base64.b64encode(png).decode("ascii")

    db1 = NeuralContextDB(dim=EMBEDDING_DIM, max_elements=10_000, sqlite_path=p)
    await db1.hydrate_from_sqlite()
    rec = await db1.upsert_chunk(
        "default",
        ChunkUpsertItem(
            external_key="img_chunk",
            title="t",
            body="b",
            embedding=vec,
            attachments=[{"slot": "a1", "mime": "image/png", "data_base64": b64}],
        ),
    )
    cid = rec.id

    db2 = NeuralContextDB(dim=EMBEDDING_DIM, max_elements=10_000, sqlite_path=p)
    await db2.hydrate_from_sqlite()
    got = await db2.get_chunk("default", cid)
    assert got is not None
    assert len(got.attachments) == 1
    assert got.attachments[0].slot == "a1"
    assert got.attachments[0].byte_length == len(png)
    blob = await db2.read_attachment_bytes("default", cid, "a1")
    assert blob is not None
    assert blob[0] == png
    assert blob[1] == "image/png"
