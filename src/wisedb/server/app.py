"""Создание приложения FastAPI и жизненный цикл процесса."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response

from wisedb.server import metrics
from wisedb.server.config import get_settings
from wisedb.server.constants import EMBEDDING_DIM
from wisedb.server.database_manager import DatabaseManager
from wisedb.server.embeddings import get_embedding_provider
from wisedb.server.routes.databases import router as databases_router
from wisedb.server.routes.v1 import router as v1_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    logging.getLogger().setLevel(settings.log_level.upper())

    app.state.db_manager = DatabaseManager(
        data_dir=Path(settings.resolved_data_dir()),
        dim=EMBEDDING_DIM,
        max_elements=settings.hnsw_max_elements,
        hnsw_ef=settings.hnsw_ef,
        hnsw_m=settings.hnsw_m,
        bootstrap_default=settings.bootstrap_default_db,
        default_db_password=settings.default_db_password,
    )
    await app.state.db_manager.init()

    if settings.embedding_model:
        provider = get_embedding_provider(settings.embedding_model)
        if provider is not None and provider.dimension != EMBEDDING_DIM:
            raise RuntimeError(
                f"размерность эмбеддингов WISEDB_EMBEDDING_MODEL ({provider.dimension}) "
                f"не совпадает с EMBEDDING_DIM сервера ({EMBEDDING_DIM})"
            )

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="WiseDB",
        version="2.1.0",
        description=(
            "Контекстная БД: несколько логических баз на одном хосте, HNSW + лексика + граф в ОЗУ; "
            "на базу — отдельный SQLite."
        ),
        lifespan=lifespan,
    )
    app.include_router(databases_router, prefix="/v1")
    app.include_router(v1_router, prefix="/v1")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request) -> dict[str, str]:
        if not hasattr(request.app.state, "db_manager"):
            return {"status": "not_ready"}
        return {"status": "ready"}

    @app.get("/metrics")
    def prom_metrics() -> Response:
        data, ctype = metrics.metrics_payload()
        return Response(content=data, media_type=ctype)

    return app


app = create_app()
