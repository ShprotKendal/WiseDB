"""Оси-выровненные «коробки» для окон контекста (аналог GiST в SQL)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float


class _HasBBox(Protocol):
    bbox_xmin: Optional[float]
    bbox_ymin: Optional[float]
    bbox_xmax: Optional[float]
    bbox_ymax: Optional[float]


def aabb_intersect(a: BBox, b: BBox) -> bool:
    return a.xmin <= b.xmax and a.xmax >= b.xmin and a.ymin <= b.ymax and a.ymax >= b.ymin


def chunk_spatial_allowed(
    chunk: Optional[_HasBBox],
    *,
    query_box: Optional[BBox],
    strict: bool,
) -> bool:
    """Если задан ``query_box`` — требовать пересечение с AABB чанка (или политика null-bbox)."""
    if query_box is None:
        return True
    if chunk is None:
        return False
    xs = chunk.bbox_xmin
    ys = chunk.bbox_ymin
    xe = chunk.bbox_xmax
    ye = chunk.bbox_ymax
    if xs is None or ys is None or xe is None or ye is None:
        return not strict
    return aabb_intersect(BBox(xs, ys, xe, ye), query_box)
