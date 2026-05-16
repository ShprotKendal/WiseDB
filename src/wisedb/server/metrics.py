"""Метрики Prometheus (эндпоинт /metrics)."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

RETRIEVE_SECONDS = Histogram(
    "wisedb_retrieve_seconds",
    "Время обработки POST /v1/retrieve",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
UPSERT_CHUNKS_TOTAL = Counter("wisedb_upsert_chunks_total", "Чанков upsert", labelnames=("tenant_id",))
UPSERT_EDGES_TOTAL = Counter("wisedb_upsert_edges_total", "Рёбер upsert", labelnames=("tenant_id",))
RETRIEVE_ERRORS = Counter("wisedb_retrieve_errors_total", "Ошибки retrieve")


def metrics_payload() -> tuple[bytes, str]:
    data = generate_latest()
    return data, CONTENT_TYPE_LATEST
