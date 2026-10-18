"""WebSocket API mirroring HTTP operations and WS[] streaming."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from wisedb.server.deps import get_database_wiki
from wisedb.server.ops import (
    OpError,
    classify_query,
    database_settings_get,
    database_settings_patch,
    databases_create,
    databases_drop,
    databases_list,
    login as op_login,
    logout as op_logout,
    node_payload,
    open_database,
    users_create,
    users_delete,
    users_grant,
    users_list,
    users_me,
    users_optic_grant,
    users_optic_revoke,
    users_password,
    users_revoke,
    wql_execute,
    wql_parse,
)
from wisedb.server.permissions import privilege_allows
from wisedb.server.sessions import get_session_store
from wisedb.server.users import User
from wisedb.server.wql.lexer import LexerError
from wisedb.wql import WqlError, iter_stream
from wisedb.wql.ast import StreamStmt
from wisedb.wql.parser import parse

ws_router = APIRouter()


def _bearer_from_headers(websocket: WebSocket) -> str | None:
    auth = websocket.headers.get("authorization") or websocket.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload, default=str))


def _error_frame(
    msg_id: str | None,
    exc: OpError | WqlError | LexerError,
) -> dict[str, Any]:
    frame: dict[str, Any] = {"type": "error", "id": msg_id}
    if isinstance(exc, OpError):
        frame["status"] = exc.status_code
        frame["message"] = exc.detail
        if exc.line is not None:
            frame["line"] = exc.line
        if exc.column is not None:
            frame["column"] = exc.column
    else:
        frame["status"] = 422
        frame["message"] = str(exc)
        line = getattr(exc, "line", None)
        column = getattr(exc, "column", None)
        if line is not None:
            frame["line"] = line
        if column is not None:
            frame["column"] = column
    return frame


async def _handle_execute(
    websocket: WebSocket,
    user: User,
    msg_id: str,
    database: str,
    query: str,
    cancel_flags: dict[str, bool],
) -> None:
    try:
        stmt = parse(query)
    except (WqlError, LexerError) as exc:
        await _send(websocket, _error_frame(msg_id, exc))
        return

    try:
        name, privs = open_database(database, user)
    except OpError as exc:
        await _send(websocket, _error_frame(msg_id, exc))
        return

    try:
        _kind, required = classify_query(query)
    except OpError as exc:
        await _send(websocket, _error_frame(msg_id, exc))
        return

    if not privilege_allows(privs, required):
        await _send(
            websocket,
            {
                "type": "error",
                "id": msg_id,
                "status": 403,
                "message": f"{required} privilege required",
            },
        )
        return

    wiki = get_database_wiki(name)

    if isinstance(stmt, StreamStmt):
        cancel_flags[msg_id] = False
        count = 0
        try:
            for index, row, result in iter_stream(wiki, query):
                if cancel_flags.get(msg_id):
                    await _send(websocket, {"type": "cancelled", "id": msg_id})
                    return
                await _send(
                    websocket,
                    {"type": "item", "id": msg_id, "index": index, "row": row, "result": result},
                )
                count += 1
        except (WqlError, LexerError) as exc:
            await _send(websocket, _error_frame(msg_id, exc))
            return
        except OpError as exc:
            await _send(websocket, _error_frame(msg_id, exc))
            return
        finally:
            cancel_flags.pop(msg_id, None)
        await _send(websocket, {"type": "done", "id": msg_id, "count": count})
        return

    try:
        result = await asyncio.to_thread(wql_execute, user, database, query)
    except OpError as exc:
        await _send(websocket, _error_frame(msg_id, exc))
        return
    await _send(websocket, {"type": "result", "id": msg_id, "result": result})


def _dispatch_op(op: str, message: dict[str, Any], user: User) -> dict[str, Any]:
    if op == "users.me":
        return {"user": users_me(user)}

    if op == "users.list":
        if not user.is_root:
            raise OpError(403, "root required")
        return {"users": users_list(user)}

    if op == "users.create":
        if not user.is_root:
            raise OpError(403, "root required")
        username = message.get("username")
        password = message.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise OpError(422, "username and password required")
        return {"user": users_create(username, password)}

    if op == "users.delete":
        if not user.is_root:
            raise OpError(403, "root required")
        user_id = message.get("user_id")
        if not isinstance(user_id, str):
            raise OpError(422, "user_id required")
        return users_delete(user_id)

    if op == "users.password":
        user_id = message.get("user_id")
        password = message.get("password")
        if not isinstance(user_id, str) or not isinstance(password, str):
            raise OpError(422, "user_id and password required")
        return users_password(user, user_id, password)

    if op == "users.grant":
        if not user.is_root:
            raise OpError(403, "root required")
        user_id = message.get("user_id")
        database = message.get("database")
        privileges = message.get("privileges")
        if not isinstance(user_id, str) or not isinstance(database, str) or not isinstance(privileges, list):
            raise OpError(422, "user_id, database, and privileges required")
        return users_grant(user_id, database, privileges)

    if op == "users.revoke":
        if not user.is_root:
            raise OpError(403, "root required")
        user_id = message.get("user_id")
        database = message.get("database")
        if not isinstance(user_id, str) or not isinstance(database, str):
            raise OpError(422, "user_id and database required")
        return users_revoke(user_id, database)

    if op == "users.optic_grant":
        if not user.is_root:
            raise OpError(403, "root required")
        user_id = message.get("user_id")
        database = message.get("database")
        optic = message.get("optic")
        privileges = message.get("privileges")
        if (
            not isinstance(user_id, str)
            or not isinstance(database, str)
            or not isinstance(optic, str)
            or not isinstance(privileges, list)
        ):
            raise OpError(422, "user_id, database, optic, and privileges required")
        return users_optic_grant(user_id, database, optic, privileges)

    if op == "users.optic_revoke":
        if not user.is_root:
            raise OpError(403, "root required")
        user_id = message.get("user_id")
        database = message.get("database")
        optic = message.get("optic")
        if not isinstance(user_id, str) or not isinstance(database, str) or not isinstance(optic, str):
            raise OpError(422, "user_id, database, and optic required")
        return users_optic_revoke(user_id, database, optic)

    if op == "databases.list":
        return databases_list(user)

    if op == "databases.settings.get":
        database = message.get("database")
        if not isinstance(database, str):
            raise OpError(422, "database required")
        return database_settings_get(user, database)

    if op == "databases.settings.patch":
        database = message.get("database")
        semantics = message.get("semantics")
        code_root = message.get("code_root")
        if not isinstance(database, str):
            raise OpError(422, "database required")
        if semantics is not None and not isinstance(semantics, bool):
            raise OpError(422, "semantics must be a boolean")
        if code_root is not None and not isinstance(code_root, str):
            raise OpError(422, "code_root must be a string")
        return database_settings_patch(
            user,
            database,
            semantics=semantics,
            code_root=code_root,
            patch_code_root="code_root" in message,
        )

    if op == "databases.create":
        if not user.is_root:
            raise OpError(403, "root required")
        name = message.get("name")
        if not isinstance(name, str):
            raise OpError(422, "name required")
        return databases_create(name)

    if op == "databases.drop":
        database = message.get("database")
        if not isinstance(database, str):
            raise OpError(422, "database required")
        return databases_drop(user, database)

    if op == "parse":
        database = message.get("database")
        query = message.get("query")
        if not isinstance(database, str) or not isinstance(query, str):
            raise OpError(422, "database and query required")
        return wql_parse(user, database, query)

    if op == "payload":
        database = message.get("database")
        slug = message.get("slug")
        if not isinstance(database, str) or not isinstance(slug, str):
            raise OpError(422, "database and slug required")
        return node_payload(user, database, slug)

    raise OpError(422, f"unknown op: {op}")


@ws_router.websocket("/ws")
async def websocket_api(websocket: WebSocket) -> None:
    await websocket.accept()
    store = get_session_store()
    user: User | None = None
    token = _bearer_from_headers(websocket)
    if token:
        user = store.authenticate(token)

    active_execute_id: str | None = None
    cancel_flags: dict[str, bool] = {}

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "invalid JSON"})
                continue
            if not isinstance(message, dict):
                await _send(websocket, {"type": "error", "message": "message must be a JSON object"})
                continue

            msg_id = message.get("id")
            op = message.get("op")
            if not isinstance(op, str):
                await _send(websocket, {"type": "error", "id": msg_id, "message": "missing op"})
                continue

            if op == "cancel":
                if isinstance(msg_id, str):
                    cancel_flags[msg_id] = True
                await _send(websocket, {"type": "cancelled", "id": msg_id})
                continue

            if op == "auth":
                tok = message.get("token")
                if not isinstance(tok, str):
                    await _send(websocket, {"type": "error", "id": msg_id, "message": "token required"})
                    continue
                user = store.authenticate(tok)
                if user is None:
                    await _send(
                        websocket,
                        {"type": "error", "id": msg_id, "status": 401, "message": "invalid session"},
                    )
                else:
                    await _send(websocket, {"type": "ok", "id": msg_id, "user": user.public_dict()})
                continue

            if op == "login":
                username = message.get("username")
                password = message.get("password")
                if not isinstance(username, str) or not isinstance(password, str):
                    await _send(
                        websocket,
                        {"type": "error", "id": msg_id, "message": "username and password required"},
                    )
                    continue
                try:
                    payload = await asyncio.to_thread(op_login, username, password)
                except OpError as exc:
                    await _send(websocket, _error_frame(msg_id, exc))
                    continue
                user = store.authenticate(payload["token"])
                await _send(websocket, {"type": "ok", "id": msg_id, **payload})
                continue

            if user is None:
                await _send(
                    websocket,
                    {"type": "error", "id": msg_id, "status": 401, "message": "authentication required"},
                )
                continue

            if op == "logout":
                try:
                    result = await asyncio.to_thread(op_logout, user)
                except OpError as exc:
                    await _send(websocket, _error_frame(msg_id, exc))
                    continue
                user = None
                await _send(websocket, {"type": "ok", "id": msg_id, **result})
                continue

            if op == "execute":
                if active_execute_id is not None:
                    await _send(
                        websocket,
                        {
                            "type": "error",
                            "id": msg_id,
                            "status": 409,
                            "message": "another execute is in progress",
                        },
                    )
                    continue
                database = message.get("database")
                query = message.get("query")
                if not isinstance(database, str) or not isinstance(query, str):
                    await _send(
                        websocket,
                        {"type": "error", "id": msg_id, "status": 422, "message": "database and query required"},
                    )
                    continue
                if not isinstance(msg_id, str):
                    await _send(
                        websocket,
                        {"type": "error", "id": msg_id, "status": 422, "message": "execute requires string id"},
                    )
                    continue
                active_execute_id = msg_id
                try:
                    await _handle_execute(websocket, user, msg_id, database, query, cancel_flags)
                finally:
                    active_execute_id = None
                continue

            try:
                result = _dispatch_op(op, message, user)
            except OpError as exc:
                await _send(websocket, _error_frame(msg_id, exc))
                continue

            await _send(websocket, {"type": "ok", "id": msg_id, **result})

    except WebSocketDisconnect:
        return
