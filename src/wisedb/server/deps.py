"""FastAPI dependencies."""

from __future__ import annotations

import re
import threading

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from wisedb.memory import Wiki
from wisedb.server.catalog import get_catalog
from wisedb.server.config import SYSTEM_WIKI_DATABASE, get_settings
from wisedb.server.permissions import privilege_allows
from wisedb.server.sessions import get_session_store
from wisedb.server.users import User

_DB_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

_wiki_lock = threading.RLock()
_wiki_instances: dict[str, Wiki] = {}


def validate_database_name(name: str) -> str:
    name = name.strip()
    if name == SYSTEM_WIKI_DATABASE:
        return name
    if not _DB_NAME.fullmatch(name):
        raise HTTPException(status_code=422, detail="invalid database name")
    return name


def is_reserved_database_name(name: str) -> bool:
    return name == SYSTEM_WIKI_DATABASE


def system_wiki_enabled() -> bool:
    return get_settings().system_wiki


def get_database_wiki(name: str) -> Wiki:
    db_name = validate_database_name(name)
    catalog = get_catalog()
    if not catalog.database_exists(db_name):
        raise HTTPException(status_code=404, detail="database not found")
    settings = get_settings()
    root = settings.database_root(db_name)
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve(strict=True)
    with _wiki_lock:
        cached = _wiki_instances.get(db_name)
        if cached is not None and cached.root.resolve(strict=True) == resolved:
            return cached
        if cached is not None:
            cached.close()
        wiki = Wiki(resolved)
        _wiki_instances[db_name] = wiki
        return wiki


def close_wiki() -> None:
    with _wiki_lock:
        for wiki in _wiki_instances.values():
            wiki.close()
        _wiki_instances.clear()


_bearer_scheme = HTTPBearer(
    auto_error=False,
    description=(
        "Session token from `POST /auth/login`. Send as "
        "`Authorization: Bearer <token>` on protected routes."
    ),
)


def require_bearer(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> User:
    store = get_session_store()
    if not store.is_configured:
        raise HTTPException(status_code=503, detail="session authentication is not configured")
    token = credentials.credentials if credentials is not None else None
    user = store.authenticate(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    if user.disabled:
        raise HTTPException(status_code=401, detail="user is disabled")
    return user


def require_root(user: User = Security(require_bearer)) -> User:
    if not user.is_root:
        raise HTTPException(status_code=403, detail="root required")
    return user


def database_privileges(user: User, database: str) -> frozenset[str]:
    if database == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
        return frozenset({"read"})
    if user.is_root:
        return frozenset({"read", "write", "admin"})
    return get_catalog().privileges_on(user.id, database)
