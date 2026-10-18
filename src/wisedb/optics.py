"""Per-database optical layers keyed by node slug and optic kind."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from wisedb.optic_span import (
    SpanConflictError,
    SpanError,
    apply_span_edit,
    bind_span,
    parse_binding,
    read_live,
    serialize_binding,
)

CONTENT_OPTIC = "content"
_OPTIC_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MAX_TEXT_BYTES = 1024 * 1024
_MAX_BINARY_BYTES = 25 * 1024 * 1024
_WISEDB = "_wisedb"
_OPTICS_DIR = "optics"
_REGISTRY_FILE = "optics.json"

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # needs WEBP at offset 8
)
_VIDEO_MAGIC = (
    (b"\x00\x00\x00", "video/mp4"),  # ftyp at 4
    (b"\x1a\x45\xdf\xa3", "video/webm"),
)


class OpticKind(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    IMAGE = "image"
    VIDEO = "video"
    LINK = "link"
    FILE = "file"
    SPAN = "span"

    @classmethod
    def from_storage(cls, value: str | None) -> OpticKind:
        if not value:
            return cls.MARKDOWN
        try:
            return cls(value)
        except ValueError:
            raise OpticsError(f"unknown optic kind: {value}") from None


TEXT_KINDS = frozenset({OpticKind.MARKDOWN, OpticKind.JSON, OpticKind.LINK, OpticKind.SPAN})
BINARY_KINDS = frozenset({OpticKind.IMAGE, OpticKind.VIDEO, OpticKind.FILE})

WQL_KIND_KEYWORDS = {
    "MARKDOWN": OpticKind.MARKDOWN,
    "JSON": OpticKind.JSON,
    "IMAGE": OpticKind.IMAGE,
    "VIDEO": OpticKind.VIDEO,
    "LINK": OpticKind.LINK,
    "FILE": OpticKind.FILE,
    "SPAN": OpticKind.SPAN,
}


class OpticsError(Exception):
    """Invalid optic name, slug, or storage operation."""


class OpticsConflictError(OpticsError):
    """Concurrent modification (for example span base hash mismatch)."""


@dataclass(frozen=True, slots=True)
class OpticRecord:
    name: str
    description: str
    kind: OpticKind = OpticKind.MARKDOWN

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
        }


def validate_optic_name(name: str) -> str:
    normalized = name.strip()
    if normalized == CONTENT_OPTIC:
        raise OpticsError("content is the built-in optic and cannot be used as a custom name")
    if not _OPTIC_NAME_RE.fullmatch(normalized):
        raise OpticsError(
            "optic name must match ^[a-z][a-z0-9_-]{0,63}$ and cannot be 'content'"
        )
    return normalized


def validate_optic_kind(kind: str | OpticKind) -> OpticKind:
    if isinstance(kind, OpticKind):
        return kind
    return OpticKind.from_storage(kind.strip().lower())


def validate_optic_slug(slug: str) -> str:
    if not slug or len(slug) > 256:
        raise OpticsError("invalid slug")
    if "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise OpticsError("invalid slug")
    return slug


def _wisedb_root(wiki_root: Path) -> Path:
    return wiki_root / _WISEDB


def _registry_path(wiki_root: Path) -> Path:
    return _wisedb_root(wiki_root) / _REGISTRY_FILE


def _optic_text_dir(wiki_root: Path, optic: str) -> Path:
    return _wisedb_root(wiki_root) / _OPTICS_DIR / optic


def _optic_text_path(wiki_root: Path, optic: str, slug: str) -> Path:
    safe_slug = validate_optic_slug(slug)
    return _optic_text_dir(wiki_root, optic) / safe_slug


def _optic_binary_path(wiki_root: Path, optic: str, slug: str) -> Path:
    safe_slug = validate_optic_slug(slug)
    return _optic_text_dir(wiki_root, optic) / f"{safe_slug}.bin"


def _optic_meta_path(wiki_root: Path, optic: str, slug: str) -> Path:
    safe_slug = validate_optic_slug(slug)
    return _optic_text_dir(wiki_root, optic) / f"{safe_slug}.meta.json"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _load_registry(path: Path) -> list[OpticRecord]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("optics", [])
    records: list[OpticRecord] = []
    for item in items:
        name = validate_optic_name(str(item.get("name", "")))
        description = str(item.get("description", "") or "")
        kind = validate_optic_kind(str(item.get("kind", "markdown")))
        records.append(OpticRecord(name=name, description=description, kind=kind))
    return records


def _save_registry(path: Path, records: list[OpticRecord]) -> None:
    payload = {
        "optics": [
            {
                "name": r.name,
                "description": r.description,
                "kind": r.kind.value,
            }
            for r in sorted(records, key=lambda x: x.name)
        ]
    }
    _atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def list_custom_optics(wiki_root: Path) -> list[OpticRecord]:
    return _load_registry(_registry_path(wiki_root))


def get_custom_optic(wiki_root: Path, name: str) -> OpticRecord | None:
    optic = validate_optic_name(name)
    for record in list_custom_optics(wiki_root):
        if record.name == optic:
            return record
    return None


def get_optic_kind(wiki_root: Path, optic: str) -> OpticKind:
    record = get_custom_optic(wiki_root, optic)
    if record is None:
        raise OpticsError("optic not found")
    return record.kind


def create_optic(
    wiki_root: Path,
    name: str,
    *,
    description: str = "",
    kind: str | OpticKind = OpticKind.MARKDOWN,
) -> OpticRecord:
    optic = validate_optic_name(name)
    kind_enum = validate_optic_kind(kind)
    path = _registry_path(wiki_root)
    records = _load_registry(path)
    if any(r.name == optic for r in records):
        raise OpticsError("optic already exists")
    record = OpticRecord(name=optic, description=description or "", kind=kind_enum)
    records.append(record)
    _save_registry(path, records)
    _optic_text_dir(wiki_root, optic).mkdir(parents=True, exist_ok=True)
    return record


def update_optic_description(wiki_root: Path, name: str, description: str) -> OpticRecord:
    optic = validate_optic_name(name)
    path = _registry_path(wiki_root)
    records = _load_registry(path)
    for index, record in enumerate(records):
        if record.name == optic:
            updated = OpticRecord(name=optic, description=description or "", kind=record.kind)
            records[index] = updated
            _save_registry(path, records)
            return updated
    raise OpticsError("optic not found")


def drop_optic(wiki_root: Path, name: str) -> None:
    optic = validate_optic_name(name)
    path = _registry_path(wiki_root)
    records = _load_registry(path)
    if not any(r.name == optic for r in records):
        raise OpticsError("optic not found")
    records = [r for r in records if r.name != optic]
    _save_registry(path, records)
    text_dir = _optic_text_dir(wiki_root, optic)
    if text_dir.exists():
        shutil.rmtree(text_dir)


def _validate_text_for_kind(kind: OpticKind, text: str) -> None:
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_TEXT_BYTES:
        raise OpticsError("optic text exceeds 1 MiB limit")
    if kind == OpticKind.JSON:
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise OpticsError(f"invalid JSON: {exc}") from exc
    if kind == OpticKind.LINK:
        parsed = urlparse(text.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise OpticsError("link must be an absolute http or https URL")


def _detect_image_mime(data: bytes) -> str | None:
    for prefix, mime in _IMAGE_MAGIC:
        if mime == "image/webp":
            if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
                return mime
            continue
        if data.startswith(prefix):
            return mime
    return None


def _detect_video_mime(data: bytes) -> str | None:
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    return None


def _validate_binary_for_kind(
    kind: OpticKind,
    data: bytes,
    *,
    media_type: str | None,
) -> str:
    if len(data) > _MAX_BINARY_BYTES:
        raise OpticsError("optic binary exceeds 25 MiB limit")
    if kind == OpticKind.IMAGE:
        detected = _detect_image_mime(data)
        if not detected:
            raise OpticsError("unsupported image format")
        return detected
    if kind == OpticKind.VIDEO:
        detected = _detect_video_mime(data)
        if not detected:
            raise OpticsError("unsupported video format")
        return detected
    return media_type or "application/octet-stream"


def read_stored_binding_raw(wiki_root: Path, optic: str, slug: str) -> str:
    path = _optic_text_path(wiki_root, optic, slug)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _require_record(wiki_root: Path, optic: str) -> OpticRecord:
    record = get_custom_optic(wiki_root, optic)
    if record is None:
        raise OpticsError("optic not found")
    return record


def read_optic_value(wiki_root: Path, optic: str, slug: str) -> dict[str, Any]:
    record = _require_record(wiki_root, optic)
    validate_optic_slug(slug)
    kind = record.kind
    if kind in BINARY_KINDS:
        bin_path = _optic_binary_path(wiki_root, optic, slug)
        meta_path = _optic_meta_path(wiki_root, optic, slug)
        if not bin_path.is_file():
            return {"kind": kind.value, "text": "", "has_bytes": False}
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        return {
            "kind": kind.value,
            "text": "",
            "has_bytes": True,
            "filename": meta.get("filename", slug),
            "media_type": meta.get("media_type", "application/octet-stream"),
            "size": meta.get("size", bin_path.stat().st_size),
            "sha256": meta.get("sha256", ""),
        }
    if kind == OpticKind.SPAN:
        raw = read_stored_binding_raw(wiki_root, optic, slug)
        binding = parse_binding(raw)
        if binding is None:
            return {"kind": kind.value, "text": "", "bound": False}
        try:
            live = read_live(binding, wiki_root)
        except SpanError as exc:
            raise OpticsError(str(exc)) from exc
        return {
            "kind": kind.value,
            "bound": True,
            "text": live["text"],
            "path": live["path"],
            "start_line": live["start_line"],
            "end_line": live["end_line"],
            "sha256": live["sha256"],
        }
    path = _optic_text_path(wiki_root, optic, slug)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {"kind": kind.value, "text": text}


def read_optic_text(wiki_root: Path, optic: str, slug: str) -> str:
    value = read_optic_value(wiki_root, optic, slug)
    kind = OpticKind.from_storage(value.get("kind"))
    if kind in BINARY_KINDS:
        raise OpticsError("binary optic has no text; use bytes endpoint")
    return str(value.get("text", ""))


def write_optic_text(wiki_root: Path, optic: str, slug: str, text: str) -> None:
    record = _require_record(wiki_root, optic)
    validate_optic_slug(slug)
    if record.kind in BINARY_KINDS:
        raise OpticsError("binary optic cannot be updated with text")
    if record.kind == OpticKind.SPAN:
        binding = parse_binding(read_stored_binding_raw(wiki_root, optic, slug))
        if binding is None:
            raise OpticsError("span is not bound")
        try:
            updated = apply_span_edit(
                wiki_root,
                binding,
                text,
                binding.sha256,
                optic=optic,
                slug=slug,
                update_stored=lambda o, s, raw: _atomic_write(
                    _optic_text_path(wiki_root, o, s),
                    raw.encode("utf-8"),
                ),
            )
        except SpanConflictError as exc:
            raise OpticsConflictError(str(exc)) from exc
        except SpanError as exc:
            raise OpticsError(str(exc)) from exc
        _atomic_write(_optic_text_path(wiki_root, optic, slug), serialize_binding(updated).encode("utf-8"))
        return
    _validate_text_for_kind(record.kind, text)
    _atomic_write(_optic_text_path(wiki_root, optic, slug), text.encode("utf-8"))


def bind_optic_span(
    wiki_root: Path,
    optic: str,
    slug: str,
    *,
    path: str,
    start_line: int,
    end_line: int,
) -> dict[str, Any]:
    record = _require_record(wiki_root, optic)
    if record.kind != OpticKind.SPAN:
        raise OpticsError("optic is not span kind")
    validate_optic_slug(slug)
    try:
        binding = bind_span(wiki_root, path, start_line, end_line)
        from wisedb.optic_span import _assert_no_intersection

        _assert_no_intersection(
            wiki_root,
            binding.path,
            binding.start_line,
            binding.end_line,
            exclude_optic=optic,
            exclude_slug=slug,
        )
    except SpanConflictError as exc:
        raise OpticsConflictError(str(exc)) from exc
    except SpanError as exc:
        raise OpticsError(str(exc)) from exc
    _atomic_write(_optic_text_path(wiki_root, optic, slug), serialize_binding(binding).encode("utf-8"))
    live = read_live(binding, wiki_root)
    return {
        "kind": OpticKind.SPAN.value,
        "bound": True,
        "text": live["text"],
        "path": live["path"],
        "start_line": live["start_line"],
        "end_line": live["end_line"],
        "sha256": live["sha256"],
    }


def write_optic_span_edit(
    wiki_root: Path,
    optic: str,
    slug: str,
    *,
    text: str,
    base_sha256: str,
) -> dict[str, Any]:
    record = _require_record(wiki_root, optic)
    if record.kind != OpticKind.SPAN:
        raise OpticsError("optic is not span kind")
    validate_optic_slug(slug)
    binding = parse_binding(read_stored_binding_raw(wiki_root, optic, slug))
    if binding is None:
        raise OpticsError("span is not bound")
    try:
        updated = apply_span_edit(
            wiki_root,
            binding,
            text,
            base_sha256,
            optic=optic,
            slug=slug,
            update_stored=lambda o, s, raw: _atomic_write(
                _optic_text_path(wiki_root, o, s),
                raw.encode("utf-8"),
            ),
        )
    except SpanConflictError as exc:
        raise OpticsConflictError(str(exc)) from exc
    except SpanError as exc:
        raise OpticsError(str(exc)) from exc
    _atomic_write(_optic_text_path(wiki_root, optic, slug), serialize_binding(updated).encode("utf-8"))
    live = read_live(updated, wiki_root)
    return {
        "kind": OpticKind.SPAN.value,
        "bound": True,
        "text": live["text"],
        "path": live["path"],
        "start_line": live["start_line"],
        "end_line": live["end_line"],
        "sha256": live["sha256"],
    }


def read_optic_bytes(wiki_root: Path, optic: str, slug: str) -> tuple[bytes, dict[str, Any]]:
    record = _require_record(wiki_root, optic)
    if record.kind not in BINARY_KINDS:
        raise OpticsError("optic is not a binary kind")
    validate_optic_slug(slug)
    bin_path = _optic_binary_path(wiki_root, optic, slug)
    if not bin_path.is_file():
        raise OpticsError("optic bytes not found")
    meta_path = _optic_meta_path(wiki_root, optic, slug)
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    return bin_path.read_bytes(), meta


def write_optic_bytes(
    wiki_root: Path,
    optic: str,
    slug: str,
    data: bytes,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> dict[str, Any]:
    record = _require_record(wiki_root, optic)
    if record.kind not in BINARY_KINDS:
        raise OpticsError("optic is not a binary kind")
    validate_optic_slug(slug)
    resolved_media = _validate_binary_for_kind(record.kind, data, media_type=media_type)
    digest = hashlib.sha256(data).hexdigest()
    safe_name = (filename or slug).replace("/", "_").replace("\\", "_") or slug
    meta = {
        "filename": safe_name,
        "media_type": resolved_media,
        "size": len(data),
        "sha256": digest,
    }
    _atomic_write(_optic_binary_path(wiki_root, optic, slug), data)
    _atomic_write(
        _optic_meta_path(wiki_root, optic, slug),
        (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return meta


def clear_optic_text(wiki_root: Path, optic: str, slug: str) -> None:
    record = _require_record(wiki_root, optic)
    validate_optic_slug(slug)
    path = _optic_text_path(wiki_root, optic, slug)
    if path.is_file():
        path.unlink()
    if record.kind in BINARY_KINDS:
        bin_path = _optic_binary_path(wiki_root, optic, slug)
        meta_path = _optic_meta_path(wiki_root, optic, slug)
        if bin_path.is_file():
            bin_path.unlink()
        if meta_path.is_file():
            meta_path.unlink()


def clear_slug_from_all_optics(wiki_root: Path, slug: str) -> None:
    validate_optic_slug(slug)
    for record in list_custom_optics(wiki_root):
        clear_optic_text(wiki_root, record.name, slug)


def list_optic_text_rows(
    wiki_root: Path,
    *,
    name: str | None = None,
    slug: str | None = None,
) -> list[dict[str, Any]]:
    """Return rows with stored values matching optional name and/or slug filters."""
    optics = list_custom_optics(wiki_root)
    if name is not None:
        optic_name = validate_optic_name(name)
        optics = [r for r in optics if r.name == optic_name]
    rows: list[dict[str, Any]] = []
    for record in optics:
        text_root = _optic_text_dir(wiki_root, record.name)
        if not text_root.is_dir():
            continue
        seen_slugs: set[str] = set()
        for path in sorted(text_root.iterdir()):
            if not path.is_file():
                continue
            if path.name.endswith(".meta.json"):
                continue
            if path.name.endswith(".bin"):
                file_slug = path.name[: -len(".bin")]
            else:
                file_slug = path.name
            if slug is not None and file_slug != validate_optic_slug(slug):
                continue
            if file_slug in seen_slugs:
                continue
            seen_slugs.add(file_slug)
            value = read_optic_value(wiki_root, record.name, file_slug)
            if record.kind in BINARY_KINDS:
                if not value.get("has_bytes"):
                    continue
                rows.append(
                    {
                        "slug": file_slug,
                        "name": record.name,
                        "kind": record.kind.value,
                        "text": "",
                    }
                )
                continue
            if record.kind == OpticKind.SPAN:
                if not value.get("bound") and not value.get("text"):
                    if not read_stored_binding_raw(wiki_root, record.name, file_slug).strip():
                        continue
            text = str(value.get("text", ""))
            if record.kind != OpticKind.SPAN and not text:
                continue
            row: dict[str, Any] = {
                "slug": file_slug,
                "name": record.name,
                "kind": record.kind.value,
                "text": text,
            }
            if record.kind == OpticKind.SPAN and value.get("bound"):
                row["path"] = value.get("path")
                row["start_line"] = value.get("start_line")
                row["end_line"] = value.get("end_line")
                row["sha256"] = value.get("sha256")
            rows.append(row)
    return rows
