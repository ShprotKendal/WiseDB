"""Maximal Marginal Relevance (MMR): разжатие дубликатов в плотном поиске / контексте графа."""

from __future__ import annotations

import math
from typing import Callable, Optional


def _norm_vec(v: list[float]) -> Optional[list[float]]:
    if not v:
        return None
    s = math.sqrt(sum(x * x for x in v))
    if s <= 0:
        return None
    return [x / s for x in v]


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def maximal_marginal_relevance(
    ordered_candidates: list[tuple[str, float]],
    *,
    query_embedding: list[float],
    doc_embedding: dict[str, list[float]],
    k: int,
    lambda_mult: float = 0.5,
    sim_fn: Callable[[list[float], list[float]], float] = dot,
) -> list[tuple[str, float]]:
    """
    Выбрать до ``k`` id из ``ordered_candidates`` (уже по убыванию score).

    ``lambda_mult`` ∈ [0, 1]: 1.0 ≈ чистая релевантность; меньше — больше разнообразия к уже выбранным.
    """
    if k <= 0 or not ordered_candidates:
        return []
    qv = _norm_vec(query_embedding)
    if qv is None:
        return list(ordered_candidates[:k])

    pool = [(i, cid, sc) for i, (cid, sc) in enumerate(ordered_candidates)]
    selected: list[tuple[str, float]] = []
    selected_vecs: list[list[float]] = []

    def rel_terms(cid: str) -> tuple[float, Optional[list[float]]]:
        dv = _norm_vec(doc_embedding.get(cid, []))
        rel_q = sim_fn(qv, dv) if dv is not None else 0.0
        return rel_q, dv

    while pool and len(selected) < k:
        best_i: Optional[int] = None
        best_score = -1e100
        for idx, (ord_i, cid, relevance_score) in enumerate(pool):
            rel_q, dv = rel_terms(cid)
            if not selected_vecs or dv is None:
                mmr = float(lambda_mult) * relevance_score + (1.0 - float(lambda_mult)) * rel_q
            else:
                max_sim = max(sim_fn(dv, sv) for sv in selected_vecs)
                mmr = float(lambda_mult) * relevance_score - (1.0 - float(lambda_mult)) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_i = idx
        assert best_i is not None
        _, cid, sc = pool.pop(best_i)
        selected.append((cid, sc))
        _, dv = rel_terms(cid)
        if dv is not None:
            selected_vecs.append(dv)
    return selected
