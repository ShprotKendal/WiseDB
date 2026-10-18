"""In-process bearer sessions backed by the catalog."""

from __future__ import annotations

import hmac
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from wisedb.server.catalog import Catalog, get_catalog
from wisedb.server.security import hash_bearer_token
from wisedb.server.users import User


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SessionRecord:
    id: str
    token_hash: str
    user_id: str
    revoked: bool = False


@dataclass
class SessionStore:
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _sessions: dict[str, SessionRecord] = field(default_factory=dict)
    _token_index: dict[str, str] = field(default_factory=dict)
    _root_password: str = ""
    _ready: bool = False

    def configure(self, root_password: str, catalog: Catalog) -> None:
        with self._lock:
            self._sessions.clear()
            self._token_index.clear()
            self._root_password = root_password
            self._ready = bool(root_password)
            catalog  # catalog opened separately; sessions stay in memory only

    @property
    def is_configured(self) -> bool:
        with self._lock:
            return self._ready

    def _root_user(self) -> User:
        return User(id="root", username="root", is_root=True, created_at=_utc_now_iso())

    def _user_from_catalog(self, stored_id: str) -> User | None:
        stored = get_catalog().get_user(stored_id)
        if stored is None:
            return None
        return User(
            id=stored.id,
            username=stored.username,
            is_root=False,
            created_at=stored.created_at,
            disabled=stored.disabled,
        )

    def login(self, username: str, password: str) -> tuple[str, User, str]:
        username = username.strip()
        if not username or not password:
            raise ValueError("username and password are required")
        with self._lock:
            if not self._ready:
                raise RuntimeError("authentication is not configured")
            if username == "root":
                if not self._root_password or not hmac.compare_digest(password, self._root_password):
                    raise PermissionError("invalid credentials")
                user = self._root_user()
            else:
                stored = get_catalog().verify_user_password(username, password)
                if stored is None:
                    raise PermissionError("invalid credentials")
                user = User(
                    id=stored.id,
                    username=stored.username,
                    is_root=False,
                    created_at=stored.created_at,
                    disabled=stored.disabled,
                )
            if user.disabled:
                raise PermissionError("user is disabled")
            return self._issue_session(user)

    def _issue_session(self, user: User) -> tuple[str, User, str]:
        token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        record = SessionRecord(id=session_id, token_hash=hash_bearer_token(token), user_id=user.id)
        self._sessions[session_id] = record
        self._token_index[record.token_hash] = session_id
        return token, user, session_id

    def authenticate(self, token: str | None) -> User | None:
        if not token:
            return None
        digest = hash_bearer_token(token)
        with self._lock:
            if not self._ready:
                return None
            session_id = self._token_index.get(digest)
            if session_id is None:
                return None
            record = self._sessions.get(session_id)
            if record is None or record.revoked:
                return None
            if record.user_id == "root":
                return self._root_user()
            return self._user_from_catalog(record.user_id)

    def logout(self, actor: User, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                session_id = self._session_id_for_actor(actor)
                if session_id is None:
                    raise KeyError("session not found")
            record = self._sessions.get(session_id)
            if record is None:
                raise KeyError("session not found")
            if not actor.is_root and record.user_id != actor.id:
                raise PermissionError("cannot revoke another user's session")
            record.revoked = True
            self._token_index.pop(record.token_hash, None)

    def _session_id_for_actor(self, actor: User) -> str | None:
        for session_id, record in self._sessions.items():
            if not record.revoked and record.user_id == actor.id:
                return session_id
        return None


_store = SessionStore()


def get_session_store() -> SessionStore:
    return _store


def reset_session_store() -> None:
    with _store._lock:
        _store._sessions.clear()
        _store._token_index.clear()
        _store._root_password = ""
        _store._ready = False
