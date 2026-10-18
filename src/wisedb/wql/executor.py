"""Execute parsed WQL against a Wiki instance."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from typing import Any

from wisedb.memory import Wiki, schema
from wisedb.optics import OpticsConflictError, OpticsError
from wisedb.server.engine.layout import (
	LayoutError,
	create_folder,
	database_tree,
	delete_folder,
	move_folder,
	move_page,
)
from wisedb.wql.ast import (
	BinaryExpr,
	Compare,
	CreateFolderStmt,
	CreateOpticStmt,
	DeleteOpticsStmt,
	DeleteStmt,
	Direction,
	DropFolderStmt,
	DropOpticStmt,
	InsertStmt,
	MoveFolderStmt,
	MovePageStmt,
	NotExpr,
	SchemaStmt,
	SearchStmt,
	SelectStmt,
	StreamStmt,
	Stmt,
	UpdateOpticsStmt,
	UpdateStmt,
)
from wisedb.wql.errors import WqlError
from wisedb.wql.parser import edge_and_compares, optics_filters_from_where, parse
from wisedb.wql.predicate_json import expr_to_json
from wisedb.wql.substitute import substitute_stmt


def execute(wiki: Wiki, source: str) -> dict[str, Any]:
	stmt = parse(source)
	if isinstance(stmt, StreamStmt):
		raise WqlError("WS[] is only supported over WebSocket")
	return _execute_stmt(wiki, stmt)


def iter_stream(wiki: Wiki, source: str) -> Iterator[tuple[int, dict[str, Any], dict[str, Any]]]:
	"""Run outer query once, then inner WQL per row with $.field substitution."""
	stmt = parse(source)
	if not isinstance(stmt, StreamStmt):
		raise WqlError("expected WS[] stream query")
	rows = _outer_stream_rows(wiki, stmt.outer)
	for index, row in enumerate(rows):
		inner = substitute_stmt(stmt.inner, row)
		with wiki.serialized():
			result = _execute_stmt(wiki, inner)
		yield index, row, result


def _outer_stream_rows(wiki: Wiki, outer: Stmt) -> list[dict[str, Any]]:
	with wiki.serialized():
		result = _execute_stmt(wiki, outer)
	return _rows_from_outer_result(result, outer)


def _rows_from_outer_result(result: dict[str, Any], outer: Stmt) -> list[dict[str, Any]]:
	if isinstance(outer, SearchStmt):
		hits = result.get("hits")
		if not isinstance(hits, list):
			raise WqlError("SEARCH did not return hits")
		return [h for h in hits if isinstance(h, dict)]

	if isinstance(outer, SelectStmt):
		if outer.table == "layout":
			pages = result.get("pages")
			if not isinstance(pages, list):
				raise WqlError("layout query did not return pages")
			return [p for p in pages if isinstance(p, dict)]
		if outer.table == "edges":
			edges = result.get("edges")
			if not isinstance(edges, list):
				raise WqlError("edges query did not return edges")
			return [e for e in edges if isinstance(e, dict)]
		if outer.links:
			rows: list[dict[str, Any]] = []
			levels = result.get("levels")
			if not isinstance(levels, list):
				raise WqlError("LINKS did not return levels")
			for depth, level in enumerate(levels, start=1):
				if not isinstance(level, list):
					continue
				for node in level:
					if isinstance(node, dict):
						row = dict(node)
						row["depth"] = depth
						rows.append(row)
			return rows
		if outer.where and _is_slug_read(outer.where) and not outer.neighbors:
			node = result.get("node")
			if isinstance(node, dict):
				return [node]
			return [result]
		if outer.neighbors:
			node = result.get("node")
			if isinstance(node, dict):
				return [node]
			return [result]
		pages = result.get("pages")
		if isinstance(pages, list):
			return [p for p in pages if isinstance(p, dict)]
		raise WqlError("outer query did not produce a row set")

	raise WqlError("WS[] outer query must produce a row set")


def _execute_stmt(wiki: Wiki, stmt: Stmt) -> dict[str, Any]:
	if isinstance(stmt, StreamStmt):
		raise WqlError("WS[] is only supported over WebSocket")
	if isinstance(stmt, SchemaStmt):
		return {"schema": schema()}
	if isinstance(stmt, SearchStmt):
		return wiki.search(stmt.query, budget_tokens=stmt.budget_tokens, top_k=stmt.top_k)
	if isinstance(stmt, InsertStmt):
		return _execute_insert(wiki, stmt)
	if isinstance(stmt, UpdateStmt):
		return _execute_update(wiki, stmt)
	if isinstance(stmt, DeleteStmt):
		with wiki.serialized():
			wiki.delete(stmt.slug)
		return {"status": "ok"}
	if isinstance(stmt, CreateOpticStmt):
		with wiki.serialized():
			try:
				record = wiki.create_optic(stmt.name, description=stmt.description, kind=stmt.kind)
			except OpticsError as exc:
				raise WqlError(str(exc)) from exc
		return {"optic": record, "status": "ok"}
	if isinstance(stmt, DropOpticStmt):
		with wiki.serialized():
			try:
				wiki.drop_optic(stmt.name)
			except OpticsError as exc:
				raise WqlError(str(exc)) from exc
		return {"status": "ok"}
	if isinstance(stmt, UpdateOpticsStmt):
		return _execute_update_optics(wiki, stmt)
	if isinstance(stmt, DeleteOpticsStmt):
		return _execute_delete_optics(wiki, stmt)
	if isinstance(stmt, SelectStmt):
		return _execute_select(wiki, stmt)
	if isinstance(stmt, CreateFolderStmt):
		with wiki.serialized():
			return _layout_op(create_folder, wiki, stmt.path)
	if isinstance(stmt, DropFolderStmt):
		with wiki.serialized():
			return _layout_op(delete_folder, wiki, stmt.path)
	if isinstance(stmt, MoveFolderStmt):
		with wiki.serialized():
			return _layout_op(move_folder, wiki, stmt.source, stmt.destination)
	if isinstance(stmt, MovePageStmt):
		with wiki.serialized():
			return _layout_op(move_page, wiki, stmt.slug, stmt.destination)
	raise WqlError("unsupported statement")


def _layout_op(fn, wiki: Wiki, *args: str) -> dict[str, Any]:
	try:
		return fn(wiki, *args)
	except LayoutError as exc:
		raise WqlError(str(exc)) from exc


def _execute_insert(wiki: Wiki, stmt: InsertStmt) -> dict[str, Any]:
	data = dict(zip(stmt.columns, stmt.values, strict=True))
	slug = data.get("slug")
	markdown = data.get("markdown")
	rel_path = data.get("rel_path")
	file_bytes = None
	if "file_base64" in data:
		raw = data["file_base64"]
		if not isinstance(raw, str):
			raise WqlError("file_base64 must be a string")
		try:
			file_bytes = base64.b64decode(raw, validate=True)
		except (binascii.Error, ValueError) as exc:
			raise WqlError("file_base64 is invalid") from exc
		if len(file_bytes) > 25 * 1024 * 1024:
			raise WqlError("media file exceeds 25 MiB")
	if not isinstance(slug, str):
		raise WqlError("slug must be a string")
	if markdown is not None and not isinstance(markdown, str):
		raise WqlError("markdown must be a string")
	if rel_path is not None and not isinstance(rel_path, str):
		raise WqlError("rel_path must be a string")
	with wiki.serialized():
		return wiki.remember(slug, markdown=markdown, file_bytes=file_bytes, rel_path=rel_path)


def _execute_update(wiki: Wiki, stmt: UpdateStmt) -> dict[str, Any]:
	with wiki.serialized():
		if stmt.full_markdown:
			markdown = stmt.assignments["markdown"]
			if not isinstance(markdown, str):
				raise WqlError("markdown must be a string")
			return wiki.edit(stmt.where_slug, markdown)
		return wiki.update(
			stmt.where_slug,
			type=stmt.assignments.get("type"),
			title=stmt.assignments.get("title"),
			summary=stmt.assignments.get("summary"),
			body=stmt.assignments.get("body"),
		)


def _resolve_str(value: str | Any, label: str) -> str:
	if not isinstance(value, str):
		raise WqlError(f"{label} must be a string")
	return value


def _execute_update_optics(wiki: Wiki, stmt: UpdateOpticsStmt) -> dict[str, Any]:
	slug = _resolve_str(stmt.slug, "slug")
	name = _resolve_str(stmt.name, "name")
	text = _resolve_str(stmt.text, "text")
	with wiki.serialized():
		try:
			wiki.read(slug)
		except Exception as exc:
			message = str(exc)
			if "not found" in message.lower():
				raise WqlError("page not found") from exc
			raise WqlError(message) from exc
		try:
			wiki.set_optic_text(name, slug, text)
		except OpticsConflictError as exc:
			raise WqlError(str(exc)) from exc
		except OpticsError as exc:
			raise WqlError(str(exc)) from exc
	return {"status": "ok"}


def _execute_delete_optics(wiki: Wiki, stmt: DeleteOpticsStmt) -> dict[str, Any]:
	slug = _resolve_str(stmt.slug, "slug")
	name = _resolve_str(stmt.name, "name")
	with wiki.serialized():
		try:
			wiki.read(slug)
		except Exception as exc:
			message = str(exc)
			if "not found" in message.lower():
				raise WqlError("page not found") from exc
			raise WqlError(message) from exc
		try:
			wiki.clear_optic_text(name, slug)
		except OpticsError as exc:
			raise WqlError(str(exc)) from exc
	return {"status": "ok"}


def _execute_select(wiki: Wiki, stmt: SelectStmt) -> dict[str, Any]:
	if stmt.table == "optics":
		slug_filter, name_filter = optics_filters_from_where(stmt.where)
		with wiki.serialized():
			rows = wiki.list_optic_texts(name=name_filter, slug=slug_filter)
		return {"rows": rows}
	if stmt.table == "layout":
		return database_tree(wiki)
	if stmt.projection == "markdown":
		slug = _slug_from_where(stmt.where)
		if slug is None:
			raise WqlError("SELECT markdown requires WHERE slug = '...'")
		return {"markdown": wiki.markdown(slug)}
	if stmt.table == "edges":
		return _execute_edge_select(wiki, stmt)
	if stmt.links:
		direction = {
			Direction.OUTGOING: "outgoing",
			Direction.INCOMING: "incoming",
			Direction.BOTH: "both",
		}[stmt.links.direction]
		slug = _slug_from_where(stmt.where)
		assert slug is not None
		return wiki.links(slug, depth=stmt.links.depth, direction=direction)
	if stmt.where and _is_slug_read(stmt.where):
		slug = _slug_from_where(stmt.where)
		assert slug is not None
		return wiki.read(slug, neighbors=stmt.neighbors)
	filter_json = expr_to_json(stmt.where)
	order_field = stmt.order_by.field if stmt.order_by else None
	order_asc = stmt.order_by.ascending if stmt.order_by else True
	limit = stmt.limit
	return wiki.list_catalog(
		filter_json=filter_json,
		order_field=order_field,
		order_asc=order_asc,
		limit=limit,
	)


def _execute_edge_select(wiki: Wiki, stmt: SelectStmt) -> dict[str, Any]:
	assert stmt.where is not None
	from_slug = to_slug = edge_type = None
	for compare in edge_and_compares(stmt.where):
		if compare.field == "from_slug" and compare.operator == "=" and isinstance(compare.value, str):
			from_slug = compare.value
		elif compare.field == "to_slug" and compare.operator == "=" and isinstance(compare.value, str):
			to_slug = compare.value
		elif compare.field == "type" and compare.operator == "=" and isinstance(compare.value, str):
			edge_type = compare.value
	return wiki.query_edges(from_slug=from_slug, to_slug=to_slug, edge_type=edge_type)


def _slug_from_where(expr: Compare | BinaryExpr | NotExpr | None) -> str | None:
	if isinstance(expr, Compare) and expr.field == "slug" and expr.operator == "=":
		return expr.value if isinstance(expr.value, str) else None
	return None


def _is_slug_read(expr: Compare | BinaryExpr | NotExpr) -> bool:
	return isinstance(expr, Compare) and expr.field == "slug" and expr.operator == "="
