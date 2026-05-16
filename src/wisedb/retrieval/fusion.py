"""Reciprocal Rank Fusion (RRF) для гибридного поиска."""

from __future__ import annotations


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """
    Объединить упорядоченные списки id в одну оценку на id.

    ``rankings`` сохраняет порядок каждого ретривера (лучший первым). Id могут повторяться.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
