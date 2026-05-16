from __future__ import annotations

import math

import pytest

from wisedb.retrieval.gaze_geometry import (
    angular_multiplier,
    bearing_from_relation_type,
    build_adjacency_gaze,
    pca2_xy,
    resolve_bearing_directed,
    triangle_coherence_ok,
    triangle_interior_angles_if_complete,
    wrap_signed_pi,
)
from wisedb.retrieval.graph_expand import build_adjacency, propagate_relevance, propagate_relevance_gaze


def test_wrap_signed_pi() -> None:
    assert wrap_signed_pi(0) == pytest.approx(0)
    assert wrap_signed_pi(math.pi / 2) == pytest.approx(math.pi / 2)
    assert abs(wrap_signed_pi(10.0)) <= math.pi + 1e-9


def test_angular_multiplier_none_is_neutral() -> None:
    assert angular_multiplier(None) == 1.0


def test_angular_multiplier_behind_suppressed() -> None:
    m = angular_multiplier(math.pi, suppress_behind=True, include_opposition=False)
    assert m < angular_multiplier(0.0, suppress_behind=True, include_opposition=False)
    m2 = angular_multiplier(math.pi, suppress_behind=True, include_opposition=True)
    assert m2 > m


def test_bearing_from_relation_type() -> None:
    assert bearing_from_relation_type("supports") == pytest.approx(0.0)
    assert bearing_from_relation_type("contradicts") == pytest.approx(math.pi)


def test_resolve_explicit_edge_bearing() -> None:
    br, prov = resolve_bearing_directed(
        "a",
        "b",
        edge_source_id="a",
        edge_target_id="b",
        edge_metadata={"wisedb_bearing": {"from_source_rad": 0.5}},
        relation_type=None,
        source_metadata={},
        neighbor_ids_for_source=[],
        embeddings={},
    )
    assert br == pytest.approx(0.5)
    assert prov == "explicit"


def test_resolve_relation_type_before_gaze() -> None:
    br, prov = resolve_bearing_directed(
        "a",
        "b",
        edge_source_id="a",
        edge_target_id="b",
        edge_metadata={},
        relation_type="contradicts",
        source_metadata={"wisedb_gaze": {"bearing_rad": 0.0}},
        neighbor_ids_for_source=["b"],
        embeddings={"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0]},
    )
    assert br == pytest.approx(math.pi)
    assert prov == "relation_type"


def test_pca2_xy_two_points() -> None:
    xy = pca2_xy(
        {
            "a": [1.0, 0.0, 0.0],
            "b": [0.0, 2.0, 0.0],
        }
    )
    assert xy is not None
    assert "a" in xy and "b" in xy


def test_triangle_coherence_equilateral_radians() -> None:
    ok = (math.pi / 3, math.pi / 3, math.pi / 3)
    assert triangle_coherence_ok(ok)


def test_triangle_interior_none_if_incomplete() -> None:
    assert triangle_interior_angles_if_complete(0.0, None, 0.0, 0.0, 0.0, 0.0) is None


def test_resolve_explicit_from_target_rad_on_reverse_hop() -> None:
    br, prov = resolve_bearing_directed(
        "b",
        "a",
        edge_source_id="a",
        edge_target_id="b",
        edge_metadata={"wisedb_bearing": {"from_target_rad": 0.25}},
        relation_type=None,
        source_metadata={},
        neighbor_ids_for_source=[],
        embeddings={},
    )
    assert br == pytest.approx(0.25)
    assert prov == "explicit"


def test_propagate_gaze_behind_hops_weaker_than_plain() -> None:
    rows = [("e1", "a", "b", 1.0, False)]
    bearings = {("a", "b"): math.pi, ("b", "a"): math.pi}
    adj_g = build_adjacency_gaze(rows, bearings)
    adj = build_adjacency(rows)
    s_g = propagate_relevance_gaze({"a": 1.0}, adj_g, hop_decay=0.9, suppress_behind=True)
    s_p = propagate_relevance({"a": 1.0}, adj, hop_decay=0.9)
    assert s_g["b"] < s_p["b"]
