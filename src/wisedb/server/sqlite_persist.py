"""SQLite (WAL) для движка контекста: горячий путь в ОЗУ, запись на диск — как у серверной БД."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np

from wisedb.server.memory_engine import AttachmentRecord, ChunkRecord, EdgeRecord


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_graph_schema(path: Path) -> None:
    """Схема SQLite только с рёбрами (чанки в файловой библиотеке — гибрид)."""
    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS edges (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              source_chunk_id TEXT NOT NULL,
              target_chunk_id TEXT NOT NULL,
              weight REAL NOT NULL,
              directed INTEGER NOT NULL,
              relation_type TEXT,
              metadata_json TEXT NOT NULL,
              deleted_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sleep_runs (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              phase TEXT NOT NULL,
              dry_run INTEGER NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              status TEXT NOT NULL,
              stats_json TEXT NOT NULL DEFAULT '{}',
              error TEXT
            );
            """
        )
    finally:
        conn.close()


def load_edges(path: Path) -> list[EdgeRecord]:
    if not path.is_file():
        return []
    conn = _connect(path)
    try:
        edges: list[EdgeRecord] = []
        for row in conn.execute(
            """
            SELECT id, tenant_id, source_chunk_id, target_chunk_id, weight, directed,
                   relation_type, metadata_json, deleted_at, created_at, updated_at
            FROM edges
            """
        ):
            edges.append(
                EdgeRecord(
                    id=uuid.UUID(row[0]),
                    tenant_id=row[1],
                    source_chunk_id=uuid.UUID(row[2]),
                    target_chunk_id=uuid.UUID(row[3]),
                    weight=float(row[4]),
                    directed=bool(row[5]),
                    relation_type=row[6],
                    metadata_json=dict(json.loads(row[7])),
                    deleted_at=_parse_iso(row[8]),
                    created_at=_parse_iso(row[9]) or datetime.now(timezone.utc),
                    updated_at=_parse_iso(row[10]) or datetime.now(timezone.utc),
                )
            )
        return edges
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def init_schema(path: Path) -> None:
    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              external_key TEXT,
              title TEXT NOT NULL,
              body TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              embedding BLOB,
              version INTEGER NOT NULL,
              deleted_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_tenant_ext_alive
              ON chunks(tenant_id, external_key)
              WHERE external_key IS NOT NULL AND deleted_at IS NULL;

            CREATE TABLE IF NOT EXISTS edges (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              source_chunk_id TEXT NOT NULL,
              target_chunk_id TEXT NOT NULL,
              weight REAL NOT NULL,
              directed INTEGER NOT NULL,
              relation_type TEXT,
              metadata_json TEXT NOT NULL,
              deleted_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attachments (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              chunk_id TEXT NOT NULL,
              slot TEXT NOT NULL,
              mime TEXT NOT NULL,
              uri TEXT,
              data BLOB,
              created_at TEXT NOT NULL,
              UNIQUE (chunk_id, slot),
              FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_attachments_chunk ON attachments(chunk_id);
            CREATE TABLE IF NOT EXISTS sleep_runs (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              phase TEXT NOT NULL,
              dry_run INTEGER NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              status TEXT NOT NULL,
              stats_json TEXT NOT NULL DEFAULT '{}',
              error TEXT
            );
            """
        )
    finally:
        conn.close()


def upsert_chunk_row(path: Path, c: ChunkRecord) -> None:
    emb_blob: Optional[bytes] = None
    if c.embedding is not None:
        emb_blob = np.asarray(c.embedding, dtype=np.float32).tobytes()
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO chunks (
              id, tenant_id, external_key, title, body, tags_json, metadata_json,
              embedding, version, deleted_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              tenant_id=excluded.tenant_id,
              external_key=excluded.external_key,
              title=excluded.title,
              body=excluded.body,
              tags_json=excluded.tags_json,
              metadata_json=excluded.metadata_json,
              embedding=excluded.embedding,
              version=excluded.version,
              deleted_at=excluded.deleted_at,
              updated_at=excluded.updated_at
            """,
            (
                str(c.id),
                c.tenant_id,
                c.external_key,
                c.title,
                c.body,
                json.dumps(list(c.tags)),
                json.dumps(c.metadata_json),
                emb_blob,
                int(c.version),
                _iso(c.deleted_at),
                _iso(c.created_at),
                _iso(c.updated_at),
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def replace_chunk_attachments(
    path: Path,
    chunk_id: uuid.UUID,
    rows: list[dict[str, Any]],
) -> None:
    """Заменить все строки вложений чанка. Поля: id, tenant_id, chunk_id, slot, mime, uri, data (bytes|None)."""
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM attachments WHERE chunk_id = ?", (str(chunk_id),))
        now = _iso(datetime.now(timezone.utc))
        for r in rows:
            conn.execute(
                """
                INSERT INTO attachments (
                  id, tenant_id, chunk_id, slot, mime, uri, data, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    str(r["id"]),
                    r["tenant_id"],
                    str(r["chunk_id"]),
                    r["slot"],
                    r["mime"],
                    r["uri"],
                    r["data"],
                    now,
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def delete_attachments_for_chunk(path: Path, chunk_id: uuid.UUID) -> None:
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM attachments WHERE chunk_id = ?", (str(chunk_id),))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def load_attachment_blob(path: Path, chunk_id: uuid.UUID, slot: str) -> Optional[Tuple[bytes, str]]:
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT data, mime FROM attachments WHERE chunk_id = ? AND slot = ?",
            (str(chunk_id), slot),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return bytes(row[0]), str(row[1])
    finally:
        conn.close()


def upsert_edge_row(path: Path, e: EdgeRecord) -> None:
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO edges (
              id, tenant_id, source_chunk_id, target_chunk_id, weight, directed,
              relation_type, metadata_json, deleted_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              tenant_id=excluded.tenant_id,
              source_chunk_id=excluded.source_chunk_id,
              target_chunk_id=excluded.target_chunk_id,
              weight=excluded.weight,
              directed=excluded.directed,
              relation_type=excluded.relation_type,
              metadata_json=excluded.metadata_json,
              deleted_at=excluded.deleted_at,
              updated_at=excluded.updated_at
            """,
            (
                str(e.id),
                e.tenant_id,
                str(e.source_chunk_id),
                str(e.target_chunk_id),
                float(e.weight),
                1 if e.directed else 0,
                e.relation_type,
                json.dumps(e.metadata_json),
                _iso(e.deleted_at),
                _iso(e.created_at),
                _iso(e.updated_at),
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def load_all(path: Path) -> Tuple[list[ChunkRecord], list[EdgeRecord]]:
    if not path.is_file():
        return [], []
    conn = _connect(path)
    try:
        chunks: list[ChunkRecord] = []
        for row in conn.execute(
            """
            SELECT id, tenant_id, external_key, title, body, tags_json, metadata_json,
                   embedding, version, deleted_at, created_at, updated_at
            FROM chunks
            """
        ):
            emb: Optional[list[float]] = None
            if row[7] is not None:
                emb = np.frombuffer(row[7], dtype=np.float32).tolist()
            chunks.append(
                ChunkRecord(
                    id=uuid.UUID(row[0]),
                    tenant_id=row[1],
                    external_key=row[2],
                    title=row[3],
                    body=row[4],
                    tags=tuple(json.loads(row[5])),
                    metadata_json=dict(json.loads(row[6])),
                    embedding=emb,
                    version=int(row[8]),
                    deleted_at=_parse_iso(row[9]),
                    created_at=_parse_iso(row[10]) or datetime.now(timezone.utc),
                    updated_at=_parse_iso(row[11]) or datetime.now(timezone.utc),
                )
            )
        edges: list[EdgeRecord] = []
        for row in conn.execute(
            """
            SELECT id, tenant_id, source_chunk_id, target_chunk_id, weight, directed,
                   relation_type, metadata_json, deleted_at, created_at, updated_at
            FROM edges
            """
        ):
            edges.append(
                EdgeRecord(
                    id=uuid.UUID(row[0]),
                    tenant_id=row[1],
                    source_chunk_id=uuid.UUID(row[2]),
                    target_chunk_id=uuid.UUID(row[3]),
                    weight=float(row[4]),
                    directed=bool(row[5]),
                    relation_type=row[6],
                    metadata_json=dict(json.loads(row[7])),
                    deleted_at=_parse_iso(row[8]),
                    created_at=_parse_iso(row[9]) or datetime.now(timezone.utc),
                    updated_at=_parse_iso(row[10]) or datetime.now(timezone.utc),
                )
            )

        att_by_chunk: dict[str, list[AttachmentRecord]] = {}
        try:
            cur = conn.execute(
                """
                SELECT id, tenant_id, chunk_id, slot, mime, uri, data
                FROM attachments
                """
            )
        except sqlite3.OperationalError:
            cur = []

        for row in cur:
            cid_s = str(row[2])
            blob = row[6]
            blen = len(blob) if blob is not None else 0
            att_by_chunk.setdefault(cid_s, []).append(
                AttachmentRecord(
                    id=uuid.UUID(row[0]),
                    tenant_id=row[1],
                    chunk_id=uuid.UUID(row[2]),
                    slot=row[3],
                    mime=row[4],
                    uri=row[5],
                    byte_length=blen,
                )
            )

        fixed_chunks: list[ChunkRecord] = []
        for c in chunks:
            atts = tuple(sorted(att_by_chunk.get(str(c.id), []), key=lambda a: a.slot))
            fixed_chunks.append(replace(c, attachments=atts))
        return fixed_chunks, edges
    finally:
        conn.close()


def ensure_sleep_schema(path: Path) -> None:
    """Создать ``sleep_runs``, если нет (БД со времён до добавления обслуживания)."""
    conn = _connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sleep_runs (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              phase TEXT NOT NULL,
              dry_run INTEGER NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              status TEXT NOT NULL,
              stats_json TEXT NOT NULL DEFAULT '{}',
              error TEXT
            );
            """
        )
    finally:
        conn.close()


def insert_sleep_run(
    path: Path,
    run_id: uuid.UUID,
    tenant_id: str,
    phase: str,
    dry_run: bool,
) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            """
            INSERT INTO sleep_runs (id, tenant_id, phase, dry_run, started_at, status, stats_json)
            VALUES (?,?,?,?,?,'running','{}')
            """,
            (
                str(run_id),
                tenant_id,
                phase,
                1 if dry_run else 0,
                _iso(datetime.now(timezone.utc)),
            ),
        )
    finally:
        conn.close()


def complete_sleep_run(
    path: Path,
    run_id: uuid.UUID,
    status: str,
    stats: dict[str, Any],
    error: Optional[str] = None,
) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            """
            UPDATE sleep_runs SET finished_at = ?, status = ?, stats_json = ?, error = ?
            WHERE id = ?
            """,
            (
                _iso(datetime.now(timezone.utc)),
                status,
                json.dumps(stats),
                error,
                str(run_id),
            ),
        )
    finally:
        conn.close()
