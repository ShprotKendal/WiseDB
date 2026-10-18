"""Session login and logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from wisedb.server.deps import require_bearer
from wisedb.server.ops import OpError, login as op_login, logout as op_logout
from wisedb.server.schemas import LoginRequest, LoginResponse
from wisedb.server.users import User

auth_router = APIRouter(prefix="/auth", tags=["Auth"])


def _http_error(exc: OpError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@auth_router.post(
    "/login",
    response_model=LoginResponse,
    summary="Obtain a bearer session",
    description=(
        "Authenticates with username and password and returns a bearer token for "
        "subsequent requests. The special user `root` uses the server-configured "
        "`WISEDB_ROOT_PASSWORD`. No prior session is required."
    ),
    responses={
        401: {"description": "Invalid username or password."},
        422: {"description": "Malformed login payload."},
        503: {"description": "Session store is not configured on this server."},
    },
)
def login(body: LoginRequest) -> LoginResponse:
    try:
        payload = op_login(body.username, body.password)
    except OpError as exc:
        raise _http_error(exc) from exc
    return LoginResponse(**payload)


@auth_router.delete(
    "/session",
    summary="End the current session",
    description=(
        "Invalidates the bearer session tied to the authenticated user. "
        "Requires a valid `Authorization: Bearer` token."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Session cannot be revoked (for example, root)."},
        404: {"description": "Session record not found."},
    },
)
def logout(user: User = Depends(require_bearer)) -> dict[str, str]:
    try:
        return op_logout(user)
    except OpError as exc:
        raise _http_error(exc) from exc
