"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from wisedb import __version__
from wisedb.server.catalog import get_catalog, reset_catalog
from wisedb.server.deps import close_wiki
from wisedb.server.routes import auth_router, databases_router, nodes_router, users_router
from wisedb.server.ws import ws_router
from wisedb.server.sessions import get_session_store, reset_session_store

_OPENAPI_DESCRIPTION = (
    "WiseDB database server: one process hosts many named databases under "
    "`{data_dir}/databases/`. Users, grants, and the database registry live in "
    "`{data_dir}/_catalog/`. "
    "When `WISEDB_SYSTEM_WIKI` is enabled (default), the read-only `_wiki` database "
    "ships built-in usage docs (re-seeded on startup; mutations are rejected). "
    "Document data is available through WQL (`POST /databases/{name}/execute`, "
    "`POST /databases/{name}/batch`) and REST nodes/layout (`GET /databases/{name}/tree`, "
    "`/databases/{name}/nodes`, …). "
    "`POST /databases/{name}/parse` checks WQL without running it. "
    "Binary media bytes for a node are served by "
    "`GET /databases/{name}/nodes/{slug}/payload`. "
    "WebSocket `WS /ws` mirrors auth, users, databases, WQL execute/parse, and "
    "payload; use `query WS[inner]` on the socket to stream per-row results."
)

_OPENAPI_TAGS = [
    {
        "name": "Health",
        "description": "Process liveness and build version (no authentication).",
    },
    {
        "name": "Auth",
        "description": "Create and revoke bearer sessions from username and password.",
    },
    {
        "name": "Databases",
        "description": (
            "Named database lifecycle and data access. Privileges per database are "
            "`read`, `write`, and `admin` (admin implies read and write). Root has "
            "full access to every database. WQL privilege rules: `SELECT`, `SEARCH`, "
            "and `SCHEMA` require read; `INSERT`, `UPDATE`, `DELETE`, and layout "
            "statements require write. `parse` reports the required privilege without "
            "executing; `batch` runs several statements in order."
        ),
    },
    {
        "name": "Users",
        "description": (
            "Catalog users and per-database grants. Root manages users and grants; "
            "non-root users can read their own profile and change their own password."
        ),
    },
    {
        "name": "Nodes",
        "description": (
            "REST CRUD for wiki nodes, raw markdown content, nested folder tree, and "
            "layout moves. Requires `read` or `write` on the database. Search, graph "
            "walks, and `SCHEMA` remain WQL-only."
        ),
    },
]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        from wisedb.server.config import get_settings

        settings = get_settings()
        if settings.host != "127.0.0.1" and not settings.root_password:
            raise RuntimeError("refusing non-loopback server startup without WISEDB_ROOT_PASSWORD")
        reset_session_store()
        reset_catalog()
        get_catalog().open(settings.catalog_dir)
        get_session_store().configure(settings.root_password, get_catalog())
        from wisedb.server.system_wiki import sync_system_wiki

        sync_system_wiki()
        yield
    finally:
        close_wiki()
        reset_session_store()
        reset_catalog()


app = FastAPI(
    title="WiseDB",
    version=__version__,
    description=_OPENAPI_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(nodes_router)
app.include_router(databases_router)
app.include_router(ws_router)


@app.get(
    "/healthz",
    tags=["Health"],
    summary="Liveness probe",
    description=(
        "Returns server status and the installed WiseDB version. "
        "Use for load balancers and orchestration health checks. No authentication."
    ),
)
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
