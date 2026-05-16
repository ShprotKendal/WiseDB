from __future__ import annotations

import json
from pathlib import Path

import pytest

from wisedb import VectorDB


def test_geometry_and_direction_query() -> None:
    db = VectorDB()
    db.upsert_node("A", 0.0, 0.0, {"type": "text", "text": "a"})
    db.upsert_node("B", 1.0, 0.0, {})
    db.add_vector("v1", "A", "B", {})
    g = db.geometry("v1")
    assert g is not None
    assert g.dx == pytest.approx(1.0)
    assert g.dy == pytest.approx(0.0)
    hits = db.query_vectors_by_direction(1.0, 0.0, top_k=1)
    assert hits[0]["id"] == "v1"
    assert hits[0]["score"] == pytest.approx(1.0)


def test_save_load_roundtrip(tmp_path: Path) -> None:
    db = VectorDB()
    db.upsert_node("A", 0.0, 0.0, {})
    db.upsert_node("B", 0.0, 1.0, {})
    db.add_vector("e", "A", "B", {"k": 1})
    path = tmp_path / "db.json"
    db.save(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 2
    db2 = VectorDB.load(str(path))
    assert db2.node_count() == 2
    assert len(db2) == 1


def test_remove_node_cascades_vectors() -> None:
    db = VectorDB()
    db.upsert_node("A", 0.0, 0.0, {})
    db.upsert_node("B", 1.0, 0.0, {})
    db.add_vector("e", "A", "B", {})
    assert db.remove_node("A")
    assert len(db) == 0
    assert db.get_node("B") is not None
