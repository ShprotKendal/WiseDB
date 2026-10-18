"""Serialize WQL WHERE expressions to catalog filter JSON for the C engine."""

from __future__ import annotations

import json
from typing import Any

from wisedb.wql.ast import BinaryExpr, Compare, NotExpr
from wisedb.wql.errors import WqlError


def expr_to_json(expr: Compare | BinaryExpr | NotExpr | None) -> str | None:
	if expr is None:
		return None
	return json.dumps(_expr_to_dict(expr), separators=(",", ":"))


def _expr_to_dict(expr: Compare | BinaryExpr | NotExpr) -> dict[str, Any]:
	if isinstance(expr, Compare):
		return {"cmp": {"field": expr.field, "op": expr.operator, "value": expr.value}}
	if isinstance(expr, BinaryExpr):
		key = "and" if expr.operator == "AND" else "or"
		return {key: [_expr_to_dict(expr.left), _expr_to_dict(expr.right)]}
	if isinstance(expr, NotExpr):
		return {"not": _expr_to_dict(expr.expr)}
	raise WqlError("unsupported expression")
