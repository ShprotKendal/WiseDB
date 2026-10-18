"""Database privilege checks and WQL operation classification."""

from __future__ import annotations

from typing import Literal

from wisedb.optics import CONTENT_OPTIC
from wisedb.server.catalog import get_catalog
from wisedb.server.users import User
from wisedb.wql.ast import (
    CreateFolderStmt,
    CreateOpticStmt,
    DeleteOpticsStmt,
    DeleteStmt,
    DropFolderStmt,
    DropOpticStmt,
    InsertStmt,
    MoveFolderStmt,
    MovePageStmt,
    SchemaStmt,
    SearchStmt,
    SelectStmt,
    StreamStmt,
    Stmt,
    UpdateOpticsStmt,
    UpdateStmt,
)
from wisedb.wql.parser import parse

RequiredPrivilege = Literal["read", "write"]
OpticRequiredPrivilege = Literal["view", "edit", "delete"]

_STATEMENT_KINDS: dict[type[Stmt], str] = {
    SelectStmt: "select",
    InsertStmt: "insert",
    UpdateStmt: "update",
    DeleteStmt: "delete",
    SearchStmt: "search",
    SchemaStmt: "schema",
    CreateFolderStmt: "create_folder",
    DropFolderStmt: "drop_folder",
    MoveFolderStmt: "move_folder",
    MovePageStmt: "move_page",
    CreateOpticStmt: "create_optic",
    DropOpticStmt: "drop_optic",
    UpdateOpticsStmt: "update_optics",
    DeleteOpticsStmt: "delete_optics",
}


def statement_kind(stmt: Stmt) -> str:
    if isinstance(stmt, StreamStmt):
        return f"stream_{statement_kind(stmt.outer)}"
    return _STATEMENT_KINDS[type(stmt)]


def classify_wql(source: str) -> tuple[str, RequiredPrivilege]:
    """Return the statement kind and the privilege required to run it."""
    stmt = parse(source)
    return statement_kind(stmt), stmt_required_privilege(stmt)


def wql_required_privilege(source: str) -> RequiredPrivilege:
    stmt = parse(source)
    return stmt_required_privilege(stmt)


def stmt_required_privilege(stmt: Stmt) -> RequiredPrivilege:
    if isinstance(stmt, StreamStmt):
        outer = stmt_required_privilege(stmt.outer)
        inner = stmt_required_privilege(stmt.inner)
        return "write" if outer == "write" or inner == "write" else "read"
    if isinstance(
        stmt,
        (
            InsertStmt,
            UpdateStmt,
            UpdateOpticsStmt,
            DeleteStmt,
            DeleteOpticsStmt,
            CreateFolderStmt,
            DropFolderStmt,
            MoveFolderStmt,
            MovePageStmt,
            CreateOpticStmt,
            DropOpticStmt,
        ),
    ):
        return "write"
    if isinstance(stmt, (SelectStmt, SearchStmt)):
        return "read"
    return "read"


def privilege_allows(holding: frozenset[str], required: RequiredPrivilege) -> bool:
    if "admin" in holding:
        return True
    if required == "read":
        return bool(holding & {"read", "write"})
    if required == "write":
        return "write" in holding or "admin" in holding
    return False


def can_admin_database(holding: frozenset[str]) -> bool:
    return "admin" in holding


def inherited_optic_privileges(database_privileges: frozenset[str]) -> frozenset[str]:
    if not database_privileges:
        return frozenset()
    result: set[str] = set()
    if privilege_allows(database_privileges, "read"):
        result.add("view")
    if privilege_allows(database_privileges, "write"):
        result.update({"view", "edit", "delete"})
    return frozenset(result)


def content_optic_privileges(user: User, database: str) -> frozenset[str]:
    """Privileges for the built-in content optic (maps database read/write)."""
    from wisedb.server.deps import database_privileges

    db_privs = database_privileges(user, database)
    return inherited_optic_privileges(db_privs)


def effective_optic_privileges(user: User, database: str, optic: str) -> frozenset[str]:
    from wisedb.server.config import SYSTEM_WIKI_DATABASE
    from wisedb.server.deps import database_privileges, system_wiki_enabled

    if optic == CONTENT_OPTIC:
        return content_optic_privileges(user, database)
    db_privs = database_privileges(user, database)
    if not db_privs:
        return frozenset()
    if user.is_root:
        if database == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
            return frozenset({"view"})
        return frozenset({"view", "edit", "delete"})
    override = get_catalog().optic_privileges_on(user.id, database, optic)
    if override is not None:
        return override
    return inherited_optic_privileges(db_privs)


def optic_allows(holding: frozenset[str], required: OpticRequiredPrivilege) -> bool:
    return required in holding
