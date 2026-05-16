"""Точка входа: python -m wisedb (editable install или PYTHONPATH=src)."""

from __future__ import annotations

from pathlib import Path

from wisedb.vector_db import VectorDB


def main() -> None:
    db = VectorDB()

    db.upsert_node("A", 0.0, 0.0, {"type": "text", "text": "origin note"})
    db.upsert_node("B", 3.0, 4.0, {"type": "text", "text": "five units along (3,4)"})
    db.upsert_node("C", 1.0, 0.0, {"type": "number", "value": 42})
    db.upsert_node("D", 1.0, 1.0, {"type": "text", "text": "diagonal"})

    db.add_vector("v_AB", "A", "B", {"label": "AB"})
    db.add_vector("v_AC", "A", "C", {"label": "AC"})

    g = db.geometry("v_AB")
    print("геометрия AB:", g)
    print("узлы:", {k: (db.get_node(k).payload) for k in db.node_ids()})
    print("запрос направления (1,0) ~ параллельно AC:", db.query_vectors_by_direction(1.0, 0.0, top_k=2))

    out = Path.cwd() / "data" / "vectors.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    db.save(str(out))
    db2 = VectorDB.load(str(out))
    print("сохранено:", out)
    print("загружено узлов:", db2.node_count(), "векторов:", len(db2))


if __name__ == "__main__":
    main()
