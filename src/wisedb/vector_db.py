from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Модель данных: плоскость (оси x, y), узлы — точки, векторы — направленные отрезки.


@dataclass
class Node:
    """Точка на плоскости (x, y); полезная нагрузка в ``payload``."""

    id: str
    x: float
    y: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("координаты узла должны быть конечными числами")
        if self.payload is None:
            object.__setattr__(self, "payload", {})


@dataclass
class DirectedVector:
    """
    Геометрический вектор: от узла-хвоста к узлу-голове.
    Смещение (dx, dy) = (head.x - tail.x, head.y - tail.y).
    """

    id: str
    tail_node_id: str
    head_node_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass
class VectorGeometry:
    """Компоненты направленного вектора на плоскости."""

    dx: float
    dy: float
    length: float
    angle_rad: float


class VectorDB:
    """
    Узлы (2D-точки с данными) и направленные векторы (хвост → голова).

    Направление и длина вектора задаются только координатами концов.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, Node] = {}
        self._vectors: Dict[str, DirectedVector] = {}

    def __len__(self) -> int:
        return len(self._vectors)

    def node_count(self) -> int:
        return len(self._nodes)

    def node_ids(self) -> List[str]:
        return list(self._nodes.keys())

    def vector_ids(self) -> List[str]:
        return list(self._vectors.keys())

    def clear(self) -> None:
        self._nodes.clear()
        self._vectors.clear()

    def upsert_node(self, node_id: str, x: float, y: float, payload: Optional[Dict[str, Any]] = None) -> None:
        pl = dict(payload) if payload else {}
        self._nodes[node_id] = Node(id=node_id, x=float(x), y=float(y), payload=pl)

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        to_drop = [vid for vid, v in self._vectors.items() if v.tail_node_id == node_id or v.head_node_id == node_id]
        for vid in to_drop:
            del self._vectors[vid]
        del self._nodes[node_id]
        return True

    def add_vector(
        self,
        vector_id: str,
        tail_node_id: str,
        head_node_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if tail_node_id == head_node_id:
            raise ValueError("хвост и голова должны быть разными узлами")
        if tail_node_id not in self._nodes or head_node_id not in self._nodes:
            raise ValueError("оба узла (хвост и голова) должны существовать")
        meta = dict(metadata) if metadata else {}
        self._vectors[vector_id] = DirectedVector(
            id=vector_id,
            tail_node_id=tail_node_id,
            head_node_id=head_node_id,
            metadata=meta,
        )

    def get_vector(self, vector_id: str) -> Optional[DirectedVector]:
        return self._vectors.get(vector_id)

    def remove_vector(self, vector_id: str) -> bool:
        return self._vectors.pop(vector_id, None) is not None

    def geometry(self, vector_id: str) -> Optional[VectorGeometry]:
        v = self._vectors.get(vector_id)
        if not v:
            return None
        tail = self._nodes[v.tail_node_id]
        head = self._nodes[v.head_node_id]
        dx = head.x - tail.x
        dy = head.y - tail.y
        length = math.hypot(dx, dy)
        angle_rad = math.atan2(dy, dx)
        return VectorGeometry(dx=dx, dy=dy, length=length, angle_rad=angle_rad)

    def vectors_from(self, node_id: str) -> List[DirectedVector]:
        return [v for v in self._vectors.values() if v.tail_node_id == node_id]

    def vectors_to(self, node_id: str) -> List[DirectedVector]:
        return [v for v in self._vectors.values() if v.head_node_id == node_id]

    def vectors_incident(self, node_id: str) -> List[DirectedVector]:
        return [v for v in self._vectors.values() if v.tail_node_id == node_id or v.head_node_id == node_id]

    def query_vectors_by_direction(
        self,
        dx: float,
        dy: float,
        top_k: int = 5,
        *,
        metadata_filter: Optional[Union[Dict[str, Any], Callable[[Dict[str, Any]], bool]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Ранжировать векторы по косинусной близости (dx, dy) к смещению каждого вектора.
        Вырожденные (нулевой длины) получают оценку 0.
        """
        if top_k <= 0:
            raise ValueError("top_k должен быть > 0")
        q_len = math.hypot(dx, dy)
        if q_len == 0.0:
            raise ValueError("направление запроса не должно быть нулевым")

        rows: List[Tuple[str, float, DirectedVector]] = []
        for vid, vec in self._vectors.items():
            if not _passes_filter(vec.metadata, metadata_filter):
                continue
            g = self.geometry(vid)
            if g is None or g.length == 0.0:
                score = 0.0
            else:
                score = (dx * g.dx + dy * g.dy) / (q_len * g.length)
            rows.append((vid, score, vec))

        rows.sort(key=lambda t: t[1], reverse=True)
        out: List[Dict[str, Any]] = []
        for vid, score, vec in rows[:top_k]:
            g = self.geometry(vid)
            out.append(
                {
                    "id": vid,
                    "score": score,
                    "tail_node_id": vec.tail_node_id,
                    "head_node_id": vec.head_node_id,
                    "metadata": dict(vec.metadata),
                    "dx": g.dx if g else 0.0,
                    "dy": g.dy if g else 0.0,
                }
            )
        return out

    def save(self, path: str) -> None:
        payload = {
            "version": 2,
            "model": "plane_nodes_directed_vectors",
            "nodes": [
                {"id": n.id, "x": n.x, "y": n.y, "payload": n.payload}
                for n in self._nodes.values()
            ],
            "vectors": [
                {
                    "id": v.id,
                    "tail_node_id": v.tail_node_id,
                    "head_node_id": v.head_node_id,
                    "metadata": v.metadata,
                }
                for v in self._vectors.values()
            ],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "VectorDB":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        ver = raw.get("version")

        if ver == 2:
            db = cls()
            for item in raw.get("nodes", []):
                db.upsert_node(item["id"], item["x"], item["y"], item.get("payload"))
            for item in raw.get("vectors", []):
                db.add_vector(
                    item["id"],
                    item["tail_node_id"],
                    item["head_node_id"],
                    item.get("metadata"),
                )
            return db

        if ver == 1 or ("dimension" in raw and "records" in raw):
            raise ValueError(
                "файл legacy v1 (записи эмбеддингов). Ожидается v2 (узлы + векторы tail/head). "
                "Конвертируйте или используйте старый скрипт."
            )

        raise ValueError("неизвестный формат файла")


def _passes_filter(
    metadata: Dict[str, Any],
    metadata_filter: Optional[Union[Dict[str, Any], Callable[[Dict[str, Any]], bool]]],
) -> bool:
    if metadata_filter is None:
        return True
    if callable(metadata_filter):
        return bool(metadata_filter(metadata))
    for key, want in metadata_filter.items():
        if key not in metadata or metadata[key] != want:
            return False
    return True
