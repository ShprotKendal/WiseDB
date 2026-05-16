"""Зависимости FastAPI (аутентификация БД и админ-ключ)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from wisedb.server.config import Settings, get_settings
from wisedb.server.database_manager import DatabaseAuthError, DatabaseManager
from wisedb.server.memory_engine import NeuralContextDB


def get_db_manager(request: Request) -> DatabaseManager:
    return request.app.state.db_manager


ManagerDep = Annotated[DatabaseManager, Depends(get_db_manager)]


async def verify_admin_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    req_key = _extract_bearer_or_header(request, x_api_key)
    expected = settings.api_key
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WISEDB_API_KEY не задан (нужен для администрирования баз /v1/databases/*)",
        )
    if req_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="неверный или отсутствующий API-ключ",
        )


def _extract_bearer_or_header(request: Request, x_api_key: str | None) -> str | None:
    if x_api_key is not None:
        return x_api_key
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


async def get_authenticated_store(
    request: Request,
    manager: ManagerDep,
    x_wisedb_database: str | None = Header(default=None, alias="X-WiseDB-Database"),
    x_wisedb_password: str | None = Header(default=None, alias="X-WiseDB-Password"),
) -> NeuralContextDB:
    """
    Агент: передавайте идентификатор базы и пароль.
    Либо заголовки X-WiseDB-Database / X-WiseDB-Password,
    либо Authorization: Bearer <db_id>:<password>.
    """
    db_id = x_wisedb_database
    password = x_wisedb_password
    if db_id is None or password is None:
        auth = request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            if ":" in token:
                db_id, password = token.split(":", 1)
    if not db_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="нет заголовка X-WiseDB-Database (или Authorization: Bearer <база>:<пароль>)",
        )
    if password is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="нет заголовка X-WiseDB-Password",
        )
    try:
        return await manager.authenticate(db_id, password)
    except DatabaseAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


StoreDep = Annotated[NeuralContextDB, Depends(get_authenticated_store)]
