"""Live file spans for span-type optics."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from wisedb.workspace import WorkspaceError, code_root_path

_SPAN_VERSION = 1
_REL_PATH_RE = re.compile(r"^(?!.*\.\.)[a-zA-Z0-9_./-]+$")


class SpanError(Exception):
    """Span bind, read, or write failure."""


class SpanConflictError(SpanError):
    """File changed externally or spans intersect."""


@dataclass(frozen=True, slots=True)
class SpanBinding:
    v: int
    path: str
    start_line: int
    end_line: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "sha256": self.sha256,
        }


def validate_rel_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise SpanError("invalid span path")
    if not _REL_PATH_RE.fullmatch(normalized):
        raise SpanError("invalid span path")
    return normalized


def _resolve_file(wiki_root: Path, rel_path: str) -> Path:
    try:
        root = code_root_path(wiki_root)
    except WorkspaceError as exc:
        raise SpanError(str(exc)) from exc
    if root is None:
        raise SpanError("code_root is not configured")
    rel = validate_rel_path(rel_path)
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise SpanError("path escapes code_root") from None
    if candidate.is_symlink():
        raise SpanError("symbolic links are not allowed")
    if not candidate.is_file():
        raise SpanError("file not found")
    return candidate


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if not text:
        return []
    return text.splitlines(keepends=True)


def _fragment_sha256(lines: list[str], start_line: int, end_line: int) -> tuple[str, str]:
    if start_line < 1:
        raise SpanError("invalid line range")
    if end_line < start_line - 1:
        raise SpanError("invalid line range")
    if end_line < start_line:
        return "", hashlib.sha256(b"").hexdigest()
    if start_line > len(lines):
        raise SpanError("start_line beyond end of file")
    end_line = min(end_line, len(lines))
    fragment = "".join(lines[start_line - 1 : end_line])
    digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    return fragment, digest


def parse_binding(raw: str) -> SpanBinding | None:
    if not raw.strip():
        return None
    data = json.loads(raw)
    if data.get("v") != _SPAN_VERSION:
        raise SpanError("unsupported span document version")
    path = validate_rel_path(str(data.get("path", "")))
    start_line = int(data["start_line"])
    end_line = int(data["end_line"])
    sha256 = str(data.get("sha256", ""))
    if not sha256:
        raise SpanError("span binding missing sha256")
    return SpanBinding(v=_SPAN_VERSION, path=path, start_line=start_line, end_line=end_line, sha256=sha256)


def serialize_binding(binding: SpanBinding) -> str:
    return json.dumps(binding.to_dict(), indent=2, sort_keys=True) + "\n"


def read_live(binding: SpanBinding, wiki_root: Path) -> dict[str, Any]:
    file_path = _resolve_file(wiki_root, binding.path)
    lines = _read_lines(file_path)
    text, digest = _fragment_sha256(lines, binding.start_line, binding.end_line)
    return {
        "text": text,
        "path": binding.path,
        "start_line": binding.start_line,
        "end_line": binding.end_line,
        "sha256": digest,
        "stored_sha256": binding.sha256,
    }


def bind_span(
    wiki_root: Path,
    rel_path: str,
    start_line: int,
    end_line: int,
) -> SpanBinding:
    if start_line < 1 or end_line < start_line:
        raise SpanError("invalid line range")
    file_path = _resolve_file(wiki_root, rel_path)
    lines = _read_lines(file_path)
    _, digest = _fragment_sha256(lines, start_line, end_line)
    return SpanBinding(
        v=_SPAN_VERSION,
        path=validate_rel_path(rel_path),
        start_line=start_line,
        end_line=end_line,
        sha256=digest,
    )


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _collect_span_stored_paths(
    wiki_root: Path,
    *,
    exclude_optic: str | None = None,
    exclude_slug: str | None = None,
) -> list[tuple[str, str, SpanBinding]]:
    from wisedb.optics import OpticKind, get_custom_optic, list_custom_optics, read_stored_binding_raw

    rows: list[tuple[str, str, SpanBinding]] = []
    for record in list_custom_optics(wiki_root):
        if record.kind != OpticKind.SPAN:
            continue
        if exclude_optic and record.name == exclude_optic:
            slug_filter = exclude_slug
        else:
            slug_filter = None
        optic_dir = wiki_root / "_wisedb" / "optics" / record.name
        if not optic_dir.is_dir():
            continue
        for path in optic_dir.iterdir():
            if not path.is_file() or path.name.endswith(".bin") or path.name.endswith(".meta.json"):
                continue
            slug = path.name
            if slug_filter is not None and slug == slug_filter:
                continue
            raw = read_stored_binding_raw(wiki_root, record.name, slug)
            binding = parse_binding(raw)
            if binding is not None:
                rows.append((record.name, slug, binding))
    return rows


def _assert_no_intersection(
    wiki_root: Path,
    path: str,
    start_line: int,
    end_line: int,
    *,
    exclude_optic: str,
    exclude_slug: str,
) -> None:
    for optic, slug, other in _collect_span_stored_paths(
        wiki_root, exclude_optic=exclude_optic, exclude_slug=exclude_slug
    ):
        if other.path != path:
            continue
        if _ranges_overlap(start_line, end_line, other.start_line, other.end_line):
            raise SpanConflictError(f"span intersects {optic}:{slug}")


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def apply_span_edit(
    wiki_root: Path,
    binding: SpanBinding,
    new_text: str,
    base_sha256: str,
    *,
    optic: str,
    slug: str,
    update_stored: Callable[[str, str, str], None],
) -> SpanBinding:
    if base_sha256 != binding.sha256:
        raise SpanConflictError("base_sha256 does not match stored binding")
    file_path = _resolve_file(wiki_root, binding.path)
    lines = _read_lines(file_path)
    _, current_digest = _fragment_sha256(lines, binding.start_line, binding.end_line)
    if current_digest != base_sha256:
        raise SpanConflictError("file fragment changed since last read")

    _assert_no_intersection(
        wiki_root,
        binding.path,
        binding.start_line,
        binding.end_line,
        exclude_optic=optic,
        exclude_slug=slug,
    )

    if new_text == "":
        new_lines: list[str] = []
        new_count = 0
    else:
        new_lines = new_text.splitlines(keepends=True)
        if not new_lines:
            new_lines = [new_text]
        elif not new_text.endswith(("\n", "\r")):
            newline = "\n"
            if lines:
                sample = lines[min(binding.start_line - 1, len(lines) - 1)]
                if sample.endswith("\r\n"):
                    newline = "\r\n"
                elif sample.endswith("\r"):
                    newline = "\r"
            new_lines[-1] = new_lines[-1].rstrip("\r\n") + newline
        new_count = len(new_lines)

    old_count = binding.end_line - binding.start_line + 1
    before = lines[: binding.start_line - 1]
    after = lines[binding.end_line :]
    merged = before + new_lines + after
    new_content = "".join(merged)
    _atomic_write_text(file_path, new_content)

    delta = new_count - old_count
    if delta != 0:
        for other_optic, other_slug, other in _collect_span_stored_paths(wiki_root):
            if other_optic == optic and other_slug == slug:
                continue
            if other.path != binding.path:
                continue
            if other.start_line > binding.end_line:
                shifted = SpanBinding(
                    v=other.v,
                    path=other.path,
                    start_line=other.start_line + delta,
                    end_line=other.end_line + delta,
                    sha256=other.sha256,
                )
                update_stored(other_optic, other_slug, serialize_binding(shifted))

    if new_count > 0:
        new_end = binding.start_line + new_count - 1
    else:
        new_end = binding.start_line - 1

    refreshed_lines = _read_lines(file_path)
    _, new_digest = _fragment_sha256(refreshed_lines, binding.start_line, new_end)

    return SpanBinding(
        v=_SPAN_VERSION,
        path=binding.path,
        start_line=binding.start_line,
        end_line=new_end,
        sha256=new_digest,
    )
