"""Хранилище контекста в процессе: чанки в ОЗУ, HNSW, лексический индекс, рёбра графа."""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import numpy as np

from wisedb.server.constants import EMBEDDING_DIM
from wisedb.server.schemas import AttachmentPublic, ChunkPublic, ChunkUpsertItem, EdgeUpsertItem


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.split(r"[^\w]+", text, flags=re.UNICODE) if len(t) >= 2}


def _lexical_key(tenant_id: str, token: str) -> tuple[str, str]:
    return (tenant_id, token)


def _metadata_pinned(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("wisedb_pinned") or metadata.get("pinned"))


def _metadata_reinforced_at(metadata: dict[str, Any]) -> Optional[datetime]:
    raw = metadata.get("wisedb_last_reinforced_at")
    if raw is None:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _sleep_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _sleep_tags(value: Any, fallback: Iterable[str]) -> list[str]:
    raw = value if isinstance(value, list) else list(fallback)
    out: list[str] = []
    for tag in raw:
        t = str(tag).strip().lower()
        if not t:
            continue
        if len(t) > 64:
            t = t[:64].rstrip()
        out.append(t)
    return sorted(set(out))[:16]


def _sleep_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in value.items():
        key = str(k).strip()
        if not key or len(key) > 96:
            continue
        if key in {"llm_api_key", "api_key", "authorization", "password", "secret"}:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[key] = _sleep_text(v, 1000) if isinstance(v, str) else v
    return out


def _sleep_action_preview(actions: list[Any]) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        row: dict[str, Any] = {
            "type": _sleep_text(action.get("type"), 32),
            "chunk_id": _sleep_text(action.get("chunk_id"), 64),
        }
        if action.get("title"):
            row["title"] = _sleep_text(action.get("title"), 180)
        if action.get("reason"):
            row["reason"] = _sleep_text(action.get("reason"), 300)
        if isinstance(action.get("chunks"), list):
            row["split_count"] = len(action["chunks"])
        preview.append(row)
    return preview


@dataclass(frozen=True)
class AttachmentRecord:
    id: uuid.UUID
    tenant_id: str
    chunk_id: uuid.UUID
    slot: str
    mime: str
    uri: Optional[str]
    byte_length: int

    def to_public(self) -> AttachmentPublic:
        return AttachmentPublic(
            id=self.id,
            slot=self.slot,
            mime=self.mime,
            uri=self.uri,
            byte_length=self.byte_length,
        )


@dataclass
class ChunkRecord:
    id: uuid.UUID
    tenant_id: str
    external_key: Optional[str]
    title: str
    body: str
    tags: tuple[str, ...]
    metadata_json: dict[str, Any]
    embedding: Optional[list[float]]
    version: int
    deleted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    attachments: tuple[AttachmentRecord, ...] = ()

    def to_public(self) -> ChunkPublic:
        return ChunkPublic(
            id=self.id,
            tenant_id=self.tenant_id,
            external_key=self.external_key,
            title=self.title,
            body=self.body,
            tags=list(self.tags),
            metadata=dict(self.metadata_json),
            attachments=[a.to_public() for a in self.attachments],
            version=self.version,
            deleted_at=self.deleted_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass
class EdgeRecord:
    id: uuid.UUID
    tenant_id: str
    source_chunk_id: uuid.UUID
    target_chunk_id: uuid.UUID
    weight: float
    directed: bool
    relation_type: Optional[str]
    metadata_json: dict[str, Any]
    deleted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class NeuralContextDB:
    """
    Один процесс, данные в ОЗУ:

    - **HNSW** (hnswlib), косинус по эмбеддингам чанков
    - **Лексика**: токен → множество чанков
    - **Граф**: взвешенные рёбра (таблица в ОЗУ)

    **Долговечность**

    - **Монолитный SQLite** (``library_path`` пуст): в ``sqlite_path`` — чанки, рёбра, вложения.
    - **Гибрид** (``library_path`` задан): каталог
      ``{library_path}/{tenant}/chunks/{uuid}/`` (``chunk.json``, ``attachments/``);
      в ``sqlite_path`` — **только рёбра** (по умолчанию ``{library}/.wisedb/graph.sqlite``).
    """

    def __init__(
        self,
        *,
        dim: int = EMBEDDING_DIM,
        max_elements: int = 500_000,
        hnsw_ef: int = 64,
        hnsw_m: int = 32,
        sqlite_path: Optional[str | Path] = None,
        library_path: Optional[str | Path] = None,
    ) -> None:
        self.dim = dim
        self.max_elements = max_elements
        self._lock = asyncio.Lock()
        self._library_path: Optional[Path] = Path(library_path) if library_path else None
        if self._library_path is not None:
            self._library_path.mkdir(parents=True, exist_ok=True)
            self._sqlite_path: Optional[Path] = (
                Path(sqlite_path) if sqlite_path else self._library_path / ".wisedb" / "graph.sqlite"
            )
        else:
            self._sqlite_path = Path(sqlite_path) if sqlite_path else None

        import hnswlib

        self._hnsw = hnswlib.Index(space="cosine", dim=dim)
        self._hnsw.init_index(max_elements=max_elements, ef_construction=200, M=hnsw_m)
        self._hnsw.set_ef(hnsw_ef)

        self._label_to_ref: dict[int, tuple[str, uuid.UUID]] = {}
        self._ref_to_label: dict[tuple[str, uuid.UUID], int] = {}
        self._next_label = 0

        self.chunks: dict[uuid.UUID, ChunkRecord] = {}
        self.edges: dict[uuid.UUID, EdgeRecord] = {}
        self._external: dict[tuple[str, str], uuid.UUID] = {}
        self._lexical: dict[tuple[str, str], set[uuid.UUID]] = {}
        self._attachment_blob_ram: dict[tuple[str, uuid.UUID, str], tuple[bytes, str]] = {}

    async def hydrate_from_sqlite(self) -> None:
        """Загрузить снимок из SQLite в ОЗУ (HNSW + лексика). Если нечего грузить — ничего."""
        from wisedb.server import sqlite_persist as sp

        if self._library_path is not None:
            graph = self._sqlite_path
            assert graph is not None
            from wisedb.server import library_persist as lp

            await asyncio.to_thread(sp.init_graph_schema, graph)
            chunks = await asyncio.to_thread(lp.load_all_chunks, self._library_path)
            edges = await asyncio.to_thread(sp.load_edges, graph)
        elif self._sqlite_path is not None:
            p = self._sqlite_path
            await asyncio.to_thread(sp.init_schema, p)
            chunks, edges = await asyncio.to_thread(sp.load_all, p)
        else:
            return
        async with self._lock:
            for c in sorted(chunks, key=lambda x: str(x.id)):
                self.chunks[c.id] = c
                if c.deleted_at is None:
                    if c.external_key:
                        self._external[(c.tenant_id, c.external_key)] = c.id
                    self._lex_add_chunk(c)
                    if c.embedding:
                        self._hnsw_attach(c.tenant_id, c.id, c.embedding)
            for e in edges:
                self.edges[e.id] = e

    async def _persist_chunk(self, c: ChunkRecord) -> None:
        if self._library_path is not None:
            return
        if self._sqlite_path is None:
            return
        from wisedb.server.sqlite_persist import upsert_chunk_row

        snap = replace(
            c,
            tags=tuple(c.tags),
            metadata_json=dict(c.metadata_json),
            embedding=list(c.embedding) if c.embedding is not None else None,
        )
        await asyncio.to_thread(upsert_chunk_row, self._sqlite_path, snap)

    async def _persist_edge(self, e: EdgeRecord) -> None:
        if self._sqlite_path is None:
            return
        from wisedb.server.sqlite_persist import upsert_edge_row

        snap = replace(
            e,
            metadata_json=dict(e.metadata_json),
        )
        await asyncio.to_thread(upsert_edge_row, self._sqlite_path, snap)

    def _alloc_label(self) -> int:
        if self._next_label >= self.max_elements:
            new_cap = int(self.max_elements * 1.5) + 1
            self._hnsw.resize_index(new_cap)
            self.max_elements = new_cap
        lid = self._next_label
        self._next_label += 1
        return lid

    def _chunk_text_for_lex(self, c: ChunkRecord) -> str:
        return "\n".join([c.title, c.body, " ".join(c.tags)])

    def _lex_add_chunk(self, c: ChunkRecord) -> None:
        if c.deleted_at is not None:
            return
        for tok in _tokenize(self._chunk_text_for_lex(c)):
            self._lexical.setdefault(_lexical_key(c.tenant_id, tok), set()).add(c.id)

    def _lex_remove_chunk(self, c: ChunkRecord) -> None:
        for tok in _tokenize(self._chunk_text_for_lex(c)):
            key = _lexical_key(c.tenant_id, tok)
            if key in self._lexical:
                self._lexical[key].discard(c.id)
                if not self._lexical[key]:
                    del self._lexical[key]

    def _hnsw_detach(self, tenant_id: str, cid: uuid.UUID) -> None:
        ref = (tenant_id, cid)
        lid = self._ref_to_label.pop(ref, None)
        if lid is not None:
            self._label_to_ref.pop(lid, None)
            try:
                self._hnsw.mark_deleted(lid)
            except Exception:
                pass

    def _hnsw_attach(self, tenant_id: str, cid: uuid.UUID, vector: list[float]) -> None:
        self._hnsw_detach(tenant_id, cid)
        vec = np.asarray(vector, dtype=np.float32).reshape(1, self.dim)
        lid = self._alloc_label()
        self._hnsw.add_items(vec, np.asarray([lid], dtype=np.int64))
        self._label_to_ref[lid] = (tenant_id, cid)
        self._ref_to_label[(tenant_id, cid)] = lid

    def _clear_attachment_ram(self, tenant_id: str, chunk_id: uuid.UUID) -> None:
        drop = [k for k in self._attachment_blob_ram if k[0] == tenant_id and k[1] == chunk_id]
        for k in drop:
            del self._attachment_blob_ram[k]

    def _build_attachment_payloads(
        self, tenant_id: str, chunk_id: uuid.UUID, item: ChunkUpsertItem
    ) -> tuple[tuple[AttachmentRecord, ...], list[dict[str, Any]]]:
        records: list[AttachmentRecord] = []
        rows: list[dict[str, Any]] = []
        for a in item.attachments:
            aid = uuid.uuid4()
            data: Optional[bytes] = None
            if a.data_base64:
                try:
                    data = base64.b64decode(a.data_base64)
                except binascii.Error as e:
                    raise ValueError(f"вложение {a.slot!r}: неверный base64") from e
            blen = len(data) if data is not None else 0
            records.append(
                AttachmentRecord(
                    id=aid,
                    tenant_id=tenant_id,
                    chunk_id=chunk_id,
                    slot=a.slot,
                    mime=a.mime,
                    uri=a.uri,
                    byte_length=blen,
                )
            )
            rows.append(
                {
                    "id": aid,
                    "tenant_id": tenant_id,
                    "chunk_id": chunk_id,
                    "slot": a.slot,
                    "mime": a.mime,
                    "uri": a.uri,
                    "data": data,
                }
            )
        order = sorted(range(len(records)), key=lambda i: records[i].slot)
        records = [records[i] for i in order]
        rows = [rows[i] for i in order]
        return tuple(records), rows

    async def upsert_chunk(self, tenant_id: str, item: ChunkUpsertItem) -> ChunkRecord:
        if item.embedding is not None and len(item.embedding) != self.dim:
            raise ValueError(f"эмбеддинг должен быть длины {self.dim}")
        async with self._lock:
            now = datetime.now(timezone.utc)
            chunk: Optional[ChunkRecord] = None
            if item.external_key:
                eid = self._external.get((tenant_id, item.external_key))
                if eid is not None:
                    cand = self.chunks.get(eid)
                    if cand is None or cand.deleted_at is not None:
                        self._external.pop((tenant_id, item.external_key), None)
                    else:
                        chunk = cand
            if chunk is None and item.id is not None:
                chunk = self.chunks.get(item.id)
                if chunk is not None and chunk.deleted_at is not None:
                    chunk = None

            if chunk is None:
                cid = item.id or uuid.uuid4()
                chunk = ChunkRecord(
                    id=cid,
                    tenant_id=tenant_id,
                    external_key=item.external_key,
                    title=item.title,
                    body=item.body,
                    tags=tuple(sorted({t.strip().lower() for t in item.tags if t.strip()})),
                    metadata_json=dict(item.metadata),
                    embedding=item.embedding,
                    version=1,
                    deleted_at=None,
                    created_at=now,
                    updated_at=now,
                    attachments=(),
                )
                self.chunks[cid] = chunk
                if chunk.external_key:
                    self._external[(tenant_id, chunk.external_key)] = cid
                self._lex_add_chunk(chunk)
                if chunk.embedding:
                    self._hnsw_attach(tenant_id, cid, chunk.embedding)
                out = chunk
            else:
                self._lex_remove_chunk(chunk)
                old_ext = chunk.external_key
                if old_ext and self._external.get((tenant_id, old_ext)) == chunk.id:
                    if item.external_key != old_ext:
                        self._external.pop((tenant_id, old_ext), None)
                self._hnsw_detach(tenant_id, chunk.id)

                chunk.tenant_id = tenant_id
                chunk.external_key = item.external_key if item.external_key is not None else chunk.external_key
                chunk.title = item.title
                chunk.body = item.body
                chunk.tags = tuple(sorted({t.strip().lower() for t in item.tags if t.strip()}))
                chunk.metadata_json = dict(item.metadata)
                if item.embedding is not None:
                    chunk.embedding = item.embedding
                chunk.version += 1
                chunk.updated_at = now
                if chunk.external_key:
                    self._external[(tenant_id, chunk.external_key)] = chunk.id
                self._lex_add_chunk(chunk)
                if chunk.embedding:
                    self._hnsw_attach(tenant_id, chunk.id, chunk.embedding)
                out = chunk
            att_tuple, att_rows = self._build_attachment_payloads(tenant_id, out.id, item)
            self._clear_attachment_ram(tenant_id, out.id)
            if self._library_path is None and self._sqlite_path is None:
                for r in att_rows:
                    if r["data"] is not None:
                        self._attachment_blob_ram[(tenant_id, out.id, r["slot"])] = (r["data"], r["mime"])
            out = replace(out, attachments=att_tuple)
            self.chunks[out.id] = out
        if self._library_path is not None:
            from wisedb.server.library_persist import write_chunk

            await asyncio.to_thread(write_chunk, self._library_path, out, att_rows)
        else:
            await self._persist_chunk(out)
            if self._sqlite_path is not None:
                from wisedb.server.sqlite_persist import replace_chunk_attachments

                await asyncio.to_thread(replace_chunk_attachments, self._sqlite_path, out.id, att_rows)
        await self._recompute_and_persist_edge_bearings(tenant_id, {out.id})
        return out

    async def upsert_edge(self, tenant_id: str, item: EdgeUpsertItem) -> EdgeRecord:
        async with self._lock:
            now = datetime.now(timezone.utc)
            eid = item.id or uuid.uuid4()
            rec = self.edges.get(eid)
            if rec is None:
                rec = EdgeRecord(
                    id=eid,
                    tenant_id=tenant_id,
                    source_chunk_id=item.source_chunk_id,
                    target_chunk_id=item.target_chunk_id,
                    weight=float(item.weight),
                    directed=item.directed,
                    relation_type=item.relation_type,
                    metadata_json=dict(item.metadata),
                    deleted_at=None,
                    created_at=now,
                    updated_at=now,
                )
                self.edges[eid] = rec
            else:
                rec.tenant_id = tenant_id
                rec.source_chunk_id = item.source_chunk_id
                rec.target_chunk_id = item.target_chunk_id
                rec.weight = float(item.weight)
                rec.directed = item.directed
                rec.relation_type = item.relation_type
                rec.metadata_json = dict(item.metadata)
                rec.updated_at = now
            out = rec
        await self._persist_edge(out)
        await self._recompute_and_persist_edge_bearings(
            tenant_id, {item.source_chunk_id, item.target_chunk_id}
        )
        return out

    async def _recompute_and_persist_edge_bearings(self, tenant_id: str, touched: set[uuid.UUID]) -> None:
        to_persist: list[EdgeRecord] = []
        async with self._lock:
            self._recompute_edge_bearings_locked(tenant_id, touched, to_persist)
        for edge in to_persist:
            await self._persist_edge(edge)

    def _recompute_edge_bearings_locked(
        self,
        tenant_id: str,
        touched: set[uuid.UUID],
        to_persist: list[EdgeRecord],
    ) -> None:
        if not touched:
            return
        from wisedb.retrieval.gaze_geometry import (
            BEARING_SOURCE_GAZE_PCA,
            WISEDB_BEARING_KEY,
            gaze_is_toward_only,
            gaze_metadata_locked,
            parse_edge_bearing_explicit,
            resolve_bearing_directed,
        )

        affected = [
            e
            for e in self.edges.values()
            if e.tenant_id == tenant_id
            and e.deleted_at is None
            and (e.source_chunk_id in touched or e.target_chunk_id in touched)
        ]
        neigh_map: dict[str, set[str]] = {}
        for e in self.edges.values():
            if e.tenant_id != tenant_id or e.deleted_at is not None:
                continue
            sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
            neigh_map.setdefault(sa, set()).add(sb)
            if not e.directed:
                neigh_map.setdefault(sb, set()).add(sa)

        emb: dict[str, list[float]] = {}
        meta: dict[str, dict[str, Any]] = {}
        for c in self.chunks.values():
            if c.tenant_id != tenant_id or c.deleted_at is not None:
                continue
            sid = str(c.id)
            meta[sid] = dict(c.metadata_json)
            if c.embedding is not None:
                emb[sid] = list(c.embedding)

        persisted: set[uuid.UUID] = set()
        for e in affected:
            sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
            md = dict(e.metadata_json)
            wb_raw = md.get(WISEDB_BEARING_KEY)
            wb: dict[str, Any] = dict(wb_raw) if isinstance(wb_raw, dict) else {}
            changed = False

            if parse_edge_bearing_explicit(md, as_source=True) is None:
                br_s, prov_s = resolve_bearing_directed(
                    sa,
                    sb,
                    edge_source_id=sa,
                    edge_target_id=sb,
                    edge_metadata=md,
                    relation_type=e.relation_type,
                    source_metadata=meta.get(sa, {}),
                    neighbor_ids_for_source=sorted(neigh_map.get(sa, ())),
                    embeddings=emb,
                )
                if br_s is not None and not (
                    prov_s == BEARING_SOURCE_GAZE_PCA
                    and gaze_metadata_locked(meta.get(sa, {}))
                    and gaze_is_toward_only(meta.get(sa, {}))
                ):
                    wb["from_source_rad"] = br_s
                    if prov_s is not None:
                        wb["from_source_kind"] = prov_s
                    changed = True

            if not e.directed and parse_edge_bearing_explicit(md, as_source=False) is None:
                br_t, prov_t = resolve_bearing_directed(
                    sb,
                    sa,
                    edge_source_id=sa,
                    edge_target_id=sb,
                    edge_metadata=md,
                    relation_type=e.relation_type,
                    source_metadata=meta.get(sb, {}),
                    neighbor_ids_for_source=sorted(neigh_map.get(sb, ())),
                    embeddings=emb,
                )
                if br_t is not None and not (
                    prov_t == BEARING_SOURCE_GAZE_PCA
                    and gaze_metadata_locked(meta.get(sb, {}))
                    and gaze_is_toward_only(meta.get(sb, {}))
                ):
                    wb["from_target_rad"] = br_t
                    if prov_t is not None:
                        wb["from_target_kind"] = prov_t
                    changed = True

            if changed:
                md[WISEDB_BEARING_KEY] = wb
                e.metadata_json = md
                if e.id not in persisted:
                    persisted.add(e.id)
                    to_persist.append(e)

    async def get_chunk(self, tenant_id: str, chunk_id: uuid.UUID) -> Optional[ChunkRecord]:
        async with self._lock:
            c = self.chunks.get(chunk_id)
            if c is None or c.tenant_id != tenant_id or c.deleted_at is not None:
                return None
            return c

    async def read_attachment_bytes(
        self, tenant_id: str, chunk_id: uuid.UUID, slot: str
    ) -> Optional[tuple[bytes, str]]:
        if self._library_path is not None:
            from wisedb.server.library_persist import read_attachment_blob

            return await asyncio.to_thread(
                read_attachment_blob, self._library_path, tenant_id, chunk_id, slot
            )
        use_sqlite_blobs = self._sqlite_path is not None
        async with self._lock:
            c = self.chunks.get(chunk_id)
            if c is None or c.tenant_id != tenant_id or c.deleted_at is not None:
                return None
            if not any(a.slot == slot for a in c.attachments):
                return None
            if not use_sqlite_blobs:
                return self._attachment_blob_ram.get((tenant_id, chunk_id, slot))
        from wisedb.server.sqlite_persist import load_attachment_blob

        return await asyncio.to_thread(load_attachment_blob, self._sqlite_path, chunk_id, slot)

    async def get_chunk_by_external_key(self, tenant_id: str, external_key: str) -> Optional[ChunkRecord]:
        async with self._lock:
            cid = self._external.get((tenant_id, external_key))
            if cid is None:
                return None
            c = self.chunks.get(cid)
            if c is None or c.deleted_at is not None:
                return None
            return c

    async def soft_delete_chunk(self, tenant_id: str, chunk_id: uuid.UUID) -> bool:
        async with self._lock:
            c = self.chunks.get(chunk_id)
            if c is None or c.tenant_id != tenant_id or c.deleted_at is not None:
                return False
            self._lex_remove_chunk(c)
            self._hnsw_detach(tenant_id, chunk_id)
            if c.external_key and self._external.get((tenant_id, c.external_key)) == chunk_id:
                del self._external[(tenant_id, c.external_key)]
            self._clear_attachment_ram(tenant_id, chunk_id)
            c.attachments = ()
            c.deleted_at = datetime.now(timezone.utc)
            c.updated_at = c.deleted_at
        if self._library_path is not None:
            from wisedb.server.library_persist import write_chunk

            await asyncio.to_thread(write_chunk, self._library_path, c, [])
        else:
            await self._persist_chunk(c)
            if self._sqlite_path is not None:
                from wisedb.server.sqlite_persist import delete_attachments_for_chunk

                await asyncio.to_thread(delete_attachments_for_chunk, self._sqlite_path, chunk_id)
        return True

    async def list_chunks_preview(self, tenant_id: str, *, limit: int) -> list[ChunkRecord]:
        async with self._lock:
            rows = [c for c in self.chunks.values() if c.tenant_id == tenant_id and c.deleted_at is None]
            rows.sort(key=lambda x: str(x.id))
            return rows[:limit]

    async def search_dense_ids(self, tenant_id: str, query_embedding: list[float], *, top_k: int) -> list[str]:
        if len(query_embedding) != self.dim:
            raise ValueError(f"эмбеддинг запроса должен быть длины {self.dim}")
        async with self._lock:
            cur = self._hnsw.get_current_count()
            if cur == 0:
                return []
            k = min(max(top_k * 8, top_k), cur)
            q = np.asarray(query_embedding, dtype=np.float32).reshape(1, self.dim)
            labels, _dist = self._hnsw.knn_query(q, k=k)
            out: list[str] = []
            seen: set[uuid.UUID] = set()
            for lid in labels[0]:
                ref = self._label_to_ref.get(int(lid))
                if ref is None:
                    continue
                t, cid = ref
                if t != tenant_id:
                    continue
                c = self.chunks.get(cid)
                if c is None or c.deleted_at is not None:
                    continue
                if cid in seen:
                    continue
                seen.add(cid)
                out.append(str(cid))
                if len(out) >= top_k:
                    break
            return out

    async def search_lexical_ids(self, tenant_id: str, query: str, *, top_k: int) -> list[str]:
        q = query.strip()
        if not q:
            return []
        tokens = _tokenize(q)
        if not tokens:
            tokens = {q.lower()} if len(q) >= 2 else set()
        async with self._lock:
            scores: dict[uuid.UUID, int] = {}
            for tok in tokens:
                for cid in self._lexical.get(_lexical_key(tenant_id, tok), ()):
                    c = self.chunks.get(cid)
                    if c is None or c.deleted_at is not None:
                        continue
                    scores[cid] = scores.get(cid, 0) + 1
            ranked = sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))
            return [str(cid) for cid, _ in ranked[:top_k]]

    async def expand_reachable_nodes(
        self, tenant_id: str, seeds: Iterable[uuid.UUID], *, max_hops: int
    ) -> set[uuid.UUID]:
        async with self._lock:
            R: set[uuid.UUID] = {uuid.UUID(str(s)) for s in seeds}
            frontier: set[uuid.UUID] = set(R)
            for _ in range(max_hops):
                if not frontier:
                    break
                neigh: set[uuid.UUID] = set()
                for e in self.edges.values():
                    if e.tenant_id != tenant_id or e.deleted_at is not None:
                        continue
                    if e.source_chunk_id in frontier:
                        neigh.add(e.target_chunk_id)
                    if e.target_chunk_id in frontier:
                        neigh.add(e.source_chunk_id)
                frontier = neigh - R
                R |= neigh
            return R

    async def load_internal_edges(
        self, tenant_id: str, nodes: list[uuid.UUID]
    ) -> list[tuple[str, str, str, float, bool]]:
        if not nodes:
            return []
        nset = set(nodes)
        async with self._lock:
            out: list[tuple[str, str, str, float, bool]] = []
            for e in self.edges.values():
                if e.tenant_id != tenant_id or e.deleted_at is not None:
                    continue
                if e.source_chunk_id in nset and e.target_chunk_id in nset:
                    out.append(
                        (
                            str(e.id),
                            str(e.source_chunk_id),
                            str(e.target_chunk_id),
                            float(e.weight),
                            bool(e.directed),
                        )
                    )
            return out

    async def load_internal_edge_records(
        self, tenant_id: str, nodes: list[uuid.UUID]
    ) -> list[EdgeRecord]:
        if not nodes:
            return []
        nset = set(nodes)
        async with self._lock:
            return [
                e
                for e in self.edges.values()
                if e.tenant_id == tenant_id
                and e.deleted_at is None
                and e.source_chunk_id in nset
                and e.target_chunk_id in nset
            ]

    async def fetch_chunks_by_ids(self, tenant_id: str, ids: Iterable[uuid.UUID]) -> dict[str, ChunkRecord]:
        id_list = list({uuid.UUID(str(i)) for i in ids})
        async with self._lock:
            m: dict[str, ChunkRecord] = {}
            for cid in id_list:
                c = self.chunks.get(cid)
                if c and c.tenant_id == tenant_id and c.deleted_at is None:
                    m[str(cid)] = c
            return m

    async def soft_delete_edge(self, tenant_id: str, edge_id: uuid.UUID) -> bool:
        async with self._lock:
            e = self.edges.get(edge_id)
            if e is None or e.tenant_id != tenant_id or e.deleted_at is not None:
                return False
            now = datetime.now(timezone.utc)
            e.deleted_at = now
            e.updated_at = now
            snap = e
        await self._persist_edge(snap)
        return True

    async def reinforce_edges_internal(self, tenant_id: str, incident: list[EdgeRecord]) -> None:
        """Пометить рёбра как усиленные retrieve (decay может их пропускать по времени)."""
        if not incident:
            return
        now = datetime.now(timezone.utc)
        iso = now.isoformat()
        to_persist: list[EdgeRecord] = []
        async with self._lock:
            for ex in incident:
                e = self.edges.get(ex.id)
                if e is None or e.tenant_id != tenant_id or e.deleted_at is not None:
                    continue
                md = dict(e.metadata_json)
                md["wisedb_last_reinforced_at"] = iso
                md["wisedb_reinforce_count"] = int(md.get("wisedb_reinforce_count", 0)) + 1
                e.metadata_json = md
                e.updated_at = now
                to_persist.append(e)
        for e in to_persist:
            await self._persist_edge(e)

    async def _run_llm_optimize_sleep(
        self,
        tenant_id: str,
        *,
        dry_run: bool,
        api_key: Optional[str],
        base_url: str,
        model: str,
        max_chunks: int,
        max_actions: int,
        embedding_fn: Optional[Callable[[list[str]], list[list[float]]]],
        stats: dict[str, Any],
    ) -> None:
        if not api_key:
            raise ValueError("llm_optimize: нужен llm_api_key или WISEDB_SLEEP_LLM_API_KEY")

        async with self._lock:
            candidates = [
                c
                for c in self.chunks.values()
                if c.tenant_id == tenant_id
                and c.deleted_at is None
                and not _metadata_pinned(c.metadata_json)
            ]
            candidates.sort(key=lambda c: (c.updated_at, str(c.id)))
            selected = candidates[:max_chunks]

        stats["llm_chunks_considered"] = len(selected)
        stats["llm_model"] = model
        if not selected:
            stats["llm_actions"] = []
            return

        payload_chunks = [
            {
                "id": str(c.id),
                "title": c.title,
                "body": c.body,
                "tags": list(c.tags),
                "metadata": dict(c.metadata_json),
            }
            for c in selected
        ]
        from wisedb.server.llm_sleep import optimize_sleep_chunks

        result = await asyncio.to_thread(
            optimize_sleep_chunks,
            api_key=api_key,
            base_url=base_url,
            model=model,
            chunks=payload_chunks,
            max_actions=max_actions,
        )
        actions = list(result.get("actions", []))[:max_actions]
        stats["llm_actions_proposed"] = len(actions)
        stats["llm_actions_preview"] = _sleep_action_preview(actions)
        stats["chunks_updated_by_llm"] = 0
        stats["chunks_created_by_llm_split"] = 0
        stats["edges_created_by_llm_split"] = 0
        if dry_run:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        selected_by_id = {str(c.id): c for c in selected}
        split_edge_items: list[EdgeUpsertItem] = []
        texts_to_embed: list[str] = []
        pending_items: list[ChunkUpsertItem] = []
        pending_kinds: list[tuple[str, str]] = []

        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("type", ""))
            chunk_id = str(action.get("chunk_id", ""))
            source = selected_by_id.get(chunk_id)
            if source is None:
                continue
            reason = str(action.get("reason", ""))[:500]
            if action_type == "update":
                md = dict(source.metadata_json)
                md.update(_sleep_metadata(action.get("metadata")))
                md["wisedb_sleep_optimized_at"] = now_iso
                if reason:
                    md["wisedb_sleep_optimization_reason"] = reason
                title = _sleep_text(action.get("title"), 256) or source.title
                body = _sleep_text(action.get("body"), 50_000) or source.body
                item = ChunkUpsertItem(
                    id=source.id,
                    external_key=source.external_key,
                    title=title,
                    body=body,
                    tags=_sleep_tags(action.get("tags"), source.tags),
                    metadata=md,
                    embedding=None,
                )
                pending_items.append(item)
                pending_kinds.append(("update", chunk_id))
                texts_to_embed.append(f"{item.title}\n{item.body}".strip())
            elif action_type == "split":
                parts = action.get("chunks")
                if not isinstance(parts, list) or len(parts) < 2:
                    continue
                for idx, part in enumerate(parts[:8], start=1):
                    if not isinstance(part, dict):
                        continue
                    title = _sleep_text(part.get("title"), 256) or f"{source.title} - part {idx}"
                    body = _sleep_text(part.get("body"), 50_000)
                    if not body:
                        continue
                    md = dict(source.metadata_json)
                    md.update(_sleep_metadata(part.get("metadata")))
                    md["wisedb_sleep_split_from"] = chunk_id
                    md["wisedb_sleep_optimized_at"] = now_iso
                    if reason:
                        md["wisedb_sleep_optimization_reason"] = reason
                    item = ChunkUpsertItem(
                        external_key=(
                            f"{source.external_key}#sleep-split:{idx}:{uuid.uuid4().hex[:8]}"
                            if source.external_key
                            else None
                        ),
                        title=title,
                        body=body,
                        tags=_sleep_tags(part.get("tags"), source.tags),
                        metadata=md,
                        embedding=None,
                    )
                    pending_items.append(item)
                    pending_kinds.append(("split", chunk_id))
                    texts_to_embed.append(f"{item.title}\n{item.body}".strip())

        embeddings: list[Optional[list[float]]] = [None] * len(pending_items)
        if embedding_fn is not None and texts_to_embed:
            encoded = await asyncio.to_thread(embedding_fn, texts_to_embed)
            embeddings = [list(v) for v in encoded]

        for idx, item in enumerate(pending_items):
            emb = embeddings[idx] if idx < len(embeddings) else None
            if emb is not None:
                item = item.model_copy(update={"embedding": emb})
            out = await self.upsert_chunk(tenant_id, item)
            kind, source_id = pending_kinds[idx]
            if kind == "update":
                stats["chunks_updated_by_llm"] += 1
            elif kind == "split":
                stats["chunks_created_by_llm_split"] += 1
                split_edge_items.append(
                    EdgeUpsertItem(
                        source_chunk_id=uuid.UUID(source_id),
                        target_chunk_id=out.id,
                        weight=0.95,
                        directed=True,
                        relation_type="sleep_split",
                        metadata={
                            "wisedb_sleep_generated": True,
                            "wisedb_sleep_optimized_at": now_iso,
                        },
                    )
                )

        for edge_item in split_edge_items:
            await self.upsert_edge(tenant_id, edge_item)
            stats["edges_created_by_llm_split"] += 1

    async def run_sleep(
        self,
        tenant_id: str,
        *,
        phase: str,
        dry_run: bool = False,
        decay_factor: float = 0.95,
        min_weight: float = 0.05,
        delete_orphan_chunks: bool = False,
        skip_reinforced_within_hours: float = 0.0,
        llm_api_key: Optional[str] = None,
        llm_base_url: str = "https://api.openai.com/v1",
        llm_model: str = "gpt-4o-mini",
        llm_max_chunks: int = 24,
        llm_max_actions: int = 12,
        embedding_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    ) -> dict[str, Any]:
        """
        Фаза обслуживания («sleep»): decay_prune, geometry_refresh или llm_optimize.

        - decay_prune: умножить веса не закреплённых рёбер на ``decay_factor``, удалить ниже ``min_weight``,
          опционально удалить чанки без инцидентных рёбер.
        - geometry_refresh: пересчитать кэш ``wisedb_bearing`` для рёбер тенанта.
        - llm_optimize: OpenAI-совместимая модель предлагает осторожные update/split чанков.
        """
        run_id = uuid.uuid4()
        stats: dict[str, Any] = {
            "edges_decayed": 0,
            "edges_deleted": 0,
            "edges_skipped_pinned": 0,
            "edges_skipped_reinforced": 0,
            "chunks_deleted_orphan": 0,
            "dry_run": dry_run,
        }
        sp_path = self._sqlite_path
        if phase not in ("decay_prune", "geometry_refresh", "llm_optimize"):
            raise ValueError(
                f"неизвестная фаза sleep: {phase!r} (нужно decay_prune, geometry_refresh или llm_optimize)"
            )

        if sp_path is not None:
            from wisedb.server.sqlite_persist import (
                ensure_sleep_schema,
                insert_sleep_run,
            )

            await asyncio.to_thread(ensure_sleep_schema, sp_path)
            await asyncio.to_thread(insert_sleep_run, sp_path, run_id, tenant_id, phase, dry_run)

        err: Optional[str] = None
        status = "completed"
        try:
            if phase == "geometry_refresh":
                touched: set[uuid.UUID] = set()
                async with self._lock:
                    for e in self.edges.values():
                        if e.tenant_id != tenant_id or e.deleted_at is not None:
                            continue
                        touched.add(e.source_chunk_id)
                        touched.add(e.target_chunk_id)
                stats["chunks_touched_geometry"] = len(touched)
                if touched and not dry_run:
                    await self._recompute_and_persist_edge_bearings(tenant_id, touched)
            elif phase == "llm_optimize":
                await self._run_llm_optimize_sleep(
                    tenant_id,
                    dry_run=dry_run,
                    api_key=llm_api_key,
                    base_url=llm_base_url,
                    model=llm_model,
                    max_chunks=llm_max_chunks,
                    max_actions=llm_max_actions,
                    embedding_fn=embedding_fn,
                    stats=stats,
                )
            elif phase == "decay_prune":
                if not 0.0 < decay_factor <= 1.0:
                    raise ValueError("decay_factor должен быть в (0, 1]")
                if not 0.0 < min_weight <= 1.0:
                    raise ValueError("min_weight должен быть в (0, 1]")
                now = datetime.now(timezone.utc)
                cutoff: Optional[datetime] = None
                if skip_reinforced_within_hours > 0:
                    cutoff = now - timedelta(hours=float(skip_reinforced_within_hours))
                recompute_touch: set[uuid.UUID] = set()
                to_persist: list[EdgeRecord] = []
                async with self._lock:
                    for e in self.edges.values():
                        if e.tenant_id != tenant_id or e.deleted_at is not None:
                            continue
                        if _metadata_pinned(e.metadata_json):
                            stats["edges_skipped_pinned"] += 1
                            continue
                        if cutoff is not None:
                            lr = _metadata_reinforced_at(e.metadata_json)
                            if lr is not None:
                                lr_aware = lr if lr.tzinfo else lr.replace(tzinfo=timezone.utc)
                                if lr_aware >= cutoff:
                                    stats["edges_skipped_reinforced"] += 1
                                    continue
                        nw = float(e.weight) * decay_factor
                        recompute_touch.add(e.source_chunk_id)
                        recompute_touch.add(e.target_chunk_id)
                        if nw < min_weight:
                            stats["edges_deleted"] += 1
                            if not dry_run:
                                e.deleted_at = now
                                e.updated_at = now
                                to_persist.append(e)
                        else:
                            stats["edges_decayed"] += 1
                            if not dry_run:
                                e.weight = nw
                                e.updated_at = now
                                to_persist.append(e)
                if not dry_run and to_persist:
                    for e in to_persist:
                        await self._persist_edge(e)
                    if recompute_touch:
                        await self._recompute_and_persist_edge_bearings(tenant_id, recompute_touch)

                if delete_orphan_chunks:
                    orphan_ids: list[uuid.UUID] = []
                    async with self._lock:
                        live: set[uuid.UUID] = set()
                        for e in self.edges.values():
                            if e.tenant_id != tenant_id or e.deleted_at is not None:
                                continue
                            live.add(e.source_chunk_id)
                            live.add(e.target_chunk_id)
                        for cid, c in self.chunks.items():
                            if c.tenant_id != tenant_id or c.deleted_at is not None:
                                continue
                            if cid in live:
                                continue
                            if _metadata_pinned(c.metadata_json):
                                continue
                            orphan_ids.append(cid)
                    stats["chunks_deleted_orphan"] = len(orphan_ids)
                    if not dry_run:
                        for cid in orphan_ids:
                            await self.soft_delete_chunk(tenant_id, cid)
        except Exception as ex:  # noqa: BLE001
            status = "failed"
            err = str(ex)
        finally:
            if sp_path is not None:
                from wisedb.server.sqlite_persist import complete_sleep_run

                await asyncio.to_thread(
                    complete_sleep_run, sp_path, run_id, status, stats, err
                )

        out: dict[str, Any] = {"run_id": str(run_id), "status": status, "stats": stats}
        if err:
            out["error"] = err
        return out
