from __future__ import annotations

import json
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# Минимальная связность > 0 (масштаб стрелки во визуализации обратен relatedness).
_REL_EPS = 1e-6


def _norm_token(s: str) -> str:
    return s.strip().lower()


def _tokenize(text: str) -> Set[str]:
    return {t for t in re.split(r"[^\w]+", text, flags=re.UNICODE) if len(t) >= 2}


@dataclass
class ContextNode:
    """Узел контекста (сущность, фрагмент текста, …)."""

    id: str
    title: str
    content: str
    tags: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextEdge:
    """
    Ненаправленная связь между двумя узлами.

    ``relatedness`` в (0, 1]: больше — сильнее связь.
    Для отрисовки часто используют ``visual_inverse_scale`` = 1 / relatedness
    (крупный глиф = слабая связь).
    """

    id: str
    node_a: str
    node_b: str
    relatedness: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        r = float(self.relatedness)
        if r <= 0 or r > 1.0:
            raise ValueError("relatedness должен быть в (0, 1]")
        object.__setattr__(self, "relatedness", r)

    @property
    def visual_inverse_scale(self) -> float:
        """Больше значение ⇒ слабее связь (обратно relatedness)."""
        return 1.0 / max(self.relatedness, _REL_EPS)


class ContextGraph:
    """
    Граф контекста в памяти для агентов / LLM.

    - Узлы: текст и теги.
    - Рёбра: ``relatedness``; обход предпочитает более сильные.
    - Поиск: подстрока по title/content/tags + соседи по шагам.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, ContextNode] = {}
        self._edges: Dict[str, ContextEdge] = {}
        self._adj: Dict[str, List[Tuple[str, str, float]]] = {}
        self._pair_edge: Dict[Tuple[str, str], str] = {}

    def __len__(self) -> int:
        return len(self._nodes)

    def node_ids(self) -> List[str]:
        return list(self._nodes.keys())

    def edge_ids(self) -> List[str]:
        return list(self._edges.keys())

    def nodes_preview(self) -> List[Tuple[str, str]]:
        """Список (id, title), отсортировано по id."""
        return [(n.id, n.title) for n in sorted(self._nodes.values(), key=lambda x: x.id)]

    def clear(self) -> None:
        self._nodes.clear()
        self._edges.clear()
        self._adj.clear()
        self._pair_edge.clear()

    def upsert_node(
        self,
        node_id: str,
        title: str,
        content: str,
        *,
        tags: Optional[Iterable[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ContextNode:
        tag_t = tuple(sorted({_norm_token(t) for t in (tags or ()) if _norm_token(t)}))
        meta = dict(metadata) if metadata else {}
        n = ContextNode(id=node_id, title=title.strip(), content=content.strip(), tags=tag_t, metadata=meta)
        self._nodes[node_id] = n
        if node_id not in self._adj:
            self._adj[node_id] = []
        return n

    def get_node(self, node_id: str) -> Optional[ContextNode]:
        return self._nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        for eid in list(self._edges):
            e = self._edges[eid]
            if e.node_a == node_id or e.node_b == node_id:
                self.remove_edge(eid)
        del self._nodes[node_id]
        self._adj.pop(node_id, None)
        return True

    def link(
        self,
        node_a: str,
        node_b: str,
        relatedness: float,
        *,
        edge_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        if node_a == node_b:
            raise ValueError("node_a и node_b должны различаться")
        if node_a not in self._nodes or node_b not in self._nodes:
            raise ValueError("оба узла должны существовать")
        key = (node_a, node_b) if node_a < node_b else (node_b, node_a)
        eid = self._pair_edge.get(key)
        meta = dict(metadata) if metadata else {}
        if eid is not None:
            old = self._edges[eid]
            self._edges[eid] = ContextEdge(
                id=eid, node_a=old.node_a, node_b=old.node_b, relatedness=float(relatedness), metadata=meta or old.metadata
            )
            self._refresh_adj_edge(eid)
            return eid
        eid = edge_id or str(uuid.uuid4())
        self._edges[eid] = ContextEdge(
            id=eid, node_a=node_a, node_b=node_b, relatedness=float(relatedness), metadata=meta
        )
        self._pair_edge[key] = eid
        self._attach_adj(node_a, node_b, eid, float(relatedness))
        self._attach_adj(node_b, node_a, eid, float(relatedness))
        return eid

    def _attach_adj(self, u: str, v: str, eid: str, r: float) -> None:
        self._adj.setdefault(u, []).append((v, eid, r))

    def _refresh_adj_edge(self, eid: str) -> None:
        e = self._edges[eid]
        r = e.relatedness
        for u, v in ((e.node_a, e.node_b), (e.node_b, e.node_a)):
            lst = self._adj.get(u, [])
            self._adj[u] = [(x, eid2, r2) if eid2 != eid else (x, eid, r) for (x, eid2, r2) in lst]

    def remove_edge(self, edge_id: str) -> bool:
        e = self._edges.pop(edge_id, None)
        if not e:
            return False
        key = (e.node_a, e.node_b) if e.node_a < e.node_b else (e.node_b, e.node_a)
        self._pair_edge.pop(key, None)
        for u, v in ((e.node_a, e.node_b), (e.node_b, e.node_a)):
            self._adj[u] = [(x, eid, r) for (x, eid, r) in self._adj.get(u, []) if eid != edge_id]
        return True

    def find_seed_nodes(self, query: str) -> List[str]:
        """Узлы, у которых в title/content/tags встречается ``query`` (без учёта регистра)."""
        q = query.strip().lower()
        if not q:
            return []
        seeds: List[str] = []
        for nid, n in self._nodes.items():
            hay = "\n".join([n.title, n.content, " ".join(n.tags)]).lower()
            if q in hay:
                seeds.append(nid)
                continue
            q_tokens = _tokenize(q)
            if q_tokens and q_tokens.intersection(_tokenize(n.title) | _tokenize(n.content) | set(n.tags)):
                seeds.append(nid)
        if not seeds:
            parts = [p for p in re.split(r"\s+", q) if len(p) >= 2]
            for nid, n in self._nodes.items():
                hay = "\n".join([n.title, n.content, " ".join(n.tags)]).lower()
                if any(p in hay for p in parts):
                    seeds.append(nid)
        return seeds

    def relevance_scores(
        self,
        seed_ids: Iterable[str],
        *,
        max_hops: int = 5,
        hop_decay: float = 0.9,
    ) -> Dict[str, float]:
        """
        Multi-source propagation: score at node v is the best (over paths) of
        product(relatedness along edges) * hop_decay**hops. Cycles allowed; improves on better paths.
        """
        seeds = [s for s in seed_ids if s in self._nodes]
        if not seeds:
            return {}
        scores: Dict[str, float] = {s: 1.0 for s in seeds}
        hops: Dict[str, int] = {s: 0 for s in seeds}
        q: deque[str] = deque(seeds)
        while q:
            u = q.popleft()
            hu = hops[u]
            if hu >= max_hops:
                continue
            su = scores[u]
            for v, _eid, rel in self._adj.get(u, ()):
                cand = su * rel * hop_decay
                nh = hu + 1
                if cand > scores.get(v, 0.0) + 1e-12:
                    scores[v] = cand
                    hops[v] = nh
                    q.append(v)
        return scores

    def expand_context_markdown(
        self,
        query: str,
        *,
        max_hops: int = 5,
        max_nodes: int = 48,
        hop_decay: float = 0.9,
    ) -> str:
        """
        Build a compact markdown bundle for an LLM: seeds + related nodes, sorted by relevance.
        """
        seeds = self.find_seed_nodes(query)
        scores = self.relevance_scores(seeds, max_hops=max_hops, hop_decay=hop_decay)
        if not scores:
            return f"# Context graph\n\n_Query:_ `{query}`\n\n_(no matching nodes; graph empty or no hits)._"

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:max_nodes]
        chosen: Set[str] = {nid for nid, _ in ranked}

        lines: List[str] = [
            f"# WiseDB context bundle",
            f"",
            f"Query: **{query}**",
            f"",
            f"## Seed matches",
        ]
        if seeds:
            for sid in seeds:
                n = self._nodes[sid]
                lines.append(f"- **{n.id}** — {n.title}")
        else:
            lines.append("_(no direct substring hit; showing strongest graph neighbors via token overlap.)_")

        lines.extend(["", "## Nodes (ranked for the model)", ""])
        for nid, sc in ranked:
            n = self._nodes[nid]
            tags = ", ".join(n.tags) if n.tags else "—"
            lines.append(f"### `{n.id}` — relevance **{sc:.4f}**")
            lines.append(f"**Title:** {n.title}")
            lines.append(f"**Tags:** {tags}")
            lines.append("")
            lines.append(n.content if n.content else "_(empty body)_")
            lines.append("")

        lines.extend(["## Links between retrieved nodes", ""])
        edge_lines: List[str] = []
        for e in self._edges.values():
            if e.node_a in chosen and e.node_b in chosen:
                inv = e.visual_inverse_scale
                edge_lines.append(
                    f"- `{e.node_a}` ↔ `{e.node_b}` — relatedness **{e.relatedness:.3f}** "
                    f"(visual inverse scale **{inv:.3f}**; stronger link ⇒ smaller scale)"
                )
        lines.extend(sorted(edge_lines) if edge_lines else ["_(no internal edges in this slice)._"])

        lines.extend(["", "---", "_End of bundle._"])
        return "\n".join(lines)

    def save(self, path: str) -> None:
        payload = {
            "version": 3,
            "model": "context_graph",
            "nodes": [
                {
                    "id": n.id,
                    "title": n.title,
                    "content": n.content,
                    "tags": list(n.tags),
                    "metadata": n.metadata,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "id": e.id,
                    "node_a": e.node_a,
                    "node_b": e.node_b,
                    "relatedness": e.relatedness,
                    "metadata": e.metadata,
                }
                for e in self._edges.values()
            ],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "ContextGraph":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("version") != 3 or raw.get("model") != "context_graph":
            raise ValueError("ожидается JSON version 3 model context_graph")
        g = cls()
        for item in raw.get("nodes", []):
            g.upsert_node(
                item["id"],
                item["title"],
                item["content"],
                tags=item.get("tags"),
                metadata=item.get("metadata"),
            )
        for item in raw.get("edges", []):
            g.link(
                item["node_a"],
                item["node_b"],
                item["relatedness"],
                edge_id=item["id"],
                metadata=item.get("metadata"),
            )
        return g
