"""Pydantic schemas for HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WqlExecuteRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"query": "SCHEMA"}],
        }
    )

    query: str = Field(
        ...,
        min_length=1,
        max_length=64 * 1024,
        description="WQL statement to run against the target database.",
        examples=["SCHEMA", "SELECT * FROM pages WHERE slug = 'redis'"],
    )


class WqlParseResponse(BaseModel):
    statement: str = Field(
        description="Parsed statement kind, such as `select`, `insert`, or `create_folder`.",
        examples=["select"],
    )
    privilege: str = Field(
        description="Privilege required to run the statement: `read` or `write`.",
        examples=["read"],
    )
    allowed: bool = Field(description="Whether the caller holds that privilege on this database.")
    streaming: bool = Field(
        default=False,
        description="True when the statement uses WS[] (WebSocket streaming only).",
    )


class WqlBatchRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "queries": [
                        "SCHEMA",
                        "SELECT * FROM pages WHERE slug = 'redis'",
                    ]
                }
            ],
        }
    )

    queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=32,
        description="WQL statements to run in order, at most 32. Each statement is at most 64 KiB.",
    )

    @field_validator("queries")
    @classmethod
    def _bounded_statements(cls, queries: list[str]) -> list[str]:
        for index, query in enumerate(queries, start=1):
            if not query or len(query) > 64 * 1024:
                raise ValueError(f"query {index} must be 1 to 65536 characters")
        return queries


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"username": "root", "password": "your-root-password"}],
        }
    )

    username: str = Field(..., min_length=1, max_length=128, description="Catalog username or `root`.")
    password: str = Field(..., min_length=1, max_length=256, description="Account password.")


class LoginResponse(BaseModel):
    token: str = Field(description="Bearer token for the `Authorization` header.")
    session_id: str = Field(description="Opaque session identifier.")
    user: dict[str, str | bool] = Field(description="Public profile of the authenticated user.")


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"username": "agent", "password": "long-random-password"}],
        }
    )

    username: str = Field(..., min_length=1, max_length=128, description="Unique catalog username.")
    password: str = Field(..., min_length=8, max_length=256, description="Initial password (min 8 characters).")


class SetPasswordRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"password": "new-long-random-password"}],
        }
    )

    password: str = Field(..., min_length=8, max_length=256, description="New password (min 8 characters).")


class GrantRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"privileges": ["read"]}],
        }
    )

    privileges: list[str] = Field(
        ...,
        min_length=1,
        description="Database privileges to assign: `read`, `write`, and/or `admin`.",
        examples=[["read"], ["read", "write"]],
    )


class GrantRecord(BaseModel):
    database: str = Field(description="Database name.")
    privileges: list[str] = Field(description="Granted privileges on that database.")


class UserResponse(BaseModel):
    id: str = Field(description="Stable user identifier.")
    username: str
    created_at: str = Field(description="ISO timestamp when the user was created.")
    disabled: bool = Field(description="Whether login is blocked for this user.")
    grants: list[GrantRecord] = Field(default_factory=list, description="Per-database privilege grants.")


class CreateDatabaseRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"name": "my-agent"}],
        }
    )

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Alphanumeric database name (may include `_` and `-`).",
        examples=["my-agent"],
    )


class DatabaseListResponse(BaseModel):
    databases: list[str] = Field(description="Names visible to the caller.")


class StatusResponse(BaseModel):
    status: str = Field(description="Operation outcome, typically `ok`.")


class CreateDatabaseResponse(BaseModel):
    name: str
    status: str


class GrantSetResponse(BaseModel):
    user_id: str
    database: str
    privileges: list[str]


class OpticGrantRequest(BaseModel):
    privileges: list[str] = Field(
        default_factory=list,
        description="Optic privileges: `view`, `edit`, and/or `delete`. Empty list denies all optic access.",
        examples=[["view", "edit"]],
    )


class OpticGrantSetResponse(BaseModel):
    user_id: str
    database: str
    optic: str
    privileges: list[str]


class OpticCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Custom optic name (lowercase).")
    description: str = Field(default="", max_length=4096, description="Optional human-readable description.")
    kind: str = Field(
        default="markdown",
        description="Immutable data type: markdown, json, image, video, link, file, or span.",
    )


class OpticPatchRequest(BaseModel):
    description: str = Field(..., max_length=4096, description="Updated optic description.")


class OpticRecordResponse(BaseModel):
    name: str
    description: str
    kind: str = Field(default="markdown", description="Immutable optic data type.")
    privileges: list[str] = Field(description="Effective optic privileges for the caller.")
    builtin: bool = Field(default=False, description="True for the built-in `content` optic.")


class OpticListResponse(BaseModel):
    optics: list[OpticRecordResponse]


class OpticTextResponse(BaseModel):
    slug: str
    optic: str
    kind: str = Field(default="markdown")
    text: str = Field(default="")
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    sha256: str | None = None
    bound: bool | None = None
    has_bytes: bool | None = None
    filename: str | None = None
    media_type: str | None = None
    size: int | None = None


class OpticTextPutRequest(BaseModel):
    text: str | None = Field(default=None, description="UTF-8 text for markdown, json, link, or span edit.")
    path: str | None = Field(default=None, description="Relative file path for span bind.")
    start_line: int | None = Field(default=None, description="Inclusive start line for span bind.")
    end_line: int | None = Field(default=None, description="Inclusive end line for span bind.")
    base_sha256: str | None = Field(default=None, description="Fragment hash from last span read for edits.")


class NodeCreateRequest(BaseModel):
    slug: str = Field(..., min_length=1, max_length=256, description="Canonical node slug.")
    markdown: str | None = Field(default=None, description="WikiLLM markdown document (required unless file_base64).")
    rel_path: str | None = Field(default=None, description="Wiki-relative file path for the stored page or media.")
    file_base64: str | None = Field(default=None, description="Base64-encoded media bytes (max 25 MiB decoded).")


class NodePatchRequest(BaseModel):
    type: str | None = Field(default=None, description="WikiLLM page type.")
    title: str | None = None
    summary: str | None = None
    body: str | None = Field(default=None, description="Markdown body without frontmatter.")


class NodePutRequest(BaseModel):
    markdown: str = Field(..., min_length=1, description="Full WikiLLM document replacing the node.")


class DatabaseSettingsResponse(BaseModel):
    semantics: bool = Field(
        description="When true, `{{page title}}` in titles and bodies creates `links_to` edges.",
    )
    code_root: str | None = Field(
        default=None,
        description="Absolute checkout path for span optics on this machine.",
    )


class DatabaseSettingsPatchRequest(BaseModel):
    semantics: bool | None = Field(
        default=None,
        description="Enable or disable semantic title mentions as automatic graph links.",
    )
    code_root: str | None = Field(
        default=None,
        description="Absolute checkout path for span optics; null clears the binding.",
    )


class FolderPathRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Wiki-relative folder path.")


class NodeMoveRequest(BaseModel):
    destination: str = Field(..., min_length=1, description="Wiki-relative destination path for the page file.")


class FolderMoveRequest(BaseModel):
    source: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)


def wql_execute_response_examples() -> dict[str, Any]:
    return {
        200: {
            "description": (
                "WQL result. Shape depends on the statement: `SCHEMA` returns `schema`; "
                "mutations often return `status`; `SELECT` and `SEARCH` return row or hit data."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "schema": {
                            "summary": "SCHEMA",
                            "value": {"schema": "# WiseDB WQL\n..."},
                        },
                        "mutation": {
                            "summary": "INSERT / UPDATE / DELETE",
                            "value": {"status": "ok"},
                        },
                    }
                }
            },
        },
        401: {"description": "Missing, invalid, or expired session."},
        403: {"description": "No database access or insufficient privilege for the WQL statement."},
        404: {"description": "Database not found."},
        422: {"description": "Invalid database name or WQL syntax/semantics."},
    }
