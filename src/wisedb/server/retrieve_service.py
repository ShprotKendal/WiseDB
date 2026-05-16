"""Оркестрация гибридного поиска (плотный + лексика + RRF + расширение по графу)."""

from __future__ import annotations

import math
import uuid
from collections import deque
from typing import Dict, List, Optional, Tuple

from wisedb.retrieval import (
    build_adjacency,
    propagate_relevance,
    propagate_relevance_gaze,
    reciprocal_rank_fusion,
)
from wisedb.retrieval.gaze_geometry import (
    build_adjacency_gaze,
    resolve_bearing_directed,
    triangle_coherence_ok,
    triangle_interior_angles_if_complete,
)
from wisedb.server.config import get_settings
from wisedb.server.constants import EMBEDDING_DIM
from wisedb.server.embeddings import get_embedding_provider
from wisedb.server.memory_engine import ChunkRecord, EdgeRecord, NeuralContextDB
from wisedb.server.schemas import RetrieveRequest, RetrievedChunk, RetrieveResponse


def _bfs_hop_distances(seeds: set[str], adjacency: Dict[str, List[tuple[str, float]]]) -> dict[str, int]:
    dist: dict[str, int] = {s: 0 for s in seeds}
    q: deque[str] = deque(seeds)
    while q:
        u = q.popleft()
        for v, _w in adjacency.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


def _sector_label(bearing_rad: Optional[float]) -> str:
    if bearing_rad is None:
        return "плавающий"
    a = abs(float(bearing_rad))
    if a <= math.pi / 4:
        return "впереди"
    if a <= 3 * math.pi / 4:
        return "сбоку"
    return "сзади"


def _neighbors_of(anchor: str, internal_edges: list[EdgeRecord]) -> set[str]:
    out: set[str] = set()
    for e in internal_edges:
        sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
        if sa == anchor:
            out.add(sb)
        if sb == anchor:
            out.add(sa)
    return out


def _triangle_coherence_lines(
    chosen: set[str],
    internal_edges: list[EdgeRecord],
    bearings: Dict[Tuple[str, str], Optional[float]],
    *,
    max_nodes: int = 16,
) -> list[str]:
    if len(chosen) > max_nodes or len(chosen) < 3:
        return []
    und: Dict[str, set[str]] = {x: set() for x in chosen}
    for e in internal_edges:
        sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
        if sa in chosen and sb in chosen:
            und[sa].add(sb)
            und[sb].add(sa)
    ids = sorted(chosen)
    lines: list[str] = []
    for i, a in enumerate(ids):
        for j in range(i + 1, len(ids)):
            b = ids[j]
            if b not in und[a]:
                continue
            for k in range(j + 1, len(ids)):
                c = ids[k]
                if c not in und[a] or c not in und[b]:
                    continue
                ang = triangle_interior_angles_if_complete(
                    bearings.get((a, b)),
                    bearings.get((b, a)),
                    bearings.get((b, c)),
                    bearings.get((c, b)),
                    bearings.get((c, a)),
                    bearings.get((a, c)),
                )
                if ang is None:
                    lines.append(f"- `{a}` `{b}` `{c}` — _недостаточно направлений для замыкания треугольника_")
                    continue
                ok = triangle_coherence_ok(ang)
                deg = sum(math.degrees(x) for x in ang)
                coh = "согласован" if ok else "расход с π"
                lines.append(
                    f"- `{a}` `{b}` `{c}` — сумма внутренних углов **{deg:.1f}°** "
                    f"({coh})"
                )
    return lines


def _markdown_bundle(
    query: str,
    ranked: list[tuple[str, float]],
    chunks: dict[str, ChunkRecord],
    hop_dist: dict[str, int],
    internal_edges: list[EdgeRecord],
    chosen: set[str],
    bearings: Dict[Tuple[str, str], Optional[float]],
    anchor_id: Optional[str],
) -> str:
    lines: list[str] = [
        "# Пакет контекста WiseDB",
        "",
        f"Запрос: **{query}**",
        "",
        "## Узлы (ранжированы для модели)",
        "",
    ]
    anchor_neighbors = _neighbors_of(anchor_id, internal_edges) if anchor_id else set()

    for cid, sc in ranked:
        c = chunks.get(cid)
        if c is None:
            continue
        hd = hop_dist.get(cid, 0)
        tags = ", ".join(c.tags) if c.tags else "—"
        gaze_tag = ""
        if anchor_id and cid != anchor_id:
            br = bearings.get((anchor_id, cid))
            if br is not None:
                gaze_tag = f" **[{_sector_label(br)}]** (≈ {math.degrees(br):.1f}° от якоря)"
            elif cid in anchor_neighbors:
                gaze_tag = " **[плавающий]** (ребро к якорю, без bearing)"
        lines.append(f"### `{c.id}` — вес **{sc:.4f}** (шагов по графу: {hd}){gaze_tag}")
        lines.append(f"**Заголовок:** {c.title}")
        lines.append(f"**Теги:** {tags}")
        lines.append("")
        lines.append(c.body if c.body else "_(пустое тело)_")
        lines.append("")

    lines.extend(["## Связи между найденными узлами", ""])
    edge_lines: list[str] = []
    for e in internal_edges:
        sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
        if sa not in chosen or sb not in chosen:
            continue
        arrow = "→" if e.directed else "↔"
        inv = 1.0 / max(float(e.weight), 1e-6)
        parts = [
            f"`{sa}` {arrow} `{sb}` — вес **{float(e.weight):.3f}** "
            f"(условный обратный масштаб **{inv:.3f}**; сильнее связь ⇒ меньше число)"
        ]
        bf = bearings.get((sa, sb))
        if bf is not None:
            parts[0] += f" · bearing `{sa}`→`{sb}` ≈ **{math.degrees(bf):.1f}°**"
        br = bearings.get((sb, sa))
        if br is not None and not e.directed:
            parts[0] += f" · `{sb}`→`{sa}` ≈ **{math.degrees(br):.1f}°**"
        if e.relation_type:
            parts[0] += f" · отношение **{e.relation_type}**"
        edge_lines.append(parts[0])
    lines.extend(sorted(edge_lines) if edge_lines else ["_(в этом срезе нет внутренних рёбер)._"])

    tri = _triangle_coherence_lines(chosen, internal_edges, bearings)
    if tri:
        lines.extend(["", "## Геометрия треугольников (bearing замыкает треугольник)", ""])
        lines.extend(tri)

    lines.extend(["", "---", "_Конец пакета._"])
    return "\n".join(lines)


def _compute_bearings(
    internal_edges: list[EdgeRecord],
    neigh_map: Dict[str, List[str]],
    chunk_meta: Dict[str, dict],
    emb: Dict[str, List[float]],
) -> Dict[Tuple[str, str], Optional[float]]:
    bearings: Dict[Tuple[str, str], Optional[float]] = {}
    for e in internal_edges:
        md = dict(e.metadata_json)
        sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
        pairs = [(sa, sb)] if e.directed else [(sa, sb), (sb, sa)]
        for a, b in pairs:
            br, _prov = resolve_bearing_directed(
                a,
                b,
                edge_source_id=sa,
                edge_target_id=sb,
                edge_metadata=md,
                relation_type=e.relation_type,
                source_metadata=chunk_meta.get(a, {}),
                neighbor_ids_for_source=neigh_map.get(a, []),
                embeddings=emb,
            )
            bearings[(a, b)] = br
    return bearings


async def retrieve(
    store: NeuralContextDB,
    req: RetrieveRequest,
    *,
    query_embedding: list[float],
    reinforce_sleep_edges: bool = False,
) -> RetrieveResponse:
    dense_ids = await store.search_dense_ids(req.tenant_id, query_embedding, top_k=req.top_k_dense)
    lexical_ids = await store.search_lexical_ids(req.tenant_id, req.query, top_k=req.top_k_lexical)

    dense_ranks = {cid: idx + 1 for idx, cid in enumerate(dense_ids)}
    lexical_ranks = {cid: idx + 1 for idx, cid in enumerate(lexical_ids)}

    fused = reciprocal_rank_fusion([dense_ids, lexical_ids], k=req.rrf_k)
    if not fused:
        return RetrieveResponse(
            chunks=[],
            markdown=None if not req.return_markdown else f"_(нет попаданий по запросу)_",
        )

    sorted_by_fused = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    bfs_seeds = [uuid.UUID(cid) for cid, _ in sorted_by_fused[:128]]

    reachable = await store.expand_reachable_nodes(req.tenant_id, bfs_seeds, max_hops=req.max_hops)
    reachable_list = list(reachable)
    edge_records = await store.load_internal_edge_records(req.tenant_id, reachable_list)
    edge_rows = [
        (str(e.id), str(e.source_chunk_id), str(e.target_chunk_id), float(e.weight), e.directed)
        for e in edge_records
    ]
    adj = build_adjacency(edge_rows)
    seed_scores = {cid: float(fused[cid]) for cid in fused}

    chunk_all = await store.fetch_chunks_by_ids(req.tenant_id, reachable_list)
    emb = {cid: list(c.embedding) for cid, c in chunk_all.items() if c.embedding is not None}
    chunk_meta = {cid: dict(c.metadata_json) for cid, c in chunk_all.items()}

    neigh_map: Dict[str, set[str]] = {cid: set() for cid in chunk_all}
    for e in edge_records:
        sa, sb = str(e.source_chunk_id), str(e.target_chunk_id)
        if sa in neigh_map:
            neigh_map[sa].add(sb)
        if sb in neigh_map:
            neigh_map[sb].add(sa)
    neigh_list = {k: sorted(v) for k, v in neigh_map.items()}

    bearings = _compute_bearings(edge_records, neigh_list, chunk_meta, emb)

    if req.gaze_aware:
        adj_gaze = build_adjacency_gaze(edge_rows, bearings)
        scores = propagate_relevance_gaze(
            seed_scores,
            adj_gaze,
            max_hops=req.max_hops,
            hop_decay=req.hop_decay,
            suppress_behind=req.suppress_behind,
            include_opposition=req.include_opposition,
        )
    else:
        scores = propagate_relevance(
            seed_scores,
            adj,
            max_hops=req.max_hops,
            hop_decay=req.hop_decay,
        )
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[: req.max_nodes]

    chosen_ids = [uuid.UUID(cid) for cid, _ in ranked]
    chunk_map = await store.fetch_chunks_by_ids(req.tenant_id, chosen_ids)

    seed_str = {str(s) for s in bfs_seeds}
    hop_dist = _bfs_hop_distances(seed_str, adj)

    if req.gaze_anchor_chunk_id is not None:
        anchor_str = str(req.gaze_anchor_chunk_id)
    else:
        anchor_str = str(bfs_seeds[0]) if bfs_seeds else None

    chunks_out: list[RetrievedChunk] = []
    for cid_str, sc in ranked:
        c = chunk_map.get(cid_str)
        if c is None:
            continue
        meta_out = dict(c.metadata_json)
        if req.gaze_aware and anchor_str and cid_str != anchor_str:
            br = bearings.get((anchor_str, cid_str))
            if br is not None:
                meta_out["wisedb_bearing_from_anchor_rad"] = br
                meta_out["wisedb_gaze_sector"] = _sector_label(br)
        chunks_out.append(
            RetrievedChunk(
                id=c.id,
                title=c.title,
                body=c.body,
                score=float(sc),
                hop_distance=int(hop_dist.get(cid_str, 0)),
                dense_rank=dense_ranks.get(cid_str),
                lexical_rank=lexical_ranks.get(cid_str),
                metadata=meta_out,
                tags=list(c.tags),
            )
        )

    md: Optional[str] = None
    if req.return_markdown:
        chosen_set = {cid for cid, _ in ranked if cid in chunk_map}
        internal_pick = [
            e
            for e in edge_records
            if str(e.source_chunk_id) in chosen_set and str(e.target_chunk_id) in chosen_set
        ]
        bear_pick = {
            k: v
            for k, v in bearings.items()
            if k[0] in chosen_set and k[1] in chosen_set
        }
        md = _markdown_bundle(
            req.query,
            ranked,
            chunk_map,
            hop_dist,
            internal_pick,
            chosen_set,
            bear_pick,
            anchor_str if req.gaze_aware else None,
        )

    chosen_set_all = {cid for cid, _ in ranked if cid in chunk_map}
    if reinforce_sleep_edges and chosen_set_all:
        internal_reinforce = [
            e
            for e in edge_records
            if str(e.source_chunk_id) in chosen_set_all
            and str(e.target_chunk_id) in chosen_set_all
            and e.deleted_at is None
        ]
        if internal_reinforce:
            await store.reinforce_edges_internal(req.tenant_id, internal_reinforce)

    return RetrieveResponse(chunks=chunks_out, markdown=md)


def resolve_query_embedding(req: RetrieveRequest) -> list[float]:
    if req.query_embedding is not None:
        if len(req.query_embedding) != EMBEDDING_DIM:
            raise ValueError(f"query_embedding должен быть длины {EMBEDDING_DIM}")
        return req.query_embedding
    settings = get_settings()
    provider = get_embedding_provider(settings.embedding_model)
    if provider is None:
        raise ValueError(
            "Передайте query_embedding или задайте WISEDB_EMBEDDING_MODEL для кодирования на сервере."
        )
    if provider.dimension != EMBEDDING_DIM:
        raise RuntimeError(
            f"Размерность модели эмбеддингов {provider.dimension} ≠ {EMBEDDING_DIM}; "
            "переиндексируйте под ту же модель или смените EMBEDDING_DIM и мигрируйте данные."
        )
    vecs = provider.encode([req.query])
    return vecs[0]
