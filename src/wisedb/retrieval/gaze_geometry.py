"""«Взгляд» узла и опционально bearing рёбер для retrieval — без глобальных координат."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


import numpy as np

WISEDB_GAZE_KEY = "wisedb_gaze"
WISEDB_BEARING_KEY = "wisedb_bearing"

BEARING_SOURCE_EXPLICIT = "explicit"
BEARING_SOURCE_RELATION_TYPE = "relation_type"
BEARING_SOURCE_GAZE_PCA = "gaze_pca"


def wrap_signed_pi(angle_rad: float) -> float:
    """Привести угол к (-pi, pi]."""
    a = math.fmod(float(angle_rad) + math.pi, 2.0 * math.pi)
    if a <= 0.0:
        a += 2.0 * math.pi
    return a - math.pi


def bearing_delta(b_from: float, b_to: float) -> float:
    """Кратчайшая разность b_to − b_from в (-pi, pi]."""
    return wrap_signed_pi(float(b_to) - float(b_from))


def interior_angle_from_two_bearings(b_a_to_b: float, b_a_to_c: float) -> float:
    """Внутренний угол в A между направленными рёбрами A→B и A→C (0..π)."""
    d = abs(bearing_delta(b_a_to_b, b_a_to_c))
    return min(d, 2.0 * math.pi - d)


@dataclass(frozen=True)
class Gaze:
    """Направление «вперёд» узла (рад., абстрактный круг: 0 = впереди)."""

    bearing_rad: float
    locked: bool


def _uuid_from_str(s: Any) -> Optional[uuid.UUID]:
    if s is None:
        return None
    if isinstance(s, uuid.UUID):
        return s
    try:
        return uuid.UUID(str(s).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def parse_gaze(metadata: Mapping[str, Any]) -> Optional[Gaze]:
    raw = metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(raw, dict):
        return None
    locked = bool(raw.get("locked", False))
    br = raw.get("bearing_rad")
    toward = _uuid_from_str(raw.get("toward"))
    if br is not None:
        return Gaze(bearing_rad=float(br), locked=locked)
    if toward is not None:
        return Gaze(bearing_rad=float("nan"), locked=locked)
    return None


def gaze_has_deferred_forward(g: Gaze) -> bool:
    return math.isnan(g.bearing_rad)


def bearing_from_relation_type(relation_type: Optional[str]) -> Optional[float]:
    if not relation_type:
        return None
    t = str(relation_type).strip().lower()
    if t in ("supports", "extends", "confirms", "entails"):
        return 0.0
    if t in ("part_of", "instance_of", "subset_of"):
        return math.pi / 4.0
    if t in ("related", "see_also", "associated"):
        return math.pi / 2.0
    if t in ("contradicts", "rejects", "supersedes", "negates", "opposes"):
        return math.pi
    return None


def pca2_xy(embeddings: Mapping[str, Sequence[float]]) -> Optional[Dict[str, Tuple[float, float]]]:
    """2D PCA строк эмбеддингов. None, если меньше 2 id или вырожденный случай."""
    ids = [k for k, v in embeddings.items() if v is not None and len(v) >= 2]
    if len(ids) < 2:
        return None
    mat = np.stack([np.asarray(list(embeddings[i]), dtype=np.float64) for i in ids], axis=0)
    if mat.size == 0:
        return None
    mat = mat - mat.mean(axis=0, keepdims=True)
    if mat.shape[0] < 2:
        return None
    _, _, vt = np.linalg.svd(mat, full_matrices=False)
    if vt.shape[0] < 2:
        return None
    basis = vt[:2, :].T
    proj = mat @ basis
    return {ids[i]: (float(proj[i, 0]), float(proj[i, 1])) for i in range(len(ids))}


def _forward_rad_from_toward(
    source_id: str,
    toward_id: str,
    xy: Mapping[str, Tuple[float, float]],
) -> Optional[float]:
    if source_id not in xy or toward_id not in xy:
        return None
    sx, sy = xy[source_id]
    tx, ty = xy[toward_id]
    return math.atan2(ty - sy, tx - sx)


def resolve_forward_bearing_for_source(
    gaze_bearing_or_nan: float,
    source_id: str,
    toward_id: Optional[str],
    neighbor_ids: Sequence[str],
    embeddings: Mapping[str, Sequence[float]],
) -> Optional[float]:
    """Разрешить NaN bearing из ``toward`` + PCA по соседям источника."""
    if not math.isnan(gaze_bearing_or_nan):
        return float(gaze_bearing_or_nan)
    if toward_id is None:
        return None
    nset = {source_id, toward_id, *neighbor_ids}
    sub: Dict[str, Sequence[float]] = {i: embeddings[i] for i in nset if i in embeddings}
    xy = pca2_xy(sub)
    if xy is None:
        return None
    return _forward_rad_from_toward(str(source_id), str(toward_id), xy)


def parse_edge_bearing_explicit(edge_metadata: Mapping[str, Any], *, as_source: bool) -> Optional[float]:
    raw = edge_metadata.get(WISEDB_BEARING_KEY)
    if not isinstance(raw, dict):
        return None
    key = "from_source_rad" if as_source else "from_target_rad"
    v = raw.get(key)
    if v is None:
        return None
    return float(v)


def gaze_metadata_toward_id(metadata: Mapping[str, Any]) -> Optional[str]:
    raw = metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(raw, dict):
        return None
    tid = _uuid_from_str(raw.get("toward"))
    return str(tid) if tid else None


def gaze_metadata_locked(metadata: Mapping[str, Any]) -> bool:
    raw = metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(raw, dict):
        return False
    return bool(raw.get("locked", False))


def gaze_is_toward_only(metadata: Mapping[str, Any]) -> bool:
    """True, если задан только ``toward`` без явного ``bearing_rad``."""
    raw = metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(raw, dict):
        return False
    return raw.get("bearing_rad") is None and _uuid_from_str(raw.get("toward")) is not None


def gaze_metadata_bearing_raw(metadata: Mapping[str, Any]) -> Optional[float]:
    raw = metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(raw, dict):
        return None
    br = raw.get("bearing_rad")
    if br is None:
        return None
    return float(br)


def parse_edge_bearing_for_hop(
    edge_metadata: Mapping[str, Any],
    *,
    hop_source_is_edge_source: bool,
) -> Optional[float]:
    return parse_edge_bearing_explicit(
        edge_metadata, as_source=hop_source_is_edge_source
    )


def resolve_bearing_directed(
    source_id: str,
    target_id: str,
    *,
    edge_source_id: str,
    edge_target_id: str,
    edge_metadata: Mapping[str, Any],
    relation_type: Optional[str],
    source_metadata: Mapping[str, Any],
    neighbor_ids_for_source: Sequence[str],
    embeddings: Mapping[str, Sequence[float]],
) -> Tuple[Optional[float], Optional[str]]:
    """
    Направленный bearing от оси «вперёд» source_id к target_id.

    ``edge_source_id`` / ``edge_target_id`` — концы строки ребра (явные bearing хранятся по концам).
    """
    hop_from_is_es = str(source_id) == str(edge_source_id)
    ex = parse_edge_bearing_for_hop(
        edge_metadata, hop_source_is_edge_source=hop_from_is_es
    )
    if ex is not None:
        return ex, BEARING_SOURCE_EXPLICIT

    brt = bearing_from_relation_type(relation_type)
    if brt is not None:
        return brt, BEARING_SOURCE_RELATION_TYPE

    g_raw = source_metadata.get(WISEDB_GAZE_KEY)
    if not isinstance(g_raw, dict):
        return None, None
    toward = gaze_metadata_toward_id(source_metadata)
    br = g_raw.get("bearing_rad")
    forward: Optional[float]
    if br is not None:
        forward = float(br)
    elif toward is not None:
        forward = resolve_forward_bearing_for_source(
            float("nan"),
            source_id,
            toward,
            list(neighbor_ids_for_source),
            embeddings,
        )
    else:
        return None, None

    if forward is None or (isinstance(forward, float) and math.isnan(forward)):
        return None, None

    nset = {source_id, target_id, *neighbor_ids_for_source}
    sub = {i: embeddings[i] for i in nset if i in embeddings}
    xy = pca2_xy(sub)
    if xy is None or source_id not in xy or target_id not in xy:
        return None, None

    sx, sy = xy[source_id]
    tx, ty = xy[target_id]
    dir_to_tgt = math.atan2(ty - sy, tx - sx)
    return wrap_signed_pi(dir_to_tgt - forward), BEARING_SOURCE_GAZE_PCA


def angular_multiplier(
    bearing_rad: Optional[float],
    *,
    suppress_behind: bool = True,
    include_opposition: bool = False,
) -> float:
    """Множитель распространённого score по cos(bearing); None → 1.0 (плавающее ребро)."""
    if bearing_rad is None:
        return 1.0
    b = float(bearing_rad)
    base = max(0.05, math.cos(b))
    if suppress_behind and not include_opposition and abs(b) > 5.0 * math.pi / 6.0:
        base *= 0.15
    return base


def triangle_interior_angles_if_complete(
    bearings_ab: Optional[float],
    bearings_ba: Optional[float],
    bearings_bc: Optional[float],
    bearings_cb: Optional[float],
    bearings_ca: Optional[float],
    bearings_ac: Optional[float],
) -> Optional[Tuple[float, float, float]]:
    """
    Внутренние углы в A, B, C, если у каждого неориентированного ребра есть оба directed bearing
    от каждого конца к другому.
    """
    if bearings_ab is None or bearings_ba is None:
        return None
    if bearings_bc is None or bearings_cb is None:
        return None
    if bearings_ca is None or bearings_ac is None:
        return None

    a = interior_angle_from_two_bearings(bearings_ab, bearings_ac)
    b = interior_angle_from_two_bearings(bearings_ba, bearings_bc)
    c = interior_angle_from_two_bearings(bearings_cb, bearings_ca)
    return (a, b, c)


def triangle_coherence_ok(
    angles: Tuple[float, float, float],
    *,
    epsilon_rad: float = 0.35,
) -> bool:
    s = sum(angles)
    return abs(s - math.pi) <= epsilon_rad


def neighbors_undirected(
    edges: Iterable[Tuple[str, str, str, float, bool]],
) -> Dict[str, List[str]]:
    """Список смежности (уникальные соседи) из строк рёбер (eid, src, tgt, w, directed)."""
    adj: Dict[str, set[str]] = {}
    for _eid, src, tgt, _w, directed in edges:
        adj.setdefault(src, set()).add(tgt)
        if not directed:
            adj.setdefault(tgt, set()).add(src)
        else:
            adj.setdefault(tgt, set())
            adj.setdefault(src, set())
    return {k: sorted(v) for k, v in adj.items()}


def bearing_for_step(
    source_id: str,
    target_id: str,
    *,
    edge_source_id: str,
    edge_target_id: str,
    edge_metadata: Mapping[str, Any],
    relation_type: Optional[str],
    source_chunk_metadata: Mapping[str, Any],
    neighbor_ids_for_source: Sequence[str],
    embeddings: Mapping[str, Sequence[float]],
) -> Optional[float]:
    """Bearing для шага ``source_id → target_id``; ``None``, если ребро геометрически «плавает»."""
    br, _prov = resolve_bearing_directed(
        str(source_id),
        str(target_id),
        edge_source_id=str(edge_source_id),
        edge_target_id=str(edge_target_id),
        edge_metadata=edge_metadata,
        relation_type=relation_type,
        source_metadata=source_chunk_metadata,
        neighbor_ids_for_source=neighbor_ids_for_source,
        embeddings=embeddings,
    )
    return br


def build_adjacency_gaze(
    edge_rows: Iterable[Tuple[str, str, str, float, bool]],
    bearings: Mapping[Tuple[str, str], Optional[float]],
) -> Dict[str, List[Tuple[str, float, Optional[float]]]]:
    """Как build_adjacency, но к каждому directed шагу добавлен опциональный bearing."""
    adj: Dict[str, List[Tuple[str, float, Optional[float]]]] = {}
    for _eid, src, tgt, weight, directed in edge_rows:
        b_st = bearings.get((src, tgt))
        adj.setdefault(src, []).append((tgt, float(weight), b_st))
        if not directed:
            b_ts = bearings.get((tgt, src))
            adj.setdefault(tgt, []).append((src, float(weight), b_ts))
    return adj
