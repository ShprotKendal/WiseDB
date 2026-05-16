"""Файловая библиотека чанков (гибрид): каталог на чанк; граф только в SQLite."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from wisedb.server.memory_engine import AttachmentRecord, ChunkRecord

_CHUNK_ID_DIR = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _blob_key(slot: str) -> str:
    return hashlib.sha256(slot.encode("utf-8")).hexdigest()


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


def chunk_root(library: Path, tenant_id: str, chunk_id: uuid.UUID) -> Path:
    return library / tenant_id / "chunks" / str(chunk_id)


def write_chunk(
    library: Path,
    c: ChunkRecord,
    att_rows: list[dict[str, Any]],
) -> None:
    """Почти атомарная запись: каталог чанка + chunk.json + блобы вложений."""
    root = chunk_root(library, c.tenant_id, c.id)
    att_dir = root / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)

    valid_keys = {_blob_key(r["slot"]) for r in att_rows}
    if att_dir.is_dir():
        for p in att_dir.iterdir():
            if p.is_file() and len(p.name) == 64 and p.name not in valid_keys:
                try:
                    p.unlink()
                except OSError:
                    pass

    for r in att_rows:
        data = r.get("data")
        if data is not None:
            key = _blob_key(r["slot"])
            (att_dir / key).write_bytes(data)

    att_meta: list[dict[str, Any]] = []
    for r in sorted(att_rows, key=lambda x: str(x["slot"])):
        blen = len(r["data"]) if r.get("data") is not None else 0
        att_meta.append(
            {
                "id": str(r["id"]),
                "slot": r["slot"],
                "mime": r["mime"],
                "uri": r["uri"],
                "byte_length": blen,
                "blob_key": _blob_key(r["slot"]) if blen else None,
            }
        )

    payload = {
        "id": str(c.id),
        "tenant_id": c.tenant_id,
        "external_key": c.external_key,
        "title": c.title,
        "body": c.body,
        "tags": list(c.tags),
        "metadata_json": dict(c.metadata_json),
        "embedding": list(c.embedding) if c.embedding is not None else None,
        "version": c.version,
        "deleted_at": _iso(c.deleted_at),
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
        "attachments": att_meta,
    }

    root.mkdir(parents=True, exist_ok=True)
    tmp = root / ".chunk.json.tmp"
    final = root / "chunk.json"
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def _record_from_payload(data: dict[str, Any]) -> ChunkRecord:
    emb = data.get("embedding")
    emb_list: Optional[list[float]] = None
    if isinstance(emb, list) and emb:
        emb_list = [float(x) for x in emb]
    elif emb is None:
        emb_list = None

    att_in = data.get("attachments") or []
    attachments: list[AttachmentRecord] = []
    if isinstance(att_in, list):
        for a in att_in:
            if not isinstance(a, dict):
                continue
            try:
                aid = uuid.UUID(str(a["id"]))
                cid = uuid.UUID(str(data["id"]))
            except (KeyError, ValueError):
                continue
            attachments.append(
                AttachmentRecord(
                    id=aid,
                    tenant_id=str(data["tenant_id"]),
                    chunk_id=cid,
                    slot=str(a["slot"]),
                    mime=str(a.get("mime") or "application/octet-stream"),
                    uri=a.get("uri"),
                    byte_length=int(a.get("byte_length") or 0),
                )
            )
    attachments.sort(key=lambda x: x.slot)

    return ChunkRecord(
        id=uuid.UUID(str(data["id"])),
        tenant_id=str(data["tenant_id"]),
        external_key=data.get("external_key"),
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        tags=tuple(data.get("tags") or ()),
        metadata_json=dict(data.get("metadata_json") or {}),
        embedding=emb_list,
        version=int(data.get("version") or 1),
        deleted_at=_parse_iso(data.get("deleted_at")),
        created_at=_parse_iso(data.get("created_at")) or datetime.now(timezone.utc),
        updated_at=_parse_iso(data.get("updated_at")) or datetime.now(timezone.utc),
        attachments=tuple(attachments),
    )


def read_chunk(library: Path, tenant_id: str, chunk_id: uuid.UUID) -> Optional[ChunkRecord]:
    path = chunk_root(library, tenant_id, chunk_id) / "chunk.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    root = chunk_root(library, tenant_id, chunk_id)
    att_dir = root / "attachments"
    rec = _record_from_payload(data)
    if att_dir.is_dir():
        fixed_atts: list[AttachmentRecord] = []
        for a in rec.attachments:
            key = _blob_key(a.slot)
            fp = att_dir / key
            blen = a.byte_length
            if fp.is_file():
                blen = fp.stat().st_size
            fixed_atts.append(replace(a, byte_length=blen))
        rec = replace(rec, attachments=tuple(sorted(fixed_atts, key=lambda x: x.slot)))
    return rec


def load_all_chunks(library: Path) -> list[ChunkRecord]:
    if not library.is_dir():
        return []
    out: list[ChunkRecord] = []
    for tenant_dir in sorted(library.iterdir()):
        if not tenant_dir.is_dir() or tenant_dir.name.startswith("."):
            continue
        chunks_root = tenant_dir / "chunks"
        if not chunks_root.is_dir():
            continue
        tenant_id = tenant_dir.name
        for ch_dir in sorted(chunks_root.iterdir()):
            if not ch_dir.is_dir() or not _CHUNK_ID_DIR.match(ch_dir.name):
                continue
            try:
                cid = uuid.UUID(ch_dir.name)
            except ValueError:
                continue
            rec = read_chunk(library, tenant_id, cid)
            if rec is not None:
                out.append(rec)
    return out


def read_attachment_blob(
    library: Path,
    tenant_id: str,
    chunk_id: uuid.UUID,
    slot: str,
) -> Optional[tuple[bytes, str]]:
    rec = read_chunk(library, tenant_id, chunk_id)
    if rec is None:
        return None
    mime: Optional[str] = None
    for a in rec.attachments:
        if a.slot == slot:
            mime = a.mime
            break
    if mime is None:
        return None
    key = _blob_key(slot)
    fp = chunk_root(library, tenant_id, chunk_id) / "attachments" / key
    if not fp.is_file():
        return None
    return fp.read_bytes(), mime


def remove_chunk_library_files(library: Path, tenant_id: str, chunk_id: uuid.UUID) -> None:
    root = chunk_root(library, tenant_id, chunk_id)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
