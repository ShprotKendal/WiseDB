"""Несколько логических БД на одном хосте: каталог + ленивое подключение хранилища контекста."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Optional

from wisedb.server import catalog as cat
from wisedb.server.constants import EMBEDDING_DIM
from wisedb.server.memory_engine import NeuralContextDB
from wisedb.server.passwords import hash_password, verify_password

_DB_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class DatabaseError(ValueError):
    pass


class DatabaseAuthError(DatabaseError):
    pass


class DatabaseManager:
    def __init__(
        self,
        *,
        data_dir: Path,
        dim: int = EMBEDDING_DIM,
        max_elements: int = 2_000_000,
        hnsw_ef: int = 64,
        hnsw_m: int = 32,
        bootstrap_default: bool = True,
        default_db_password: str = "",
    ) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._catalog_path = self.data_dir / "catalog.sqlite"
        self._databases_root = self.data_dir / "databases"
        self._databases_root.mkdir(parents=True, exist_ok=True)
        self._dim = dim
        self._max_elements = max_elements
        self._hnsw_ef = hnsw_ef
        self._hnsw_m = hnsw_m
        self._bootstrap_default = bootstrap_default
        self._default_db_password = default_db_password
        self._lock = asyncio.Lock()
        self._stores: dict[str, NeuralContextDB] = {}

    @staticmethod
    def validate_db_id(db_id: str) -> str:
        if not _DB_ID_RE.match(db_id):
            raise DatabaseError(
                "идентификатор базы должен соответствовать ^[a-z][a-z0-9_-]{0,63}$ (нижний регистр, slug)"
            )
        return db_id

    async def init(self) -> None:
        await asyncio.to_thread(cat.init_catalog, self._catalog_path)
        await self._maybe_import_legacy_sqlite()
        await self._maybe_bootstrap_default()

    async def _maybe_import_legacy_sqlite(self) -> None:
        legacy = self.data_dir / "wisedb.sqlite"
        if not legacy.is_file():
            return
        entries = await asyncio.to_thread(cat.list_databases, self._catalog_path)
        if entries:
            return
        await asyncio.to_thread(
            cat.insert_database,
            self._catalog_path,
            db_id="default",
            name="default",
            password_hash=hash_password(self._default_db_password),
            sqlite_path=str(legacy.resolve()),
        )

    async def _maybe_bootstrap_default(self) -> None:
        if not self._bootstrap_default:
            return
        entries = await asyncio.to_thread(cat.list_databases, self._catalog_path)
        if entries:
            return
        await self._register_database(
            "default",
            password=self._default_db_password,
            name="default",
            require_password=False,
        )

    def _db_dir(self, db_id: str) -> Path:
        return self._databases_root / db_id

    async def _register_database(
        self,
        db_id: str,
        *,
        password: str,
        name: Optional[str] = None,
        require_password: bool = True,
    ) -> cat.DatabaseEntry:
        db_id = self.validate_db_id(db_id)
        if await asyncio.to_thread(cat.get_database, self._catalog_path, db_id) is not None:
            raise DatabaseError(f"база уже существует: {db_id}")
        if require_password and not password:
            raise DatabaseError("требуется пароль")

        db_dir = self._db_dir(db_id)
        db_dir.mkdir(parents=True, exist_ok=False)
        sqlite_path = db_dir / "wisedb.sqlite"
        entry = await asyncio.to_thread(
            cat.insert_database,
            self._catalog_path,
            db_id=db_id,
            name=name or db_id,
            password_hash=hash_password(password),
            sqlite_path=str(sqlite_path),
            library_path=None,
        )
        store = self._make_store(entry)
        await store.hydrate_from_sqlite()
        async with self._lock:
            self._stores[db_id] = store
        return entry

    async def create_database(
        self,
        db_id: str,
        *,
        password: str,
        name: Optional[str] = None,
    ) -> cat.DatabaseEntry:
        return await self._register_database(
            db_id, password=password, name=name, require_password=True
        )

    async def evict_store(self, db_id: str) -> None:
        async with self._lock:
            self._stores.pop(db_id, None)

    async def list_databases(self) -> list[cat.DatabaseEntry]:
        return await asyncio.to_thread(cat.list_databases, self._catalog_path)

    async def delete_database(self, db_id: str) -> bool:
        db_id = self.validate_db_id(db_id)
        async with self._lock:
            self._stores.pop(db_id, None)
        ok = await asyncio.to_thread(cat.delete_database, self._catalog_path, db_id)
        if not ok:
            return False
        db_dir = self._db_dir(db_id)
        if db_dir.exists():
            await asyncio.to_thread(shutil.rmtree, db_dir)
        return True

    async def rotate_password(self, db_id: str, *, new_password: str) -> bool:
        db_id = self.validate_db_id(db_id)
        if not new_password:
            raise DatabaseError("требуется пароль")
        ok = await asyncio.to_thread(
            cat.update_password,
            self._catalog_path,
            db_id,
            hash_password(new_password),
        )
        return ok

    def _make_store(self, entry: cat.DatabaseEntry) -> NeuralContextDB:
        lib = Path(entry.library_path) if entry.library_path else None
        return NeuralContextDB(
            dim=self._dim,
            max_elements=self._max_elements,
            hnsw_ef=self._hnsw_ef,
            hnsw_m=self._hnsw_m,
            sqlite_path=entry.sqlite_path,
            library_path=lib,
        )

    async def authenticate(self, db_id: str, password: str) -> NeuralContextDB:
        db_id = self.validate_db_id(db_id)
        entry = await asyncio.to_thread(cat.get_database, self._catalog_path, db_id)
        if entry is None:
            raise DatabaseAuthError("база не найдена")
        if not verify_password(password, entry.password_hash):
            raise DatabaseAuthError("неверный пароль базы")

        async with self._lock:
            store = self._stores.get(db_id)
            if store is not None:
                return store

        store = self._make_store(entry)
        await store.hydrate_from_sqlite()
        async with self._lock:
            self._stores[db_id] = store
        return store
