"""Substitute $.field references in inner WS[] statements."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from wisedb.wql.ast import (
	BinaryExpr,
	Compare,
	DeleteStmt,
	Expr,
	InsertStmt,
	LiteralValue,
	NotExpr,
	ParamRef,
	SelectStmt,
	Stmt,
	UpdateStmt,
)
from wisedb.wql.errors import WqlError


def resolve_literal(value: LiteralValue, row: dict[str, Any]) -> Any:
	if isinstance(value, ParamRef):
		if value.field not in row:
			raise WqlError(f"missing stream row field: {value.field}")
		return row[value.field]
	return value


def substitute_expr(expr: Expr | None, row: dict[str, Any]) -> Expr | None:
	if expr is None:
		return None
	if isinstance(expr, Compare):
		return Compare(
			field=expr.field,
			operator=expr.operator,
			value=resolve_literal(expr.value, row),
		)
	if isinstance(expr, NotExpr):
		return NotExpr(expr=substitute_expr(expr.expr, row))  # type: ignore[arg-type]
	if isinstance(expr, BinaryExpr):
		return BinaryExpr(
			left=substitute_expr(expr.left, row),  # type: ignore[arg-type]
			operator=expr.operator,
			right=substitute_expr(expr.right, row),  # type: ignore[arg-type]
		)
	raise WqlError("unsupported expression")


def substitute_stmt(stmt: Stmt, row: dict[str, Any]) -> Stmt:
	if isinstance(stmt, SelectStmt):
		return replace(stmt, where=substitute_expr(stmt.where, row))
	if isinstance(stmt, InsertStmt):
		return InsertStmt(
			columns=stmt.columns,
			values=tuple(resolve_literal(v, row) for v in stmt.values),
		)
	if isinstance(stmt, UpdateStmt):
		slug = stmt.where_slug
		if isinstance(slug, ParamRef):
			slug = resolve_literal(slug, row)
		if not isinstance(slug, str):
			raise WqlError("slug must be a string")
		return UpdateStmt(
			assignments={k: resolve_literal(v, row) for k, v in stmt.assignments.items()},
			where_slug=slug,
			full_markdown=stmt.full_markdown,
		)
	if isinstance(stmt, DeleteStmt):
		slug = stmt.slug
		if isinstance(slug, ParamRef):
			slug = resolve_literal(slug, row)
		if not isinstance(slug, str):
			raise WqlError("slug must be a string")
		return DeleteStmt(slug=slug)
	return stmt
