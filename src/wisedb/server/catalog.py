"""Persistent server catalog: databases, users, and per-database grants."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from wisedb.server.security import hash_password, verify_password

Privilege = Literal["read", "write", "admin"]
OpticPrivilege = Literal["view", "edit", "delete"]

PRIVILEGES: frozenset[str] = frozenset({"read", "write", "admin"})
OPTIC_PRIVILEGES: frozenset[str] = frozenset({"view", "edit", "delete"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class StoredUser:
    id: str
    username: str
    password_hash: str
    created_at: str
    disabled: bool = False


@dataclass
class Grant:
    user_id: str
    database: str
    privileges: frozenset[str]


@dataclass
class OpticGrant:
    user_id: str
    database: str
    optic: str
    privileges: frozenset[str]


@dataclass
class Catalog:
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _path: Path | None = None
    _users: dict[str, StoredUser] = field(default_factory=dict)
    _username_index: dict[str, str] = field(default_factory=dict)
    _grants: list[Grant] = field(default_factory=list)
    _optic_grants: list[OpticGrant] = field(default_factory=list)
    _databases: set[str] = field(default_factory=set)

    def open(self, catalog_dir: Path) -> None:
        catalog_dir.mkdir(parents=True, exist_ok=True)
        path = catalog_dir / "system.json"
        with self._lock:
            self._path = path
            if path.is_file():
                self._load(path)
            else:
                self._users.clear()
                self._username_index.clear()
                self._grants.clear()
                self._optic_grants.clear()
                self._databases.clear()
                self._save()

    def _load(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._users.clear()
        self._username_index.clear()
        for item in raw.get("users", []):
            user = StoredUser(
                id=item["id"],
                username=item["username"],
                password_hash=item["password_hash"],
                created_at=item["created_at"],
                disabled=bool(item.get("disabled", False)),
            )
            self._users[user.id] = user
            self._username_index[user.username] = user.id
        self._grants.clear()
        for item in raw.get("grants", []):
            privs = frozenset(p for p in item.get("privileges", []) if p in PRIVILEGES)
            self._grants.append(
                Grant(user_id=item["user_id"], database=item["database"], privileges=privs)
            )
        self._optic_grants.clear()
        for item in raw.get("optic_grants", []):
            privs = frozenset(p for p in item.get("privileges", []) if p in OPTIC_PRIVILEGES)
            self._optic_grants.append(
                OpticGrant(
                    user_id=item["user_id"],
                    database=item["database"],
                    optic=item["optic"],
                    privileges=privs,
                )
            )
        self._databases = set(raw.get("databases", []))

    def _save(self) -> None:
        if self._path is None:
            return
        payload = {
            "users": [
                {
                    "id": u.id,
                    "username": u.username,
                    "password_hash": u.password_hash,
                    "created_at": u.created_at,
                    "disabled": u.disabled,
                }
                for u in self._users.values()
            ],
            "grants": [
                {"user_id": g.user_id, "database": g.database, "privileges": sorted(g.privileges)}
                for g in self._grants
            ],
            "optic_grants": [
                {
                    "user_id": g.user_id,
                    "database": g.database,
                    "optic": g.optic,
                    "privileges": sorted(g.privileges),
                }
                for g in self._optic_grants
            ],
            "databases": sorted(self._databases),
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def list_databases(self) -> list[str]:
        with self._lock:
            return sorted(self._databases)

    def database_exists(self, name: str) -> bool:
        with self._lock:
            return name in self._databases

    def create_database(self, name: str) -> None:
        with self._lock:
            if name in self._databases:
                raise ValueError("database already exists")
            self._databases.add(name)
            self._save()

    def delete_database(self, name: str) -> None:
        with self._lock:
            if name not in self._databases:
                raise KeyError("database not found")
            self._databases.remove(name)
            self._grants = [g for g in self._grants if g.database != name]
            self._optic_grants = [g for g in self._optic_grants if g.database != name]
            self._save()

    def list_users(self) -> list[StoredUser]:
        with self._lock:
            return sorted(self._users.values(), key=lambda u: u.username)

    def get_user(self, user_id: str) -> StoredUser | None:
        with self._lock:
            return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> StoredUser | None:
        with self._lock:
            uid = self._username_index.get(username)
            return self._users.get(uid) if uid else None

    def create_user(self, username: str, password: str) -> StoredUser:
        username = username.strip()
        if not username or username == "root":
            raise ValueError("invalid username")
        with self._lock:
            if username in self._username_index:
                raise ValueError("username already exists")
            user = StoredUser(
                id=str(uuid.uuid4()),
                username=username,
                password_hash=hash_password(password),
                created_at=_utc_now_iso(),
            )
            self._users[user.id] = user
            self._username_index[user.username] = user.id
            self._save()
            return user

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            if user_id not in self._users:
                raise KeyError("user not found")
            del self._users[user_id]
            self._username_index = {u.username: u.id for u in self._users.values()}
            self._grants = [g for g in self._grants if g.user_id != user_id]
            self._optic_grants = [g for g in self._optic_grants if g.user_id != user_id]
            self._save()

    def set_password(self, user_id: str, password: str) -> None:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                raise KeyError("user not found")
            user.password_hash = hash_password(password)
            self._save()

    def verify_user_password(self, username: str, password: str) -> StoredUser | None:
        with self._lock:
            user = self.get_user_by_username(username)
            if user is None or user.disabled:
                return None
            if not verify_password(password, user.password_hash):
                return None
            return user

    def set_grants(self, user_id: str, database: str, privileges: frozenset[str]) -> Grant:
        unknown = privileges - PRIVILEGES
        if unknown:
            raise ValueError(f"unknown privileges: {', '.join(sorted(unknown))}")
        with self._lock:
            if user_id not in self._users:
                raise KeyError("user not found")
            if database not in self._databases:
                raise KeyError("database not found")
            self._grants = [g for g in self._grants if not (g.user_id == user_id and g.database == database)]
            grant = Grant(user_id=user_id, database=database, privileges=privileges)
            if privileges:
                self._grants.append(grant)
            self._save()
            return grant

    def clear_grants(self, user_id: str, database: str) -> None:
        with self._lock:
            if user_id not in self._users:
                raise KeyError("user not found")
            before = len(self._grants)
            self._grants = [g for g in self._grants if not (g.user_id == user_id and g.database == database)]
            if len(self._grants) == before:
                raise KeyError("grant not found")
            self._save()

    def grants_for_user(self, user_id: str) -> list[Grant]:
        with self._lock:
            return [g for g in self._grants if g.user_id == user_id]

    def privileges_on(self, user_id: str, database: str) -> frozenset[str]:
        with self._lock:
            for grant in self._grants:
                if grant.user_id == user_id and grant.database == database:
                    return grant.privileges
            return frozenset()

    def set_optic_grants(
        self,
        user_id: str,
        database: str,
        optic: str,
        privileges: frozenset[str],
    ) -> OpticGrant:
        unknown = privileges - OPTIC_PRIVILEGES
        if unknown:
            raise ValueError(f"unknown optic privileges: {', '.join(sorted(unknown))}")
        with self._lock:
            if user_id not in self._users:
                raise KeyError("user not found")
            if database not in self._databases:
                raise KeyError("database not found")
            self._optic_grants = [
                g
                for g in self._optic_grants
                if not (g.user_id == user_id and g.database == database and g.optic == optic)
            ]
            grant = OpticGrant(
                user_id=user_id,
                database=database,
                optic=optic,
                privileges=privileges,
            )
            self._optic_grants.append(grant)
            self._save()
            return grant

    def clear_optic_grants(self, user_id: str, database: str, optic: str) -> None:
        with self._lock:
            if user_id not in self._users:
                raise KeyError("user not found")
            before = len(self._optic_grants)
            self._optic_grants = [
                g
                for g in self._optic_grants
                if not (g.user_id == user_id and g.database == database and g.optic == optic)
            ]
            if len(self._optic_grants) == before:
                raise KeyError("optic grant not found")
            self._save()

    def clear_optic_grants_for_optic(self, database: str, optic: str) -> None:
        with self._lock:
            self._optic_grants = [
                g for g in self._optic_grants if not (g.database == database and g.optic == optic)
            ]
            self._save()

    def optic_grants_for_user(self, user_id: str) -> list[OpticGrant]:
        with self._lock:
            return [g for g in self._optic_grants if g.user_id == user_id]

    def optic_privileges_on(self, user_id: str, database: str, optic: str) -> frozenset[str] | None:
        with self._lock:
            for grant in self._optic_grants:
                if grant.user_id == user_id and grant.database == database and grant.optic == optic:
                    return grant.privileges
            return None


_catalog = Catalog()


def get_catalog() -> Catalog:
    return _catalog


def reset_catalog() -> None:
    with _catalog._lock:
        _catalog._path = None
        _catalog._users.clear()
        _catalog._username_index.clear()
        _catalog._grants.clear()
        _catalog._optic_grants.clear()
        _catalog._databases.clear()
