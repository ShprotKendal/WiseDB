"""Recursive-descent parser for WQL."""

from __future__ import annotations

from typing import Any, Literal

from wisedb.optics import WQL_KIND_KEYWORDS
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
	LinksClause,
	LiteralValue,
	MoveFolderStmt,
	MovePageStmt,
	NotExpr,
	OrderBy,
	ParamRef,
	SchemaStmt,
	SearchStmt,
	SelectStmt,
	StreamStmt,
	Stmt,
	UpdateOpticsStmt,
	UpdateStmt,
)
from wisedb.wql.errors import WqlError
from wisedb.wql.lexer import LexerError, Token, TokenType, tokenize

_CATALOG_FIELDS = frozenset({"slug", "type", "title", "summary", "rel_path", "is_media", "token_count"})
_INSERT_COLUMNS = frozenset({"slug", "markdown", "rel_path", "file_base64"})
_UPDATE_FIELDS = frozenset({"type", "title", "summary", "body", "markdown"})


class Parser:
	def __init__(self, tokens: list[Token], *, in_ws_inner: bool = False) -> None:
		self._tokens = tokens
		self._pos = 0
		self._in_ws_inner = in_ws_inner

	def parse(self) -> Stmt:
		stmt = self._statement()
		if self._match_keyword("WS"):
			if self._in_ws_inner:
				token = self._current()
				raise WqlError("nested WS[] is not allowed", token.line, token.column)
			self._expect_punct("[")
			inner_parser = Parser(self._tokens, in_ws_inner=True)
			inner_parser._pos = self._pos
			inner = inner_parser._statement()
			self._pos = inner_parser._pos
			self._expect_punct("]")
			self._expect(TokenType.EOF, "end of query")
			stream = StreamStmt(outer=stmt, inner=inner)
			_validate(stream)
			return stream
		self._expect(TokenType.EOF, "end of query")
		return stmt

	def _current(self) -> Token:
		return self._tokens[self._pos]

	def _advance(self) -> Token:
		token = self._current()
		self._pos += 1
		return token

	def _match_keyword(self, word: str) -> bool:
		token = self._current()
		if token.type == TokenType.KEYWORD and token.value.upper() == word:
			self._advance()
			return True
		return False

	def _expect_keyword(self, word: str) -> Token:
		token = self._current()
		if token.type != TokenType.KEYWORD or token.value.upper() != word:
			raise WqlError(f"expected {word}", token.line, token.column)
		return self._advance()

	def _expect(self, kind: TokenType, message: str) -> Token:
		token = self._current()
		if token.type != kind:
			raise WqlError(f"expected {message}", token.line, token.column)
		return self._advance()

	def _expect_punct(self, char: str) -> Token:
		token = self._current()
		if token.type != TokenType.PUNCTUATION or token.value != char:
			raise WqlError(f"expected '{char}'", token.line, token.column)
		return self._advance()

	def _statement(self) -> Stmt:
		if self._match_keyword("SEARCH"):
			return self._search()
		if self._match_keyword("SCHEMA"):
			return SchemaStmt()
		if self._match_keyword("CREATE"):
			if self._match_keyword("OPTIC"):
				name = self._optic_name_value()
				kind = "markdown"
				token = self._current()
				if token.type == TokenType.KEYWORD:
					upper = token.value.upper()
					if upper in WQL_KIND_KEYWORDS:
						kind = WQL_KIND_KEYWORDS[upper].value
						self._advance()
				description = ""
				if self._current().type == TokenType.STRING:
					description_value = self._literal_value()
					if not isinstance(description_value, str):
						token = self._current()
						raise WqlError("CREATE OPTIC description must be a string", token.line, token.column)
					description = description_value
				return CreateOpticStmt(name=name, description=description, kind=kind)
			self._expect_keyword("FOLDER")
			path = self._literal_value()
			if not isinstance(path, str):
				token = self._current()
				raise WqlError("CREATE FOLDER requires a path string", token.line, token.column)
			return CreateFolderStmt(path=path)
		if self._match_keyword("DROP"):
			if self._match_keyword("OPTIC"):
				return DropOpticStmt(name=self._optic_name_value())
			self._expect_keyword("FOLDER")
			path = self._literal_value()
			if not isinstance(path, str):
				token = self._current()
				raise WqlError("DROP FOLDER requires a path string", token.line, token.column)
			return DropFolderStmt(path=path)
		if self._match_keyword("MOVE"):
			if self._match_keyword("FOLDER"):
				source = self._literal_value()
				if not isinstance(source, str):
					token = self._current()
					raise WqlError("MOVE FOLDER requires a source path string", token.line, token.column)
				self._expect_keyword("TO")
				destination = self._literal_value()
				if not isinstance(destination, str):
					token = self._current()
					raise WqlError("MOVE FOLDER requires a destination path string", token.line, token.column)
				return MoveFolderStmt(source=source, destination=destination)
			self._expect_keyword("PAGE")
			slug = self._literal_value()
			if not isinstance(slug, str):
				token = self._current()
				raise WqlError("MOVE PAGE requires a slug string", token.line, token.column)
			self._expect_keyword("TO")
			destination = self._literal_value()
			if not isinstance(destination, str):
				token = self._current()
				raise WqlError("MOVE PAGE requires a destination path string", token.line, token.column)
			return MovePageStmt(slug=slug, destination=destination)
		if self._match_keyword("SELECT"):
			return self._select()
		if self._match_keyword("INSERT"):
			return self._insert()
		if self._match_keyword("UPDATE"):
			return self._update()
		if self._match_keyword("DELETE"):
			return self._delete()
		token = self._current()
		raise WqlError("expected statement", token.line, token.column)

	def _search(self) -> SearchStmt:
		query = self._literal_value()
		if not isinstance(query, str):
			raise WqlError("SEARCH requires a string literal", self._current().line, self._current().column)
		top_k = 10
		budget = 2048
		while self._current().type != TokenType.EOF:
			if self._match_keyword("LIMIT"):
				top_k = self._expect_number_int("LIMIT value")
			elif self._match_keyword("BUDGET"):
				budget = self._expect_number_int("BUDGET value")
			else:
				token = self._current()
				raise WqlError("unexpected token after SEARCH", token.line, token.column)
		return SearchStmt(query=query, top_k=top_k, budget_tokens=budget)

	def _select(self) -> SelectStmt:
		projection: Literal["*", "markdown"] = "*"
		token = self._current()
		if token.type == TokenType.OPERATOR and token.value == "*":
			self._advance()
		elif token.type == TokenType.KEYWORD and token.value.upper() == "MARKDOWN":
			projection = "markdown"
			self._advance()
		else:
			raise WqlError("only SELECT * or SELECT markdown is supported", token.line, token.column)
		self._expect_keyword("FROM")
		table = self._table_name()
		where: Compare | BinaryExpr | NotExpr | None = None
		if self._match_keyword("WHERE"):
			where = self._expression()
		neighbors = self._match_keyword("NEIGHBORS")
		links = self._links_clause() if self._match_keyword("LINKS") else None
		if neighbors and links:
			token = self._current()
			raise WqlError("NEIGHBORS and LINKS are mutually exclusive", token.line, token.column)
		order_by = None
		limit = None
		if self._match_keyword("ORDER"):
			self._expect_keyword("BY")
			field = self._identifier_name()
			ascending = True
			if self._match_keyword("DESC"):
				ascending = False
			elif self._match_keyword("ASC"):
				ascending = True
			order_by = OrderBy(field=field, ascending=ascending)
		if self._match_keyword("LIMIT"):
			limit = self._expect_number_int("LIMIT value")
		return SelectStmt(
			table=table,
			projection=projection,
			where=where,
			neighbors=neighbors,
			links=links,
			order_by=order_by,
			limit=limit,
		)

	def _insert(self) -> InsertStmt:
		self._expect_keyword("INTO")
		table = self._table_name()
		if table != "pages":
			token = self._current()
			raise WqlError("INSERT supports only pages", token.line, token.column)
		self._expect_punct("(")
		columns: list[str] = [self._identifier_name()]
		while self._match_punct(","):
			columns.append(self._identifier_name())
		self._expect_punct(")")
		self._expect_keyword("VALUES")
		self._expect_punct("(")
		values: list[Any] = [self._literal_value()]
		while self._match_punct(","):
			values.append(self._literal_value())
		self._expect_punct(")")
		return InsertStmt(columns=tuple(columns), values=tuple(values))

	def _update(self) -> UpdateStmt | UpdateOpticsStmt:
		if self._match_keyword("OPTICS"):
			return self._update_optics()
		self._expect_keyword("PAGES")
		self._expect_keyword("SET")
		assignments: dict[str, Any] = {}
		while True:
			name = self._identifier_name()
			self._expect(TokenType.OPERATOR, "=")
			assignments[name] = self._literal_value()
			if not self._match_punct(","):
				break
		self._expect_keyword("WHERE")
		slug = self._slug_equality_where()
		full_markdown = "markdown" in assignments and len(assignments) == 1
		if "markdown" in assignments and len(assignments) > 1:
			token = self._current()
			raise WqlError("markdown cannot be mixed with field updates", token.line, token.column)
		return UpdateStmt(assignments=assignments, where_slug=slug, full_markdown=full_markdown)

	def _update_optics(self) -> UpdateOpticsStmt:
		self._expect_keyword("SET")
		field = self._identifier_name()
		if field != "text":
			token = self._current()
			raise WqlError("UPDATE optics supports only text", token.line, token.column)
		self._expect(TokenType.OPERATOR, "=")
		text = self._literal_value()
		self._expect_keyword("WHERE")
		slug, name = self._optics_slug_name_where()
		return UpdateOpticsStmt(text=text, slug=slug, name=name)

	def _delete(self) -> DeleteStmt | DeleteOpticsStmt:
		self._expect_keyword("FROM")
		table = self._table_name()
		if table == "optics":
			self._expect_keyword("WHERE")
			slug, name = self._optics_slug_name_where()
			return DeleteOpticsStmt(slug=slug, name=name)
		if table != "pages":
			token = self._current()
			raise WqlError("DELETE supports only pages or optics", token.line, token.column)
		self._expect_keyword("WHERE")
		slug = self._slug_equality_where()
		return DeleteStmt(slug=slug)

	def _optics_slug_name_where(self) -> tuple[str | ParamRef, str | ParamRef]:
		slug: str | ParamRef | None = None
		name: str | ParamRef | None = None

		def read_compare() -> None:
			nonlocal slug, name
			field = self._identifier_name()
			if field not in {"slug", "name"}:
				token = self._current()
				raise WqlError("optics WHERE supports only slug and name", token.line, token.column)
			op = self._current()
			if op.type != TokenType.OPERATOR or op.value != "=":
				raise WqlError("optics WHERE requires = comparisons", op.line, op.column)
			self._advance()
			value = self._literal_value()
			if isinstance(value, ParamRef):
				if not self._in_ws_inner:
					raise WqlError("$.field is only allowed inside WS[]", op.line, op.column)
			elif not isinstance(value, str):
				token = self._current()
				raise WqlError("slug and name must be strings", token.line, token.column)
			if field == "slug":
				if slug is not None:
					raise WqlError("duplicate slug predicate", op.line, op.column)
				slug = value
			else:
				if name is not None:
					raise WqlError("duplicate name predicate", op.line, op.column)
				name = value

		read_compare()
		if self._match_keyword("AND"):
			read_compare()
		if slug is None or name is None:
			token = self._current()
			raise WqlError("optics WHERE requires slug and name", token.line, token.column)
		return slug, name

	def _slug_equality_where(self) -> str | ParamRef:
		field = self._identifier_name()
		if field != "slug":
			token = self._current()
			raise WqlError("WHERE must compare slug", token.line, token.column)
		op = self._current()
		if op.type != TokenType.OPERATOR or op.value != "=":
			raise WqlError("WHERE slug requires =", op.line, op.column)
		self._advance()
		value = self._literal_value()
		if isinstance(value, ParamRef):
			if not self._in_ws_inner:
				raise WqlError("$.field is only allowed inside WS[]", op.line, op.column)
			return value
		if not isinstance(value, str):
			token = self._current()
			raise WqlError("slug must be a string", token.line, token.column)
		return value

	def _links_clause(self) -> LinksClause:
		depth = 1
		direction = Direction.OUTGOING
		while self._current().type != TokenType.EOF:
			if self._match_keyword("DEPTH"):
				depth = self._expect_number_int("DEPTH value")
			elif self._match_keyword("DIRECTION"):
				direction = self._parse_direction()
			elif self._current().type == TokenType.KEYWORD and self._current().value.upper() in {
				"ORDER",
				"LIMIT",
			}:
				break
			else:
				token = self._current()
				if token.type in {TokenType.EOF}:
					break
				if token.type == TokenType.KEYWORD and token.value.upper() == "NEIGHBORS":
					raise WqlError("NEIGHBORS and LINKS are mutually exclusive", token.line, token.column)
				break
		return LinksClause(depth=depth, direction=direction)

	def _parse_direction(self) -> Direction:
		token = self._current()
		if token.type != TokenType.KEYWORD:
			raise WqlError("expected OUTGOING, INCOMING, or BOTH", token.line, token.column)
		word = token.value.upper()
		self._advance()
		if word == "OUTGOING":
			return Direction.OUTGOING
		if word == "INCOMING":
			return Direction.INCOMING
		if word == "BOTH":
			return Direction.BOTH
		raise WqlError("expected OUTGOING, INCOMING, or BOTH", token.line, token.column)

	def _table_name(self) -> str:
		token = self._current()
		if token.type == TokenType.KEYWORD and token.value.upper() in {"PAGES", "EDGES", "LAYOUT", "OPTICS"}:
			self._advance()
			return token.value.lower()
		if token.type == TokenType.IDENTIFIER and token.value.lower() in {
			"pages",
			"edges",
			"layout",
			"optics",
		}:
			self._advance()
			return token.value.lower()
		raise WqlError("expected pages, edges, layout, or optics", token.line, token.column)

	def _identifier_name(self) -> str:
		token = self._current()
		if token.type == TokenType.IDENTIFIER:
			self._advance()
			return token.value
		if token.type == TokenType.KEYWORD and token.value.upper() in {
			"TYPE",
			"TRUE",
			"FALSE",
			"NULL",
			"MARKDOWN",
			"NAME",
			"TEXT",
		}:
			self._advance()
			return token.value.lower()
		raise WqlError("expected identifier", token.line, token.column)

	def _optic_name_value(self) -> str:
		token = self._current()
		if token.type == TokenType.STRING:
			value = self._literal_value()
			if not isinstance(value, str):
				raise WqlError("CREATE OPTIC requires a name", token.line, token.column)
			return value
		if token.type == TokenType.IDENTIFIER:
			return self._identifier_name()
		raise WqlError("CREATE OPTIC requires a name", token.line, token.column)

	def _literal_value(self) -> LiteralValue:
		token = self._current()
		if token.type == TokenType.PARAM_REF:
			if not self._in_ws_inner:
				raise WqlError("$.field is only allowed inside WS[]", token.line, token.column)
			self._advance()
			return ParamRef(field=token.value)
		if token.type == TokenType.STRING:
			self._advance()
			return token.value
		if token.type == TokenType.NUMBER:
			self._advance()
			text = token.value
			if "." in text or "e" in text.lower():
				return float(text)
			return int(text)
		if token.type == TokenType.KEYWORD:
			word = token.value.upper()
			if word == "TRUE":
				self._advance()
				return True
			if word == "FALSE":
				self._advance()
				return False
			if word == "NULL":
				self._advance()
				return None
		raise WqlError("expected literal", token.line, token.column)

	def _expect_number_int(self, context: str) -> int:
		token = self._current()
		if token.type != TokenType.NUMBER:
			raise WqlError(f"{context} must be an integer", token.line, token.column)
		if "." in token.value or "e" in token.value.lower():
			raise WqlError(f"{context} must be an integer", token.line, token.column)
		self._advance()
		return int(token.value)

	def _match_punct(self, char: str) -> bool:
		token = self._current()
		if token.type == TokenType.PUNCTUATION and token.value == char:
			self._advance()
			return True
		return False

	def _expression(self) -> Compare | BinaryExpr | NotExpr:
		return self._or_expr()

	def _or_expr(self) -> Compare | BinaryExpr | NotExpr:
		left = self._and_expr()
		while self._match_keyword("OR"):
			right = self._and_expr()
			left = BinaryExpr(left=left, operator="OR", right=right)
		return left

	def _and_expr(self) -> Compare | BinaryExpr | NotExpr:
		left = self._not_expr()
		while self._match_keyword("AND"):
			right = self._not_expr()
			left = BinaryExpr(left=left, operator="AND", right=right)
		return left

	def _not_expr(self) -> Compare | BinaryExpr | NotExpr:
		if self._match_keyword("NOT"):
			inner = self._not_expr()
			return NotExpr(expr=inner)
		return self._compare()

	def _compare(self) -> Compare:
		field = self._identifier_name()
		token = self._current()
		if token.type != TokenType.OPERATOR:
			raise WqlError("expected comparison operator", token.line, token.column)
		op = token.value
		if op not in {"=", "!=", "<", ">", "<=", ">="}:
			raise WqlError("invalid comparison operator", token.line, token.column)
		self._advance()
		value = self._literal_value()
		return Compare(field=field, operator=op, value=value)


def parse(source: str) -> Stmt:
	try:
		tokens = tokenize(source)
	except LexerError as exc:
		raise WqlError(str(exc).split(" at line")[0], exc.line, exc.column) from exc
	parser = Parser(tokens)
	stmt = parser.parse()
	_validate(stmt)
	return stmt


def _validate(stmt: Stmt) -> None:
	if isinstance(stmt, StreamStmt):
		_validate_stream(stmt)
		_validate(stmt.outer)
		_validate(stmt.inner)
		return
	if isinstance(stmt, SelectStmt):
		if stmt.table not in {"pages", "edges", "layout", "optics"}:
			raise WqlError("unknown table")
		if stmt.table == "optics":
			if stmt.projection != "*":
				raise WqlError("optics supports only SELECT *")
			if stmt.neighbors or stmt.links or stmt.order_by or stmt.limit:
				raise WqlError("optics listing does not support graph or catalog clauses")
			_validate_optics_where(stmt.where)
		if stmt.table == "layout":
			if stmt.projection != "*":
				raise WqlError("layout supports only SELECT *")
			if stmt.where or stmt.neighbors or stmt.links or stmt.order_by or stmt.limit:
				raise WqlError("layout listing does not support filters or graph clauses")
		if stmt.projection == "markdown":
			if stmt.table != "pages":
				raise WqlError("SELECT markdown supports only pages")
			if stmt.neighbors or stmt.links or stmt.order_by or stmt.limit:
				raise WqlError("SELECT markdown does not support graph or catalog clauses")
			if stmt.where is None or not _is_slug_equality(stmt.where):
				raise WqlError("SELECT markdown requires WHERE slug = '...'")
		if stmt.links:
			if stmt.links.depth < 1 or stmt.links.depth > 8:
				raise WqlError("DEPTH must be between 1 and 8")
			if stmt.where is None or not _is_slug_equality(stmt.where):
				raise WqlError("LINKS requires WHERE slug = '...'")
		if stmt.neighbors and stmt.where is None:
			raise WqlError("NEIGHBORS requires WHERE slug = '...'")
		if stmt.table == "edges":
			if stmt.neighbors or stmt.links:
				raise WqlError("graph clauses apply only to pages")
			if stmt.order_by or stmt.limit:
				raise WqlError("ORDER BY and LIMIT are not supported for edges")
			_validate_edges_where(stmt.where)
		if stmt.table == "pages" and stmt.where and _is_slug_equality(stmt.where) and not stmt.links:
			if stmt.order_by or stmt.limit:
				raise WqlError("ORDER BY and LIMIT apply only to catalog listing")
		if (
			stmt.order_by
			and stmt.table == "pages"
			and not stmt.links
			and not stmt.neighbors
			and not (stmt.where and _is_slug_equality(stmt.where))
		):
			if stmt.order_by.field not in _CATALOG_FIELDS:
				raise WqlError("ORDER BY field is not supported for catalog listing")
	if isinstance(stmt, InsertStmt):
		if "slug" not in stmt.columns:
			raise WqlError("INSERT requires slug column")
		if "markdown" not in stmt.columns and "file_base64" not in stmt.columns:
			raise WqlError("INSERT requires markdown or file_base64 column")
		if len(stmt.columns) != len(stmt.values):
			raise WqlError("INSERT column count must match VALUES count")
		for column in stmt.columns:
			if column not in _INSERT_COLUMNS:
				raise WqlError(f"unknown INSERT column: {column}")
	if isinstance(stmt, UpdateStmt):
		for field in stmt.assignments:
			if field not in _UPDATE_FIELDS:
				raise WqlError(f"unknown UPDATE field: {field}")


def _validate_optics_where(expr: Compare | BinaryExpr | NotExpr | None) -> None:
	if expr is None:
		return

	def walk(node: Compare | BinaryExpr | NotExpr) -> None:
		if isinstance(node, Compare):
			if node.field not in {"slug", "name"}:
				raise WqlError("optics WHERE supports only slug and name")
			if node.operator != "=":
				raise WqlError("optics WHERE supports only = comparisons")
			return
		if isinstance(node, NotExpr):
			raise WqlError("optics WHERE supports only AND comparisons")
		if isinstance(node, BinaryExpr):
			if node.operator != "AND":
				raise WqlError("optics WHERE supports only AND comparisons")
			walk(node.left)
			walk(node.right)
			return
		raise WqlError("optics WHERE supports only AND comparisons")

	walk(expr)


def optics_filters_from_where(
	expr: Compare | BinaryExpr | NotExpr | None,
) -> tuple[str | None, str | None]:
	if expr is None:
		return None, None
	slug: str | None = None
	name: str | None = None
	for compare in edge_and_compares(expr):
		if compare.operator != "=":
			raise WqlError("optics WHERE supports only = comparisons")
		if compare.field == "slug" and isinstance(compare.value, str):
			slug = compare.value
		elif compare.field == "name" and isinstance(compare.value, str):
			name = compare.value
		else:
			raise WqlError("optics WHERE supports only slug and name string literals")
	return slug, name


def _is_slug_equality(expr: Compare | BinaryExpr | NotExpr) -> bool:
	if isinstance(expr, Compare):
		return (
			expr.field == "slug"
			and expr.operator == "="
			and (isinstance(expr.value, str) or isinstance(expr.value, ParamRef))
		)
	return False


def _validate_stream(stmt: StreamStmt) -> None:
	outer = stmt.outer
	if isinstance(outer, (CreateFolderStmt, DropFolderStmt, MoveFolderStmt, MovePageStmt)):
		raise WqlError("WS[] outer query must produce a row set")
	if isinstance(
		outer,
		(
			InsertStmt,
			UpdateStmt,
			UpdateOpticsStmt,
			DeleteStmt,
			DeleteOpticsStmt,
			CreateOpticStmt,
			DropOpticStmt,
			SchemaStmt,
		),
	):
		raise WqlError("WS[] outer query must produce a row set")
	if isinstance(outer, SelectStmt) and outer.projection == "markdown":
		raise WqlError("SELECT markdown cannot be the outer query of WS[]")
	if isinstance(stmt.inner, StreamStmt):
		raise WqlError("nested WS[] is not allowed")


def is_stream_stmt(stmt: Stmt) -> bool:
	return isinstance(stmt, StreamStmt)


def _validate_edges_where(expr: Compare | BinaryExpr | NotExpr | None) -> None:
	if expr is None:
		raise WqlError("edges query requires from_slug or to_slug filter")
	has_endpoint = False

	def walk(node: Compare | BinaryExpr | NotExpr) -> None:
		nonlocal has_endpoint
		if isinstance(node, Compare):
			if node.field not in {"from_slug", "to_slug", "type"}:
				raise WqlError("edges WHERE supports only from_slug, to_slug, and type")
			if node.field == "type" and node.operator == "=" and node.value != "links_to":
				raise WqlError("unknown edge type")
			if node.field in {"from_slug", "to_slug"}:
				has_endpoint = True
			return
		if isinstance(node, NotExpr):
			raise WqlError("edges WHERE supports only AND comparisons")
		if isinstance(node, BinaryExpr):
			if node.operator != "AND":
				raise WqlError("edges WHERE supports only AND comparisons")
			walk(node.left)
			walk(node.right)
			return
		raise WqlError("edges WHERE supports only AND comparisons")

	walk(expr)
	if not has_endpoint:
		raise WqlError("edges query requires from_slug or to_slug filter")


def edge_and_compares(expr: Compare | BinaryExpr | NotExpr) -> list[Compare]:
	if isinstance(expr, Compare):
		return [expr]
	if isinstance(expr, BinaryExpr) and expr.operator == "AND":
		return edge_and_compares(expr.left) + edge_and_compares(expr.right)
	return []
