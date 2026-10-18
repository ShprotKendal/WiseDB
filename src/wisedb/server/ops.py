"""Shared server operations for HTTP and WebSocket."""

from __future__ import annotations

import base64
import binascii
import shutil
from pathlib import Path
from typing import Any

from wisedb.server import deps as deps_module
from wisedb.optics import CONTENT_OPTIC, OpticKind, OpticsConflictError, OpticsError
from wisedb.server.catalog import OPTIC_PRIVILEGES, PRIVILEGES, get_catalog
from wisedb.server.config import get_settings
from wisedb.server.config import SYSTEM_WIKI_DATABASE
from wisedb.server.deps import (
    database_privileges,
    get_database_wiki,
    is_reserved_database_name,
    system_wiki_enabled,
    validate_database_name,
)
from wisedb.server.engine.layout import (
    LayoutError,
    create_folder,
    delete_folder,
    media_path,
    move_folder,
    move_page,
    nested_database_tree,
)
from wisedb.server.permissions import (
    OpticRequiredPrivilege,
    classify_wql,
    effective_optic_privileges,
    optic_allows,
    privilege_allows,
    statement_kind,
    stmt_required_privilege,
)
from wisedb.server.sessions import get_session_store
from wisedb.server.users import User
from wisedb.memory import Wiki
from wisedb.server.wql.lexer import LexerError
from wisedb.wql import WqlError, execute, iter_stream
from wisedb.wql.ast import (
    CreateOpticStmt,
    DeleteOpticsStmt,
    DropOpticStmt,
    SelectStmt,
    StreamStmt,
    UpdateOpticsStmt,
    Stmt,
)
from wisedb.wql.parser import optics_filters_from_where, parse


class OpError(Exception):
    """Operation failed with an HTTP-style status code."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        self.status_code = status_code
        self.detail = detail
        self.line = line
        self.column = column
        super().__init__(detail)


def _wql_error(exc: WqlError | LexerError) -> OpError:
    line = getattr(exc, "line", None)
    column = getattr(exc, "column", None)
    return OpError(422, str(exc), line=line, column=column)


def _layout_error(exc: LayoutError) -> OpError:
    message = str(exc)
    if "not found" in message:
        return OpError(404, message)
    return OpError(422, message)


def _require_read(user: User, database: str) -> tuple[str, Wiki]:
    name, privs = open_database(database, user)
    if not privilege_allows(privs, "read"):
        raise OpError(403, "read privilege required")
    return name, get_database_wiki(name)


def _require_write(user: User, database: str) -> tuple[str, Wiki]:
    name, privs = open_database(database, user)
    if not privilege_allows(privs, "write"):
        raise OpError(403, "write privilege required")
    return name, get_database_wiki(name)


def _require_admin(user: User, database: str) -> tuple[str, Wiki]:
    name, privs = open_database(database, user)
    if not privilege_allows(privs, "admin"):
        raise OpError(403, "admin privilege required")
    return name, get_database_wiki(name)


def _reject_system_wiki_optic_writes(database: str) -> None:
    if database == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
        raise OpError(403, "system wiki database is read-only")


def _require_optic(
    user: User,
    database: str,
    optic: str,
    required: OpticRequiredPrivilege,
) -> tuple[str, Wiki]:
    name, _privs = open_database(database, user)
    if optic == CONTENT_OPTIC:
        raise OpError(422, "content optic is not available on this route")
    wiki = get_database_wiki(name)
    custom_names = {item["name"] for item in wiki.list_optics() if item["name"] != CONTENT_OPTIC}
    if optic not in custom_names:
        raise OpError(404, "optic not found")
    privs = effective_optic_privileges(user, name, optic)
    if not optic_allows(privs, required):
        raise OpError(403, f"{required} privilege required on optic")
    return name, wiki


def _optic_error(exc: OpticsError) -> OpError:
    message = str(exc)
    if isinstance(exc, OpticsConflictError):
        return OpError(409, message)
    if "not found" in message:
        return OpError(404, message)
    return OpError(422, message)


def _node_exists(wiki: Wiki, slug: str) -> None:
    try:
        wiki.read(slug)
    except Exception as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise OpError(404, "page not found") from exc
        raise OpError(422, message) from exc


_MEDIA_MAX_BYTES = 25 * 1024 * 1024


def login(username: str, password: str) -> dict[str, Any]:
    store = get_session_store()
    try:
        token, user, session_id = store.login(username, password)
    except RuntimeError as exc:
        raise OpError(503, str(exc)) from exc
    except ValueError as exc:
        raise OpError(422, str(exc)) from exc
    except PermissionError as exc:
        raise OpError(401, str(exc)) from exc
    return {"token": token, "session_id": session_id, "user": user.public_dict()}


def logout(user: User) -> dict[str, str]:
    store = get_session_store()
    try:
        store.logout(user)
    except KeyError as exc:
        raise OpError(404, "session not found") from exc
    except PermissionError as exc:
        raise OpError(403, str(exc)) from exc
    return {"status": "ok"}


def users_me(user: User) -> dict[str, str | bool]:
    return user.public_dict()


def users_list(_actor: User) -> list[dict[str, Any]]:
    catalog = get_catalog()
    return [
        {
            "id": u.id,
            "username": u.username,
            "created_at": u.created_at,
            "disabled": u.disabled,
            "grants": [
                {"database": g.database, "privileges": sorted(g.privileges)}
                for g in catalog.grants_for_user(u.id)
            ],
        }
        for u in catalog.list_users()
    ]


def users_create(username: str, password: str) -> dict[str, Any]:
    catalog = get_catalog()
    try:
        user = catalog.create_user(username, password)
    except ValueError as exc:
        raise OpError(422, str(exc)) from exc
    return {
        "id": user.id,
        "username": user.username,
        "created_at": user.created_at,
        "disabled": user.disabled,
        "grants": [],
    }


def users_delete(user_id: str) -> dict[str, str]:
    try:
        get_catalog().delete_user(user_id)
    except KeyError as exc:
        raise OpError(404, "user not found") from exc
    return {"status": "ok"}


def users_password(actor: User, user_id: str, password: str) -> dict[str, str]:
    if user_id == "root":
        raise OpError(403, "root password is configured on the server")
    if not actor.is_root and actor.id != user_id:
        raise OpError(403, "cannot change another user's password")
    try:
        get_catalog().set_password(user_id, password)
    except KeyError as exc:
        raise OpError(404, "user not found") from exc
    return {"status": "ok"}


def users_grant(user_id: str, database: str, privileges: list[str]) -> dict[str, Any]:
    db_name = validate_database_name(database)
    if db_name == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
        raise OpError(422, "grants are not allowed on the system wiki database")
    unknown = set(privileges) - PRIVILEGES
    if unknown:
        raise OpError(422, f"unknown privileges: {', '.join(sorted(unknown))}")
    try:
        grant = get_catalog().set_grants(user_id, database, frozenset(privileges))
    except KeyError as exc:
        raise OpError(404, str(exc)) from exc
    except ValueError as exc:
        raise OpError(422, str(exc)) from exc
    return {
        "user_id": grant.user_id,
        "database": grant.database,
        "privileges": sorted(grant.privileges),
    }


def users_revoke(user_id: str, database: str) -> dict[str, str]:
    try:
        get_catalog().clear_grants(user_id, database)
    except KeyError as exc:
        raise OpError(404, str(exc)) from exc
    return {"status": "ok"}


def users_optic_grant(
    user_id: str,
    database: str,
    optic: str,
    privileges: list[str],
) -> dict[str, Any]:
    db_name = validate_database_name(database)
    if optic == CONTENT_OPTIC:
        raise OpError(422, "grants are not allowed on the content optic")
    if db_name == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
        raise OpError(422, "grants are not allowed on the system wiki database")
    unknown = set(privileges) - OPTIC_PRIVILEGES
    if unknown:
        raise OpError(422, f"unknown optic privileges: {', '.join(sorted(unknown))}")
    wiki = get_database_wiki(db_name)
    custom_names = {item["name"] for item in wiki.list_optics() if item["name"] != CONTENT_OPTIC}
    if optic not in custom_names:
        raise OpError(422, "optic not found in database")
    try:
        grant = get_catalog().set_optic_grants(
            user_id,
            db_name,
            optic,
            frozenset(privileges),
        )
    except KeyError as exc:
        raise OpError(404, str(exc)) from exc
    except ValueError as exc:
        raise OpError(422, str(exc)) from exc
    return {
        "user_id": grant.user_id,
        "database": grant.database,
        "optic": grant.optic,
        "privileges": sorted(grant.privileges),
    }


def users_optic_revoke(user_id: str, database: str, optic: str) -> dict[str, str]:
    db_name = validate_database_name(database)
    if optic == CONTENT_OPTIC:
        raise OpError(422, "grants are not allowed on the content optic")
    try:
        get_catalog().clear_optic_grants(user_id, db_name, optic)
    except KeyError as exc:
        raise OpError(404, str(exc)) from exc
    return {"status": "ok"}


def databases_list(user: User) -> dict[str, list[str]]:
    catalog = get_catalog()
    if user.is_root:
        names = catalog.list_databases()
    else:
        names = sorted({g.database for g in catalog.grants_for_user(user.id) if g.privileges})
    if system_wiki_enabled() and catalog.database_exists(SYSTEM_WIKI_DATABASE):
        names = sorted(set(names) | {SYSTEM_WIKI_DATABASE})
    return {"databases": names}


def databases_create(name: str) -> dict[str, str]:
    db_name = validate_database_name(name)
    if is_reserved_database_name(db_name):
        raise OpError(422, "reserved database name")
    catalog = get_catalog()
    settings = get_settings()
    try:
        catalog.create_database(db_name)
    except ValueError as exc:
        raise OpError(409, str(exc)) from exc
    settings.database_root(db_name).mkdir(parents=True, exist_ok=True)
    return {"name": db_name, "status": "ok"}


def database_settings_get(user: User, database: str) -> dict[str, Any]:
    _, wiki = _require_read(user, database)
    return wiki.settings()


def database_settings_patch(
    user: User,
    database: str,
    *,
    semantics: bool | None = None,
    code_root: str | None = None,
    patch_code_root: bool = False,
) -> dict[str, Any]:
    _, wiki = _require_admin(user, database)
    result = wiki.settings()
    if semantics is not None:
        result = wiki.update_settings(semantics=semantics)
    if patch_code_root:
        from wisedb.workspace import WorkspaceError

        try:
            result = wiki.set_code_root(code_root)
        except WorkspaceError as exc:
            raise OpError(422, str(exc)) from exc
    return result


def databases_drop(user: User, database: str) -> dict[str, str]:
    name = validate_database_name(database)
    if name == SYSTEM_WIKI_DATABASE and system_wiki_enabled():
        raise OpError(403, "system wiki database cannot be dropped")
    catalog = get_catalog()
    if not catalog.database_exists(name):
        raise OpError(404, "database not found")
    privs = database_privileges(user, name)
    if not user.is_root and "admin" not in privs:
        raise OpError(403, "admin privilege required")
    settings = get_settings()
    root = settings.database_root(name)
    try:
        catalog.delete_database(name)
    except KeyError as exc:
        raise OpError(404, "database not found") from exc
    with deps_module._wiki_lock:
        wiki = deps_module._wiki_instances.pop(name, None)
        if wiki is not None:
            wiki.close()
    if root.exists():
        shutil.rmtree(root)
    return {"status": "ok"}


def open_database(database: str, user: User) -> tuple[str, frozenset[str]]:
    name = validate_database_name(database)
    catalog = get_catalog()
    if not catalog.database_exists(name):
        raise OpError(404, "database not found")
    privs = database_privileges(user, name)
    if not privs:
        raise OpError(403, "no access to this database")
    return name, privs


def _filter_optics_select_result(user: User, database: str, result: dict[str, Any]) -> dict[str, Any]:
    rows = result.get("rows")
    if not isinstance(rows, list):
        return result
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        optic = row.get("name")
        if isinstance(optic, str) and optic_allows(
            effective_optic_privileges(user, database, optic),
            "view",
        ):
            filtered.append(row)
    return {"rows": filtered}


def _enforce_wql_access(user: User, database: str, stmt: Stmt) -> None:
    if isinstance(stmt, StreamStmt):
        _enforce_wql_access(user, database, stmt.outer)
        _enforce_wql_access(user, database, stmt.inner)
        return
    if isinstance(stmt, (CreateOpticStmt, DropOpticStmt)):
        privs = database_privileges(user, database)
        if not user.is_root and "admin" not in privs:
            raise OpError(403, "admin privilege required")
        _reject_system_wiki_optic_writes(database)
        return
    if isinstance(stmt, UpdateOpticsStmt):
        if isinstance(stmt.name, str) and not optic_allows(
            effective_optic_privileges(user, database, stmt.name),
            "edit",
        ):
            raise OpError(403, "edit privilege required on optic")
        _reject_system_wiki_optic_writes(database)
        return
    if isinstance(stmt, DeleteOpticsStmt):
        if isinstance(stmt.name, str) and not optic_allows(
            effective_optic_privileges(user, database, stmt.name),
            "delete",
        ):
            raise OpError(403, "delete privilege required on optic")
        _reject_system_wiki_optic_writes(database)
        return
    if isinstance(stmt, SelectStmt) and stmt.table == "optics":
        _name_filter, optic_name = optics_filters_from_where(stmt.where)
        if optic_name is not None and not optic_allows(
            effective_optic_privileges(user, database, optic_name),
            "view",
        ):
            raise OpError(403, "view privilege required on optic")
        return


def classify_query(query: str) -> tuple[str, str]:
    try:
        return classify_wql(query)
    except (WqlError, LexerError) as exc:
        raise _wql_error(exc) from exc


def wql_parse(user: User, database: str, query: str) -> dict[str, Any]:
    _name, privs = open_database(database, user)
    kind, required = classify_query(query)
    streaming = False
    try:
        streaming = isinstance(parse(query), StreamStmt)
    except (WqlError, LexerError):
        pass
    return {
        "statement": kind,
        "privilege": required,
        "allowed": privilege_allows(privs, required),
        "streaming": streaming,
    }


def wql_execute(user: User, database: str, query: str) -> dict[str, Any]:
    name, privs = open_database(database, user)
    try:
        stmt = parse(query)
    except (WqlError, LexerError) as exc:
        raise _wql_error(exc) from exc
    _kind, required = statement_kind(stmt), stmt_required_privilege(stmt)
    if not privilege_allows(privs, required):
        raise OpError(403, f"{required} privilege required")
    _enforce_wql_access(user, name, stmt)
    wiki = get_database_wiki(name)
    with wiki.serialized():
        try:
            result = execute(wiki, query)
        except (WqlError, LexerError) as exc:
            raise _wql_error(exc) from exc
        if isinstance(stmt, DropOpticStmt):
            get_catalog().clear_optic_grants_for_optic(name, stmt.name)
        if isinstance(stmt, SelectStmt) and stmt.table == "optics":
            result = _filter_optics_select_result(user, name, result)
        return result


def wql_batch(user: User, database: str, queries: list[str]) -> dict[str, list[dict]]:
    name, privs = open_database(database, user)
    parsed: list[Stmt] = []
    for index, query in enumerate(queries, start=1):
        try:
            stmt = parse(query)
            parsed.append(stmt)
            required = stmt_required_privilege(stmt)
        except (WqlError, LexerError) as exc:
            raise OpError(422, f"query {index}: {exc}") from exc
        if not privilege_allows(privs, required):
            raise OpError(403, f"query {index}: {required} privilege required")
        try:
            _enforce_wql_access(user, name, stmt)
        except OpError as exc:
            raise OpError(exc.status_code, f"query {index}: {exc.detail}") from exc
    wiki = get_database_wiki(name)
    results: list[dict] = []
    with wiki.serialized():
        for index, (query, stmt) in enumerate(zip(queries, parsed, strict=True), start=1):
            try:
                result = execute(wiki, query)
            except (WqlError, LexerError) as exc:
                raise OpError(422, f"query {index}: {exc}") from exc
            if isinstance(stmt, DropOpticStmt):
                get_catalog().clear_optic_grants_for_optic(name, stmt.name)
            if isinstance(stmt, SelectStmt) and stmt.table == "optics":
                result = _filter_optics_select_result(user, name, result)
            results.append(result)
    return {"results": results}


def node_payload(user: User, database: str, slug: str) -> dict[str, Any]:
    name = validate_database_name(database)
    catalog = get_catalog()
    if not catalog.database_exists(name):
        raise OpError(404, "database not found")
    privs = database_privileges(user, name)
    if not privilege_allows(privs, "read"):
        raise OpError(403, "read privilege required")
    wiki = get_database_wiki(name)
    try:
        page, path = media_path(wiki, slug)
    except LayoutError as exc:
        message = str(exc)
        if "not found" in message:
            raise OpError(404, message) from exc
        raise OpError(422, message) from exc
    data = path.read_bytes()
    return {
        "mime": page.get("mime") or "application/octet-stream",
        "filename": Path(page["rel_path"]).name,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def database_tree_nested(user: User, database: str) -> dict[str, Any]:
    _name, wiki = _require_read(user, database)
    with wiki.serialized():
        return nested_database_tree(wiki)


def nodes_list(user: User, database: str) -> dict[str, Any]:
    _name, wiki = _require_read(user, database)
    with wiki.serialized():
        return wiki.list()


def _search_query_has_terms(query: str) -> bool:
    for ch in query:
        if ord(ch) >= 0x80 or ch.isalnum():
            return True
    return False


def nodes_search(user: User, database: str, query: str, *, limit: int = 20) -> dict[str, Any]:
    if not _search_query_has_terms(query):
        raise OpError(422, "search query is required")
    top_k = max(1, min(limit, 100))
    _name, wiki = _require_read(user, database)
    with wiki.serialized():
        result = wiki.search(query, budget_tokens=0, top_k=top_k)
        nodes: list[dict[str, Any]] = []
        hits = result.get("hits")
        if isinstance(hits, list):
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                slug = hit.get("slug")
                if not isinstance(slug, str):
                    continue
                read_result = wiki.read(slug)
                node = read_result.get("node")
                if isinstance(node, dict):
                    nodes.append(node)
        return {"query": query, "nodes": nodes}


def nodes_get(user: User, database: str, slug: str, *, neighbors: bool = False) -> dict[str, Any]:
    _name, wiki = _require_read(user, database)
    with wiki.serialized():
        try:
            return wiki.read(slug, neighbors=neighbors)
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise OpError(404, "page not found") from exc
            raise OpError(422, message) from exc


def nodes_content(user: User, database: str, slug: str) -> str:
    _name, wiki = _require_read(user, database)
    with wiki.serialized():
        try:
            return wiki.markdown(slug)
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise OpError(404, "page not found") from exc
            raise OpError(422, message) from exc


def nodes_create(
    user: User,
    database: str,
    *,
    slug: str,
    markdown: str | None = None,
    rel_path: str | None = None,
    file_base64: str | None = None,
) -> dict[str, Any]:
    _name, wiki = _require_write(user, database)
    file_bytes = None
    if file_base64 is not None:
        try:
            file_bytes = base64.b64decode(file_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OpError(422, "file_base64 is invalid") from exc
        if len(file_bytes) > _MEDIA_MAX_BYTES:
            raise OpError(422, "media file exceeds 25 MiB")
    if markdown is None and file_bytes is None:
        raise OpError(422, "markdown or file_base64 required")
    with wiki.serialized():
        try:
            return wiki.remember(slug, markdown=markdown, file_bytes=file_bytes, rel_path=rel_path)
        except LayoutError as exc:
            raise _layout_error(exc) from exc


def nodes_patch(
    user: User,
    database: str,
    slug: str,
    *,
    type: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    if type is None and title is None and summary is None and body is None:
        raise OpError(422, "at least one field required")
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return wiki.update(slug, type=type, title=title, summary=summary, body=body)
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise OpError(404, "page not found") from exc
            raise OpError(422, message) from exc


def nodes_put(user: User, database: str, slug: str, markdown: str) -> dict[str, Any]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return wiki.edit(slug, markdown)
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise OpError(404, "page not found") from exc
            raise OpError(422, message) from exc


def nodes_delete(user: User, database: str, slug: str) -> dict[str, str]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            wiki.delete(slug)
        except Exception as exc:
            message = str(exc)
            if "not found" in message.lower():
                raise OpError(404, "page not found") from exc
            raise OpError(422, message) from exc
    return {"status": "ok"}


def folders_create(user: User, database: str, path: str) -> dict[str, str]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return create_folder(wiki, path)
        except LayoutError as exc:
            raise _layout_error(exc) from exc


def folders_delete(user: User, database: str, path: str) -> dict[str, str]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return delete_folder(wiki, path)
        except LayoutError as exc:
            raise _layout_error(exc) from exc


def nodes_move(user: User, database: str, slug: str, destination: str) -> dict[str, Any]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return move_page(wiki, slug, destination)
        except LayoutError as exc:
            raise _layout_error(exc) from exc


def folders_move(user: User, database: str, source: str, destination: str) -> dict[str, str]:
    _name, wiki = _require_write(user, database)
    with wiki.serialized():
        try:
            return move_folder(wiki, source, destination)
        except LayoutError as exc:
            raise _layout_error(exc) from exc


def optics_list(user: User, database: str) -> dict[str, Any]:
    name, _privs = open_database(database, user)
    wiki = get_database_wiki(name)
    items: list[dict[str, Any]] = []
    for record in wiki.list_optics():
        optic_name = record["name"]
        privs = effective_optic_privileges(user, name, optic_name)
        if not optic_allows(privs, "view"):
            continue
        items.append(
            {
                "name": optic_name,
                "description": record.get("description", ""),
                "kind": record.get("kind", "markdown"),
                "privileges": sorted(privs),
                "builtin": optic_name == CONTENT_OPTIC,
            }
        )
    return {"optics": items}


def optics_create(
    user: User,
    database: str,
    name: str,
    *,
    description: str = "",
    kind: str = "markdown",
) -> dict[str, Any]:
    name_db, wiki = _require_admin(user, database)
    _reject_system_wiki_optic_writes(name_db)
    with wiki.serialized():
        try:
            record = wiki.create_optic(name, description=description, kind=kind)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return record


def optics_patch(user: User, database: str, optic: str, description: str) -> dict[str, Any]:
    name_db, wiki = _require_admin(user, database)
    _reject_system_wiki_optic_writes(name_db)
    with wiki.serialized():
        try:
            record = wiki.update_optic(optic, description=description)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return record


def optics_drop(user: User, database: str, optic: str) -> dict[str, str]:
    name_db, wiki = _require_admin(user, database)
    _reject_system_wiki_optic_writes(name_db)
    with wiki.serialized():
        try:
            wiki.drop_optic(optic)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    get_catalog().clear_optic_grants_for_optic(name_db, optic)
    return {"status": "ok"}


def node_optic_get(user: User, database: str, slug: str, optic: str) -> dict[str, Any]:
    _name_db, wiki = _require_optic(user, database, optic, "view")
    with wiki.serialized():
        try:
            _node_exists(wiki, slug)
            value = wiki.optic_value(optic, slug)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return {"slug": slug, "optic": optic, **value}


def node_optic_put(
    user: User,
    database: str,
    slug: str,
    optic: str,
    *,
    text: str | None = None,
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    base_sha256: str | None = None,
) -> dict[str, Any]:
    name_db, wiki = _require_optic(user, database, optic, "edit")
    _reject_system_wiki_optic_writes(name_db)
    with wiki.serialized():
        try:
            _node_exists(wiki, slug)
            record = next(item for item in wiki.list_optics() if item["name"] == optic)
            kind = OpticKind.from_storage(record.get("kind"))
            if kind == OpticKind.SPAN:
                if path is not None and start_line is not None and end_line is not None:
                    value = wiki.bind_optic_span(
                        optic,
                        slug,
                        path=path,
                        start_line=start_line,
                        end_line=end_line,
                    )
                elif text is not None and base_sha256 is not None:
                    value = wiki.edit_optic_span(
                        optic,
                        slug,
                        text=text,
                        base_sha256=base_sha256,
                    )
                else:
                    raise OpError(
                        422,
                        "span requires path/start_line/end_line or text/base_sha256",
                    )
                return {"slug": slug, "optic": optic, **value}
            if kind in {OpticKind.IMAGE, OpticKind.VIDEO, OpticKind.FILE}:
                raise OpError(422, "binary optic requires raw bytes upload on /bytes route")
            if text is None:
                raise OpError(422, "text is required")
            wiki.set_optic_text(optic, slug, text)
            value = wiki.optic_value(optic, slug)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return {"slug": slug, "optic": optic, **value}


def node_optic_bytes_put(
    user: User,
    database: str,
    slug: str,
    optic: str,
    data: bytes,
    *,
    filename: str | None = None,
    media_type: str | None = None,
) -> dict[str, Any]:
    name_db, wiki = _require_optic(user, database, optic, "edit")
    _reject_system_wiki_optic_writes(name_db)
    kind_value = OpticKind.from_storage(
        next(item for item in wiki.list_optics() if item["name"] == optic).get("kind")
    ).value
    with wiki.serialized():
        try:
            _node_exists(wiki, slug)
            meta = wiki.set_optic_bytes(
                optic,
                slug,
                data,
                filename=filename,
                media_type=media_type,
            )
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return {"slug": slug, "optic": optic, "kind": kind_value, "has_bytes": True, **meta}


def node_optic_bytes_get(user: User, database: str, slug: str, optic: str) -> tuple[bytes, dict[str, Any]]:
    _name_db, wiki = _require_optic(user, database, optic, "view")
    with wiki.serialized():
        try:
            _node_exists(wiki, slug)
            return wiki.optic_bytes(optic, slug)
        except OpticsError as exc:
            raise _optic_error(exc) from exc


def node_optic_delete(user: User, database: str, slug: str, optic: str) -> dict[str, str]:
    name_db, wiki = _require_optic(user, database, optic, "delete")
    _reject_system_wiki_optic_writes(name_db)
    with wiki.serialized():
        try:
            _node_exists(wiki, slug)
            wiki.clear_optic_text(optic, slug)
        except OpticsError as exc:
            raise _optic_error(exc) from exc
    return {"status": "ok"}

