"""REST access to database nodes and folder layout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response

from wisedb.server.deps import require_bearer
from wisedb.server.ops import (
    OpError,
    database_tree_nested,
    folders_create,
    folders_delete,
    folders_move,
    node_optic_bytes_get,
    node_optic_bytes_put,
    node_optic_delete,
    node_optic_get,
    node_optic_put,
    nodes_content,
    nodes_create,
    nodes_delete,
    nodes_get,
    nodes_list,
    nodes_search,
    nodes_move,
    nodes_patch,
    nodes_put,
)
from wisedb.server.schemas import (
    FolderMoveRequest,
    FolderPathRequest,
    NodeCreateRequest,
    NodeMoveRequest,
    NodePatchRequest,
    NodePutRequest,
    OpticTextPutRequest,
    OpticTextResponse,
    StatusResponse,
)
from wisedb.server.users import User

nodes_router = APIRouter(prefix="/databases", tags=["Nodes"], dependencies=[Depends(require_bearer)])


def _http_error(exc: OpError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@nodes_router.get(
    "/{database}/tree",
    summary="Nested folder and page tree",
    description=(
        "Returns the database layout as nested folders and page catalog entries. "
        "Flat layout remains available via WQL `SELECT * FROM layout`. Requires `read`."
    ),
)
def get_database_tree(database: str, user: User = Depends(require_bearer)) -> dict:
    try:
        return database_tree_nested(user, database)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.get(
    "/{database}/nodes",
    summary="List node catalog",
    description="Page metadata without bodies (`wiki.list()`). Requires `read`.",
)
def list_nodes(database: str, user: User = Depends(require_bearer)) -> dict:
    try:
        return nodes_list(user, database)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.get(
    "/{database}/search",
    summary="Search nodes by keyword",
    description=(
        "Full-text search over title, summary, and body (whole words, same BM25 engine as WQL SEARCH). "
        "Returns matching node objects including body text. Requires `read`."
    ),
)
def search_nodes(
    database: str,
    q: str = Query(..., description="Search terms (whole-word match)."),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of nodes to return."),
    user: User = Depends(require_bearer),
) -> dict:
    try:
        return nodes_search(user, database, q, limit=limit)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.post(
    "/{database}/nodes",
    summary="Create a node",
    description="Stores a markdown page or media file. Requires `write`.",
)
def create_node(database: str, body: NodeCreateRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return nodes_create(
            user,
            database,
            slug=body.slug,
            markdown=body.markdown,
            rel_path=body.rel_path,
            file_base64=body.file_base64,
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.get(
    "/{database}/nodes/{slug}/content",
    summary="Raw node markdown",
    description="Full stored document including frontmatter. Requires `read`.",
    response_class=PlainTextResponse,
)
def get_node_content(database: str, slug: str, user: User = Depends(require_bearer)) -> PlainTextResponse:
    try:
        text = nodes_content(user, database, slug)
    except OpError as exc:
        raise _http_error(exc) from exc
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@nodes_router.get(
    "/{database}/nodes/{slug}/optics/{optic}",
    response_model=OpticTextResponse,
    summary="Read optic text for a node",
    description="Returns UTF-8 text stored in a custom optical layer. Requires `view` on the optic.",
)
def get_node_optic(database: str, slug: str, optic: str, user: User = Depends(require_bearer)) -> OpticTextResponse:
    try:
        return OpticTextResponse(**node_optic_get(user, database, slug, optic))
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.put(
    "/{database}/nodes/{slug}/optics/{optic}",
    response_model=OpticTextResponse,
    summary="Write optic text for a node",
    description="Stores UTF-8 text in a custom optical layer. Requires `edit` on the optic.",
)
def put_node_optic(
    database: str,
    slug: str,
    optic: str,
    body: OpticTextPutRequest,
    user: User = Depends(require_bearer),
) -> OpticTextResponse:
    try:
        return OpticTextResponse(
            **node_optic_put(
                user,
                database,
                slug,
                optic,
                text=body.text,
                path=body.path,
                start_line=body.start_line,
                end_line=body.end_line,
                base_sha256=body.base_sha256,
            )
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.get(
    "/{database}/nodes/{slug}/optics/{optic}/bytes",
    summary="Download binary optic payload",
    description="Returns stored bytes for image, video, or file optics. Requires `view` on the optic.",
)
def get_node_optic_bytes(
    database: str,
    slug: str,
    optic: str,
    user: User = Depends(require_bearer),
) -> Response:
    try:
        data, meta = node_optic_bytes_get(user, database, slug, optic)
    except OpError as exc:
        raise _http_error(exc) from exc
    return Response(
        content=data,
        media_type=meta.get("media_type") or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{meta.get("filename", slug)}"'},
    )


@nodes_router.put(
    "/{database}/nodes/{slug}/optics/{optic}/bytes",
    response_model=OpticTextResponse,
    summary="Upload binary optic payload",
    description="Stores raw bytes for image, video, or file optics. Requires `edit` on the optic.",
)
async def put_node_optic_bytes(
    database: str,
    slug: str,
    optic: str,
    request: Request,
    filename: str | None = Query(default=None),
    user: User = Depends(require_bearer),
) -> OpticTextResponse:
    data = await request.body()
    media_type = request.headers.get("content-type")
    try:
        return OpticTextResponse(
            **node_optic_bytes_put(
                user,
                database,
                slug,
                optic,
                data,
                filename=filename,
                media_type=media_type,
            )
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.delete(
    "/{database}/nodes/{slug}/optics/{optic}",
    response_model=StatusResponse,
    summary="Clear optic text for a node",
    description="Removes stored text for this slug in the optic. Requires `delete` on the optic.",
)
def delete_node_optic(database: str, slug: str, optic: str, user: User = Depends(require_bearer)) -> StatusResponse:
    try:
        return StatusResponse(**node_optic_delete(user, database, slug, optic))
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.get(
    "/{database}/nodes/{slug}",
    summary="Read a node",
    description="Structured node JSON from the engine. Use `neighbors=true` for one-hop links. Requires `read`.",
)
def get_node(
    database: str,
    slug: str,
    neighbors: bool = Query(False, description="Include direct link neighbors."),
    user: User = Depends(require_bearer),
) -> dict:
    try:
        return nodes_get(user, database, slug, neighbors=neighbors)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.patch(
    "/{database}/nodes/{slug}",
    summary="Update node fields",
    description="Patch `type`, `title`, `summary`, and/or `body` without replacing frontmatter wholesale. Requires `write`.",
)
def patch_node(database: str, slug: str, body: NodePatchRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return nodes_patch(
            user,
            database,
            slug,
            type=body.type,
            title=body.title,
            summary=body.summary,
            body=body.body,
        )
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.put(
    "/{database}/nodes/{slug}",
    summary="Replace node markdown",
    description="Full WikiLLM document replacement (`wiki.edit`). Requires `write`.",
)
def put_node(database: str, slug: str, body: NodePutRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return nodes_put(user, database, slug, body.markdown)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.delete(
    "/{database}/nodes/{slug}",
    response_model=StatusResponse,
    summary="Delete a node",
    description="Removes the page and on-disk files. Requires `write`.",
)
def delete_node(database: str, slug: str, user: User = Depends(require_bearer)) -> StatusResponse:
    try:
        return StatusResponse(**nodes_delete(user, database, slug))
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.post(
    "/{database}/folders",
    summary="Create folder",
    description="Creates an empty wiki-relative directory. Parent must exist. Requires `write`.",
)
def create_folder_route(database: str, body: FolderPathRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return folders_create(user, database, body.path)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.delete(
    "/{database}/folders",
    summary="Delete empty folder",
    description="Removes an empty folder. Requires `write`.",
)
def delete_folder_route(
    database: str,
    path: str = Query(..., min_length=1, description="Wiki-relative folder path."),
    user: User = Depends(require_bearer),
) -> dict:
    try:
        return folders_delete(user, database, path)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.post(
    "/{database}/nodes/{slug}/move",
    summary="Move page file",
    description="Changes on-disk location while keeping slug stable. Requires `write`.",
)
def move_node(database: str, slug: str, body: NodeMoveRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return nodes_move(user, database, slug, body.destination)
    except OpError as exc:
        raise _http_error(exc) from exc


@nodes_router.post(
    "/{database}/folders/move",
    summary="Move folder",
    description="Moves a folder tree on disk. Requires `write`.",
)
def move_folder_route(database: str, body: FolderMoveRequest, user: User = Depends(require_bearer)) -> dict:
    try:
        return folders_move(user, database, body.source, body.destination)
    except OpError as exc:
        raise _http_error(exc) from exc
