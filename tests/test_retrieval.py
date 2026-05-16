from __future__ import annotations

import pytest

from wisedb.retrieval import reciprocal_rank_fusion
from wisedb.retrieval.graph_expand import build_adjacency, propagate_relevance


def test_rrf_fuses_two_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    assert fused["b"] > fused["a"]
    assert fused["b"] > fused["c"]


def test_graph_propagation_prefers_strong_paths() -> None:
    rows = [
        ("e1", "a", "b", 1.0, False),
        ("e2", "b", "c", 0.5, False),
    ]
    adj = build_adjacency(rows)
    scores = propagate_relevance({"a": 1.0}, adj, max_hops=5, hop_decay=0.9)
    assert scores["a"] == 1.0
    assert scores["b"] == pytest.approx(0.9)
    assert scores["c"] == pytest.approx(0.9 * 0.5 * 0.9)
    assert scores["c"] < scores["b"]
