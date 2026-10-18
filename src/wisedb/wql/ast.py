"""WQL abstract syntax tree."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Union


class Direction(str, Enum):
	OUTGOING = "outgoing"
	INCOMING = "incoming"
	BOTH = "both"


@dataclass(frozen=True, slots=True)
class LinksClause:
	depth: int
	direction: Direction


@dataclass(frozen=True, slots=True)
class OrderBy:
	field: str
	ascending: bool = True


@dataclass(frozen=True, slots=True)
class Compare:
	field: str
	operator: str
	value: LiteralValue


@dataclass(frozen=True, slots=True)
class NotExpr:
	expr: "Expr"


@dataclass(frozen=True, slots=True)
class BinaryExpr:
	left: "Expr"
	operator: Literal["AND", "OR"]
	right: "Expr"


@dataclass(frozen=True, slots=True)
class ParamRef:
	field: str


LiteralValue = Union[str, int, float, bool, None, ParamRef]

Expr = Union[Compare, BinaryExpr, NotExpr]


@dataclass(frozen=True, slots=True)
class SelectStmt:
	table: Literal["pages", "edges", "layout", "optics"]
	projection: Literal["*", "markdown"]
	where: Expr | None
	neighbors: bool
	links: LinksClause | None
	order_by: OrderBy | None
	limit: int | None


@dataclass(frozen=True, slots=True)
class InsertStmt:
	columns: tuple[str, ...]
	values: tuple[LiteralValue, ...]


@dataclass(frozen=True, slots=True)
class UpdateStmt:
	assignments: dict[str, LiteralValue]
	where_slug: str | ParamRef
	full_markdown: bool


@dataclass(frozen=True, slots=True)
class DeleteStmt:
	slug: str | ParamRef


@dataclass(frozen=True, slots=True)
class CreateOpticStmt:
	name: str
	description: str
	kind: str = "markdown"


@dataclass(frozen=True, slots=True)
class DropOpticStmt:
	name: str


@dataclass(frozen=True, slots=True)
class UpdateOpticsStmt:
	text: str | ParamRef
	slug: str | ParamRef
	name: str | ParamRef


@dataclass(frozen=True, slots=True)
class DeleteOpticsStmt:
	slug: str | ParamRef
	name: str | ParamRef


@dataclass(frozen=True, slots=True)
class SearchStmt:
	query: str
	top_k: int
	budget_tokens: int


@dataclass(frozen=True, slots=True)
class SchemaStmt:
	pass


@dataclass(frozen=True, slots=True)
class CreateFolderStmt:
	path: str


@dataclass(frozen=True, slots=True)
class DropFolderStmt:
	path: str


@dataclass(frozen=True, slots=True)
class MoveFolderStmt:
	source: str
	destination: str


@dataclass(frozen=True, slots=True)
class MovePageStmt:
	slug: str
	destination: str


@dataclass(frozen=True, slots=True)
class StreamStmt:
	outer: Stmt
	inner: Stmt


Stmt = Union[
	SelectStmt,
	InsertStmt,
	UpdateStmt,
	DeleteStmt,
	CreateOpticStmt,
	DropOpticStmt,
	UpdateOpticsStmt,
	DeleteOpticsStmt,
	SearchStmt,
	SchemaStmt,
	CreateFolderStmt,
	DropFolderStmt,
	MoveFolderStmt,
	MovePageStmt,
	StreamStmt,
]
