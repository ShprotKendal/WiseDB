"""Каталог логических баз на одном сервере WiseDB (SQLite)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DatabaseEntry:
    id: str
    name: str
    password_hash: str
    sqlite_path: str
    library_path: Optional[str]
    created_at: datetime


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_catalog(path: Path) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS databases (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              sqlite_path TEXT NOT NULL,
              library_path TEXT,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def insert_database(
    path: Path,
    *,
    db_id: str,
    name: str,
    password_hash: str,
    sqlite_path: str,
    library_path: Optional[str] = None,
) -> DatabaseEntry:
    created_at = datetime.now(timezone.utc)
    with _connect(path) as conn:
        conn.execute(
            """
            INSERT INTO databases (id, name, password_hash, sqlite_path, library_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (db_id, name, password_hash, sqlite_path, library_path, created_at.isoformat()),
        )
        conn.commit()
    return DatabaseEntry(
        id=db_id,
        name=name,
        password_hash=password_hash,
        sqlite_path=sqlite_path,
        library_path=library_path,
        created_at=created_at,
    )


def get_database(path: Path, db_id: str) -> Optional[DatabaseEntry]:
    with _connect(path) as conn:
        row = conn.execute(
            """
            SELECT id, name, password_hash, sqlite_path, library_path, created_at
            FROM databases WHERE id = ?
            """,
            (db_id,),
        ).fetchone()
    if row is None:
        return None
    return DatabaseEntry(
        id=row[0],
        name=row[1],
        password_hash=row[2],
        sqlite_path=row[3],
        library_path=row[4],
        created_at=datetime.fromisoformat(row[5]),
    )


def list_databases(path: Path) -> list[DatabaseEntry]:
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT id, name, password_hash, sqlite_path, library_path, created_at
            FROM databases ORDER BY id
            """
        ).fetchall()
    return [
        DatabaseEntry(
            id=r[0],
            name=r[1],
            password_hash=r[2],
            sqlite_path=r[3],
            library_path=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]


def delete_database(path: Path, db_id: str) -> bool:
    with _connect(path) as conn:
        cur = conn.execute("DELETE FROM databases WHERE id = ?", (db_id,))
        conn.commit()
        return cur.rowcount > 0


def update_password(path: Path, db_id: str, password_hash: str) -> bool:
    with _connect(path) as conn:
        cur = conn.execute(
            "UPDATE databases SET password_hash = ? WHERE id = ?",
            (password_hash, db_id),
        )
        conn.commit()
        return cur.rowcount > 0
