"""Распространение релевантности по графу (много шагов, взвешенные рёбра)."""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Tuple

from wisedb.retrieval.gaze_geometry import angular_multiplier


def build_adjacency(
    edges: Iterable[Tuple[str, str, str, float, bool]],
) -> Dict[str, List[Tuple[str, float]]]:
    """
    Список смежности из рёбер.

    Каждая строка: (edge_id, source_id, target_id, weight, directed).
    Ненаправленное ребро добавляет обе стороны.
    """
    adj: Dict[str, List[Tuple[str, float]]] = {}
    for _eid, src, tgt, weight, directed in edges:
        adj.setdefault(src, []).append((tgt, weight))
        if not directed:
            adj.setdefault(tgt, []).append((src, weight))
    return adj


def propagate_relevance(
    seed_scores: Dict[str, float],
    adjacency: Dict[str, List[Tuple[str, float]]],
    *,
    max_hops: int = 5,
    hop_decay: float = 0.9,
) -> Dict[str, float]:
    """
    Мультиисточник: оценка узла v — максимум по путям вида
    ``parent_score * edge_weight * hop_decay`` длиной не больше ``max_hops``.

    ``seed_scores``: id узла → начальная сила (например, после гибридного поиска).
    """
    seeds = {sid: float(s) for sid, s in seed_scores.items() if float(s) > 0}
    if not seeds:
        return {}
    scores: Dict[str, float] = dict(seeds)
    hops: Dict[str, int] = {s: 0 for s in seeds}
    q: deque[str] = deque(seeds.keys())
    while q:
        u = q.popleft()
        hu = hops[u]
        if hu >= max_hops:
            continue
        su = scores[u]
        for v, rel in adjacency.get(u, ()):
            cand = su * rel * hop_decay
            nh = hu + 1
            if cand > scores.get(v, 0.0) + 1e-12:
                scores[v] = cand
                hops[v] = nh
                q.append(v)
    return scores


def propagate_relevance_gaze(
    seed_scores: Dict[str, float],
    adjacency: Dict[str, List[Tuple[str, float, Optional[float]]]],
    *,
    max_hops: int = 5,
    hop_decay: float = 0.9,
    suppress_behind: bool = True,
    include_opposition: bool = False,
) -> Dict[str, float]:
    """
    Как :func:`propagate_relevance`, но у каждого шага может быть опциональный bearing;
    ``None`` ⇒ угловой множитель 1.0 («плавающее» ребро).
    """
    seeds = {sid: float(s) for sid, s in seed_scores.items() if float(s) > 0}
    if not seeds:
        return {}
    scores: Dict[str, float] = dict(seeds)
    hops: Dict[str, int] = {s: 0 for s in seeds}
    q: deque[str] = deque(seeds.keys())
    while q:
        u = q.popleft()
        hu = hops[u]
        if hu >= max_hops:
            continue
        su = scores[u]
        for v, rel, br in adjacency.get(u, ()):
            ang = angular_multiplier(
                br,
                suppress_behind=suppress_behind,
                include_opposition=include_opposition,
            )
            cand = su * rel * hop_decay * ang
            nh = hu + 1
            if cand > scores.get(v, 0.0) + 1e-12:
                scores[v] = cand
                hops[v] = nh
                q.append(v)
    return scores
