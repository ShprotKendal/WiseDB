"""Админ-маршруты: создание и управление логическими базами на сервере."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from wisedb.server.database_manager import DatabaseError
from wisedb.server.deps import ManagerDep, verify_admin_api_key
from wisedb.server.schemas import DatabaseCreateRequest, DatabasePasswordRequest, DatabasePublic

router = APIRouter(prefix="/databases", dependencies=[Depends(verify_admin_api_key)])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DatabasePublic)
async def create_database(body: DatabaseCreateRequest, manager: ManagerDep) -> DatabasePublic:
    try:
        entry = await manager.create_database(body.id, password=body.password, name=body.name)
    except DatabaseError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return DatabasePublic(id=entry.id, name=entry.name, created_at=entry.created_at)


@router.get("", response_model=list[DatabasePublic])
async def list_databases_route(manager: ManagerDep) -> list[DatabasePublic]:
    entries = await manager.list_databases()
    return [DatabasePublic(id=e.id, name=e.name, created_at=e.created_at) for e in entries]


@router.delete("/{db_id}", status_code=status.HTTP_200_OK)
async def delete_database_route(db_id: str, manager: ManagerDep) -> dict[str, bool]:
    try:
        ok = await manager.delete_database(db_id)
    except DatabaseError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="база не найдена")
    return {"ok": True}


@router.post("/{db_id}/password", status_code=status.HTTP_200_OK)
async def rotate_database_password(
    db_id: str,
    body: DatabasePasswordRequest,
    manager: ManagerDep,
) -> dict[str, bool]:
    try:
        ok = await manager.rotate_password(db_id, new_password=body.password)
    except DatabaseError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="база не найдена")
    await manager.evict_store(db_id)
    return {"ok": True}
