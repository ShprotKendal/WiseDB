"""User administration and database grants."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from wisedb.server.deps import require_bearer, require_root
from wisedb.server.ops import (
    OpError,
    users_create,
    users_delete,
    users_grant,
    users_list,
    users_me,
    users_optic_grant,
    users_optic_revoke,
    users_password,
    users_revoke,
)
from wisedb.server.schemas import (
    CreateUserRequest,
    GrantRequest,
    GrantSetResponse,
    OpticGrantRequest,
    OpticGrantSetResponse,
    SetPasswordRequest,
    StatusResponse,
    UserResponse,
)
from wisedb.server.users import User

users_router = APIRouter(prefix="/users", tags=["Users"], dependencies=[Depends(require_bearer)])


def _http_error(exc: OpError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@users_router.get(
    "/me",
    summary="Current session profile",
    description=(
        "Returns the authenticated user's public fields (`id`, `username`, `is_root`, etc.). "
        "Any valid bearer session."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
    },
)
def me(user: User = Depends(require_bearer)) -> dict[str, str | bool]:
    return users_me(user)


@users_router.get(
    "",
    dependencies=[Depends(require_root)],
    response_model=list[UserResponse],
    summary="List catalog users",
    description=("Returns all users with their per-database grants. Root only."),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
    },
)
def list_users(_actor: User = Depends(require_root)) -> list[UserResponse]:
    return [UserResponse(**row) for row in users_list(_actor)]


@users_router.post(
    "",
    dependencies=[Depends(require_root)],
    response_model=UserResponse,
    summary="Create a catalog user",
    description=(
        "Registers a new user that can log in via `POST /auth/login`. Grants are "
        "assigned separately. Root only."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
        422: {"description": "Invalid username or password."},
    },
)
def create_user(body: CreateUserRequest, _actor: User = Depends(require_root)) -> UserResponse:
    try:
        return UserResponse(**users_create(body.username, body.password))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.delete(
    "/{user_id}",
    dependencies=[Depends(require_root)],
    response_model=StatusResponse,
    summary="Delete a catalog user",
    description=("Removes the user and their grants from the catalog. Root only."),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
        404: {"description": "User not found."},
    },
)
def delete_user(user_id: str, _actor: User = Depends(require_root)) -> StatusResponse:
    try:
        return StatusResponse(**users_delete(user_id))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.patch(
    "/{user_id}/password",
    response_model=StatusResponse,
    summary="Change a user password",
    description=(
        "Updates the password for `user_id`. Non-root users may only change their own "
        "password. The `root` account password is set via server environment, not this "
        "endpoint."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Cannot change another user's password or change root here."},
        404: {"description": "User not found."},
        422: {"description": "Password does not meet requirements."},
    },
)
def set_password(
    user_id: str,
    body: SetPasswordRequest,
    actor: User = Depends(require_bearer),
) -> StatusResponse:
    try:
        return StatusResponse(**users_password(actor, user_id, body.password))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.put(
    "/{user_id}/grants/{database}",
    dependencies=[Depends(require_root)],
    response_model=GrantSetResponse,
    summary="Set database grants for a user",
    description=(
        "Replaces privileges for one user on one database. Valid values are `read`, "
        "`write`, and `admin` (see Databases tag description). Root only."
    ),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
        404: {"description": "User or database not found."},
        422: {"description": "Unknown privilege or invalid database name."},
    },
)
def set_grants(
    user_id: str,
    database: str,
    body: GrantRequest,
    _actor: User = Depends(require_root),
) -> GrantSetResponse:
    try:
        return GrantSetResponse(**users_grant(user_id, database, body.privileges))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.delete(
    "/{user_id}/grants/{database}",
    dependencies=[Depends(require_root)],
    response_model=StatusResponse,
    summary="Remove database grants for a user",
    description=("Clears all privileges for the user on the given database. Root only."),
    responses={
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "Caller is not root."},
        404: {"description": "User or database not found."},
    },
)
def clear_grants(user_id: str, database: str, _actor: User = Depends(require_root)) -> StatusResponse:
    try:
        return StatusResponse(**users_revoke(user_id, database))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.put(
    "/{user_id}/grants/{database}/optics/{optic}",
    dependencies=[Depends(require_root)],
    response_model=OpticGrantSetResponse,
    summary="Set per-optic grant override for a user",
    description=(
        "Replaces optic privileges for the user on the given database optic. "
        "An empty privilege list explicitly denies optic access. Root only."
    ),
)
def set_optic_grants(
    user_id: str,
    database: str,
    optic: str,
    body: OpticGrantRequest,
    _actor: User = Depends(require_root),
) -> OpticGrantSetResponse:
    try:
        return OpticGrantSetResponse(**users_optic_grant(user_id, database, optic, body.privileges))
    except OpError as exc:
        raise _http_error(exc) from exc


@users_router.delete(
    "/{user_id}/grants/{database}/optics/{optic}",
    dependencies=[Depends(require_root)],
    response_model=StatusResponse,
    summary="Remove per-optic grant override",
    description="Restores inherited optic privileges from database grants. Root only.",
)
def clear_optic_grants(
    user_id: str,
    database: str,
    optic: str,
    _actor: User = Depends(require_root),
) -> StatusResponse:
    try:
        return StatusResponse(**users_optic_revoke(user_id, database, optic))
    except OpError as exc:
        raise _http_error(exc) from exc
