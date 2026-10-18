"""Database lifecycle and WQL execution."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from wisedb.server.deps import require_bearer, require_root
from wisedb.server.engine.layout import LayoutError, media_path
from wisedb.server.ops import (
    OpError,
    database_settings_get,
    database_settings_patch,
    databases_create,
    databases_drop,
    databases_list,
    optics_create,
    optics_drop,
    optics_list,
    optics_patch,
    wql_batch,
    wql_execute,
    wql_parse,
)
from wisedb.server.deps import get_database_wiki, validate_database_name
from wisedb.server.catalog import get_catalog
from wisedb.server.permissions import effective_optic_privileges, privilege_allows
from wisedb.server.deps import database_privileges
from wisedb.server.schemas import (
    CreateDatabaseRequest,
    CreateDatabaseResponse,
    DatabaseListResponse,
    DatabaseSettingsPatchRequest,
    DatabaseSettingsResponse,
    OpticCreateRequest,
    OpticListResponse,
    OpticPatchRequest,
    OpticRecordResponse,
    StatusResponse,
    WqlBatchRequest,
    WqlExecuteRequest,
    WqlParseResponse,
    wql_execute_response_examples,
)
from wisedb.server.users import User

databases_router = APIRouter(prefix="/databases", tags=["Databases"], dependencies=[Depends(require_bearer)])


def _http_error(exc: OpError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@databases_router.get(
    "",
    response_model=DatabaseListResponse,
    summary="List accessible databases",
    description=(
        "Returns database names the caller may use. Root sees every registered database; "
        "other users see only databases where they have at least one grant. "
        "Requires a valid bearer session."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
    },
)
def list_databases(user: User = Depends(require_bearer)) -> DatabaseListResponse:
    return DatabaseListResponse(**databases_list(user))


@databases_router.post(
    "",
    dependencies=[Depends(require_root)],
    response_model=CreateDatabaseResponse,
    summary="Register a new database",
    description=(
        "Adds a database to the catalog and creates its on-disk directory under "
        "`{data_dir}/databases/{name}`. Root only."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
        409: {"description": "A database with this name already exists."},
        422: {"description": "Invalid database name."},
    },
)
def create_database(body: CreateDatabaseRequest, _actor: User = Depends(require_root)) -> CreateDatabaseResponse:
    try:
        return CreateDatabaseResponse(**databases_create(body.name))
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.delete(
    "/{database}",
    response_model=StatusResponse,
    summary="Drop a database",
    description=(
        "Removes the database from the catalog, closes any open engine handle, and "
        "deletes its storage directory. Root or a user with the `admin` privilege on "
        "that database."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "`admin` privilege required."},
        404: {"description": "Database not found."},
        422: {"description": "Invalid database name."},
    },
)
def delete_database(database: str, user: User = Depends(require_bearer)) -> StatusResponse:
    try:
        return StatusResponse(**databases_drop(user, database))
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.get(
    "/{database}/settings",
    response_model=DatabaseSettingsResponse,
    summary="Read database settings",
    description="Returns per-database options such as semantic `{{title}}` linking. Requires `read`.",
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "`read` privilege required."},
        404: {"description": "Database not found."},
    },
)
def get_database_settings(database: str, user: User = Depends(require_bearer)) -> DatabaseSettingsResponse:
    try:
        return DatabaseSettingsResponse(**database_settings_get(user, database))
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.patch(
    "/{database}/settings",
    response_model=DatabaseSettingsResponse,
    summary="Update database settings",
    description=(
        "Changes semantics (`_wisedb/settings.json`) and/or local `code_root` "
        "(`_wisedb/workspace.json`) for span optics. Requires `admin` on the database."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "`admin` privilege required."},
        404: {"description": "Database not found."},
    },
)
def patch_database_settings(
    database: str,
    body: DatabaseSettingsPatchRequest,
    user: User = Depends(require_bearer),
) -> DatabaseSettingsResponse:
    try:
        return DatabaseSettingsResponse(
            **database_settings_patch(
                user,
                database,
                semantics=body.semantics,
                code_root=body.code_root,
                patch_code_root="code_root" in body.model_fields_set,
            )
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.get(
    "/{database}/optics",
    response_model=OpticListResponse,
    summary="List optical layers",
    description=(
        "Returns the built-in `content` optic and custom optics the caller may view, "
        "each with effective `view`/`edit`/`delete` privileges for this session."
    ),
)
def list_optics(database: str, user: User = Depends(require_bearer)) -> OpticListResponse:
    try:
        payload = optics_list(user, database)
        return OpticListResponse(
            optics=[OpticRecordResponse(**item) for item in payload["optics"]],
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.post(
    "/{database}/optics",
    response_model=OpticRecordResponse,
    summary="Create a custom optic",
    description="Registers a new optical layer. Requires `admin` on the database.",
)
def create_optic_route(
    database: str,
    body: OpticCreateRequest,
    user: User = Depends(require_bearer),
) -> OpticRecordResponse:
    try:
        record = optics_create(
            user,
            database,
            body.name,
            description=body.description,
            kind=body.kind,
        )
        privs = sorted(effective_optic_privileges(user, validate_database_name(database), record["name"]))
        return OpticRecordResponse(
            name=record["name"],
            description=record.get("description", ""),
            kind=record.get("kind", "markdown"),
            privileges=privs,
            builtin=False,
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.patch(
    "/{database}/optics/{optic}",
    response_model=OpticRecordResponse,
    summary="Update optic description",
    description="Changes metadata for a custom optic. Requires `admin`.",
)
def patch_optic_route(
    database: str,
    optic: str,
    body: OpticPatchRequest,
    user: User = Depends(require_bearer),
) -> OpticRecordResponse:
    try:
        record = optics_patch(user, database, optic, body.description)
        privs = sorted(effective_optic_privileges(user, validate_database_name(database), record["name"]))
        return OpticRecordResponse(
            name=record["name"],
            description=record.get("description", ""),
            kind=record.get("kind", "markdown"),
            privileges=privs,
            builtin=False,
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.delete(
    "/{database}/optics/{optic}",
    response_model=StatusResponse,
    summary="Drop a custom optic",
    description="Removes the optic, all stored texts, and per-user optic grant overrides. Requires `admin`.",
)
def delete_optic_route(database: str, optic: str, user: User = Depends(require_bearer)) -> StatusResponse:
    try:
        return StatusResponse(**optics_drop(user, database, optic))
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.post(
    "/{database}/execute",
    summary="Run one WQL statement",
    description=(
        "Primary data path: pages, edges, search, layout, and `SCHEMA` all go through "
        "WQL. Required privilege follows the statement (see the Databases tag description). "
        "The caller must have at least one grant on the database. Response JSON shape "
        "depends on the query. `WS[]` streaming is only available on WebSocket `/ws`."
    ),
    responses=wql_execute_response_examples(),
)
def execute_wql(
    database: str,
    body: WqlExecuteRequest,
    user: User = Depends(require_bearer),
) -> dict:
    try:
        return wql_execute(user, database, body.query)
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.post(
    "/{database}/parse",
    response_model=WqlParseResponse,
    summary="Check a WQL statement",
    description=(
        "Parses one WQL statement and reports its kind and required privilege. "
        "Does not read or change database contents. `allowed` is false when the "
        "caller can see the database but lacks the privilege this statement needs."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "No access to this database."},
        404: {"description": "Database not found."},
        422: {"description": "Invalid database name or WQL syntax/semantics."},
    },
)
def parse_wql(
    database: str,
    body: WqlExecuteRequest,
    user: User = Depends(require_bearer),
) -> WqlParseResponse:
    try:
        return WqlParseResponse(**wql_parse(user, database, body.query))
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.post(
    "/{database}/batch",
    summary="Run several WQL statements",
    description=(
        "Runs up to 32 WQL statements in order on one database. Syntax and privileges "
        "are checked for every statement before the first one runs. A later runtime "
        "error leaves earlier statements in the batch applied. Response is "
        "`{\"results\": [...]}` with one JSON value per statement, in the same shapes "
        "as `POST /databases/{name}/execute`."
    ),
    responses={
        200: {
            "description": "One result object per statement, in request order.",
            "content": {
                "application/json": {
                    "example": {"results": [{"schema": "# WiseDB WQL\n..."}, {"status": "ok"}]},
                }
            },
        },
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "No database access, or a statement needs a privilege the caller lacks."},
        404: {"description": "Database not found."},
        422: {"description": "Invalid database name, or a statement failed to parse or run."},
    },
)
def execute_wql_batch(
    database: str,
    body: WqlBatchRequest,
    user: User = Depends(require_bearer),
) -> dict:
    try:
        return wql_batch(user, database, body.queries)
    except OpError as exc:
        raise _http_error(exc) from exc


@databases_router.get(
    "/{database}/nodes/{slug}/payload",
    summary="Download binary node payload",
    description=(
        "Streams the on-disk bytes for a media-type page (image, file attachment, etc.). "
        "Document text and graph data should be read via WQL instead. Requires `read` "
        "privilege on the database (see Databases tag description)."
    ),
    responses={
        200: {
            "description": "Raw file bytes with an appropriate `Content-Type`.",
            "content": {"application/octet-stream": {}},
        },
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "`read` privilege required."},
        404: {"description": "Database or node not found."},
        422: {"description": "Invalid database name or node is not a media payload."},
    },
)
def download_node_payload(database: str, slug: str, user: User = Depends(require_bearer)) -> FileResponse:
    name = validate_database_name(database)
    catalog = get_catalog()
    if not catalog.database_exists(name):
        raise HTTPException(status_code=404, detail="database not found")
    privs = database_privileges(user, name)
    if not privilege_allows(privs, "read"):
        raise HTTPException(status_code=403, detail="read privilege required")
    wiki = get_database_wiki(name)
    try:
        page, path = media_path(wiki, slug)
    except LayoutError as exc:
        message = str(exc)
        if "not found" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=422, detail=message) from exc
    return FileResponse(
        path,
        media_type=page.get("mime") or "application/octet-stream",
        filename=Path(page["rel_path"]).name,
    )
