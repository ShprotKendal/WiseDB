"""Версионированные HTTP-маршруты API."""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from wisedb.server import metrics
from wisedb.server.deps import StoreDep
from wisedb.server.embeddings import get_embedding_provider
from wisedb.server.schemas import (
    ChunkListItem,
    ChunkPublic,
    ChunksUpsertRequest,
    EdgesUpsertRequest,
    RetrieveRequest,
    RetrieveResponse,
    SleepRequest,
    SleepResponse,
)
from wisedb.server.retrieve_service import resolve_query_embedding, retrieve
from wisedb.wisedoc import parse_wisedoc, text_for_embedding

router = APIRouter()


@router.post("/chunks/upsert", status_code=status.HTTP_200_OK)
async def chunks_upsert(
    request: Request,
    body: ChunksUpsertRequest,
    store: StoreDep,
) -> dict[str, list[str]]:
    provider = get_embedding_provider(request.app.state.settings.embedding_model)
    ids: list[str] = []
    for item in body.chunks:
        try:
            emb = item.embedding
            if emb is None and provider is not None:
                if item.body_format == "wisedoc":
                    fm, md_body = parse_wisedoc(item.body)
                    raw = text_for_embedding(title=item.title, body=md_body, frontmatter=fm)
                else:
                    raw = f"{item.title}\n{item.body}".strip()
                emb = provider.encode([raw])[0]
            item2 = item.model_copy(update={"embedding": emb})
            chunk = await store.upsert_chunk(body.tenant_id, item2)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
        ids.append(str(chunk.id))
    metrics.UPSERT_CHUNKS_TOTAL.labels(tenant_id=body.tenant_id).inc(len(body.chunks))
    return {"ids": ids}


@router.post("/edges/upsert", status_code=status.HTTP_200_OK)
async def edges_upsert(body: EdgesUpsertRequest, store: StoreDep) -> dict[str, list[str]]:
    ids: list[str] = []
    for item in body.edges:
        edge = await store.upsert_edge(body.tenant_id, item)
        ids.append(str(edge.id))
    metrics.UPSERT_EDGES_TOTAL.labels(tenant_id=body.tenant_id).inc(len(body.edges))
    return {"ids": ids}


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve_route(request: Request, body: RetrieveRequest, store: StoreDep) -> RetrieveResponse:
    t0 = time.perf_counter()
    try:
        qe = resolve_query_embedding(body)
        result = await retrieve(
            store,
            body,
            query_embedding=qe,
            reinforce_sleep_edges=request.app.state.settings.sleep_reinforce_on_retrieve,
        )
        return result
    except ValueError as e:
        metrics.RETRIEVE_ERRORS.inc()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception:
        metrics.RETRIEVE_ERRORS.inc()
        raise
    finally:
        metrics.RETRIEVE_SECONDS.observe(time.perf_counter() - t0)


@router.post("/sleep", response_model=SleepResponse, status_code=status.HTTP_200_OK)
async def sleep_route(request: Request, body: SleepRequest, store: StoreDep) -> SleepResponse:
    """Обслуживание: усечение/обновление геометрии рёбер или LLM-оптимизация чанков."""
    settings = request.app.state.settings
    provider = get_embedding_provider(settings.embedding_model)
    try:
        out = await store.run_sleep(
            body.tenant_id,
            phase=body.phase,
            dry_run=body.dry_run,
            decay_factor=body.decay_factor,
            min_weight=body.min_weight,
            delete_orphan_chunks=body.delete_orphan_chunks,
            skip_reinforced_within_hours=body.skip_reinforced_within_hours,
            llm_api_key=body.llm_api_key or settings.sleep_llm_api_key,
            llm_base_url=body.llm_base_url or settings.sleep_llm_base_url,
            llm_model=body.llm_model or settings.sleep_llm_model,
            llm_max_chunks=body.llm_max_chunks,
            llm_max_actions=body.llm_max_actions,
            embedding_fn=provider.encode if provider is not None else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return SleepResponse(
        run_id=out["run_id"],
        status=str(out["status"]),
        stats=dict(out["stats"]),
        error=out.get("error"),
    )


@router.get("/chunks/lookup", response_model=ChunkPublic)
async def lookup_chunk_route(
    store: StoreDep,
    external_key: str = Query(..., min_length=1),
    tenant_id: str = Query(default="default"),
) -> ChunkPublic:
    row = await store.get_chunk_by_external_key(tenant_id, external_key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="чанк не найден")
    return row.to_public()


@router.get("/chunks", response_model=list[ChunkListItem])
async def list_chunks_route(
    store: StoreDep,
    tenant_id: str = Query(default="default"),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[ChunkListItem]:
    rows = await store.list_chunks_preview(tenant_id, limit=limit)
    return [ChunkListItem(id=c.id, title=c.title, external_key=c.external_key) for c in rows]


@router.delete("/chunks/{chunk_id}", status_code=status.HTTP_200_OK)
async def delete_chunk_route(
    chunk_id: uuid.UUID,
    store: StoreDep,
    tenant_id: str = Query(default="default"),
) -> dict[str, bool]:
    ok = await store.soft_delete_chunk(tenant_id, chunk_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="чанк не найден")
    return {"ok": True}


@router.get("/chunks/{chunk_id}", response_model=ChunkPublic)
async def get_chunk_route(
    chunk_id: uuid.UUID,
    store: StoreDep,
    tenant_id: str = Query(default="default"),
) -> ChunkPublic:
    row = await store.get_chunk(tenant_id, chunk_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="чанк не найден")
    return row.to_public()


@router.get("/chunks/{chunk_id}/attachments/{slot}")
async def get_chunk_attachment_route(
    chunk_id: uuid.UUID,
    slot: str,
    store: StoreDep,
    tenant_id: str = Query(default="default"),
) -> Response:
    got = await store.read_attachment_bytes(tenant_id, chunk_id, slot)
    if got is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="вложение не найдено или нет бинарных данных в ответе",
        )
    blob, mime = got
    return Response(content=blob, media_type=mime)
