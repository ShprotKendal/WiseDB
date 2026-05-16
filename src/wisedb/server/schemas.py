"""Схемы Pydantic для HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChunkAttachmentUpsert(BaseModel):
    """Данные вложения (слот см. wisedb://… в WiseDoc)."""

    slot: str = Field(..., min_length=1, max_length=256)
    mime: str = Field(default="application/octet-stream", max_length=256)
    uri: Optional[str] = Field(default=None, description="Внешний URL, если байты не хранятся")
    data_base64: Optional[str] = Field(
        default=None,
        description="Base64 байтов (пропустите, если только uri)",
    )


class ChunkUpsertItem(BaseModel):
    id: Optional[uuid.UUID] = None
    external_key: Optional[str] = None
    title: str = ""
    body: str = ""
    body_format: Literal["plain", "wisedoc"] = "plain"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[list[float]] = None
    attachments: list[ChunkAttachmentUpsert] = Field(default_factory=list)


class ChunksUpsertRequest(BaseModel):
    tenant_id: str = "default"
    chunks: list[ChunkUpsertItem]


class EdgeUpsertItem(BaseModel):
    id: Optional[uuid.UUID] = None
    source_chunk_id: uuid.UUID
    target_chunk_id: uuid.UUID
    weight: float = Field(default=0.85, gt=0, le=1)
    directed: bool = False
    relation_type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EdgesUpsertRequest(BaseModel):
    tenant_id: str = "default"
    edges: list[EdgeUpsertItem]


class RetrieveRequest(BaseModel):
    tenant_id: str = "default"
    query: str
    query_embedding: Optional[list[float]] = None
    top_k_dense: int = 20
    top_k_lexical: int = 20
    max_hops: int = 5
    hop_decay: float = 0.9
    max_nodes: int = 48
    rrf_k: int = 60
    return_markdown: bool = True
    gaze_aware: bool = True
    suppress_behind: bool = True
    include_opposition: bool = False
    gaze_anchor_chunk_id: Optional[uuid.UUID] = Field(
        default=None,
        description="Якорь для секторов «взгляда» в markdown; по умолчанию — топ чанк после fusion.",
    )


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    body: str
    score: float
    hop_distance: int = 0
    dense_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class RetrieveResponse(BaseModel):
    chunks: list[RetrievedChunk]
    markdown: Optional[str] = None


class SleepRequest(BaseModel):
    tenant_id: str = "default"
    phase: Literal["decay_prune", "geometry_refresh", "llm_optimize"] = "decay_prune"
    dry_run: bool = False
    decay_factor: float = Field(default=0.95, gt=0, le=1)
    min_weight: float = Field(default=0.05, gt=0, le=1)
    delete_orphan_chunks: bool = False
    skip_reinforced_within_hours: float = Field(
        default=0.0,
        ge=0,
        description="Не ослаблять рёбра, усиленные retrieve за последние N часов.",
    )
    llm_api_key: Optional[str] = Field(
        default=None,
        repr=False,
        description="API-ключ OpenAI-совместимого API на запрос для llm_optimize; затирает env, не пишется в журнал.",
    )
    llm_base_url: Optional[str] = Field(
        default=None,
        description="Базовый URL OpenAI-совместимого API для llm_optimize (на запрос).",
    )
    llm_model: Optional[str] = Field(
        default=None,
        description="Имя модели для llm_optimize (на запрос).",
    )
    llm_max_chunks: int = Field(
        default=24,
        ge=1,
        le=200,
        description="Макс. число «живых» чанков, отправляемых в LLM.",
    )
    llm_max_actions: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Макс. число действий update/split в ответе LLM.",
    )


class SleepResponse(BaseModel):
    run_id: str
    status: str
    stats: dict[str, Any]
    error: Optional[str] = None


class DatabaseCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    password: str = Field(..., min_length=1, max_length=256)
    name: Optional[str] = Field(default=None, max_length=256)


class DatabasePasswordRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class DatabasePublic(BaseModel):
    id: str
    name: str
    created_at: datetime


class ChunkListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    external_key: Optional[str] = None


class AttachmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slot: str
    mime: str
    uri: Optional[str] = None
    byte_length: int = 0


class ChunkPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    tenant_id: str
    external_key: Optional[str]
    title: str
    body: str
    tags: list[str]
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    attachments: list[AttachmentPublic] = Field(default_factory=list)
    version: int
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
