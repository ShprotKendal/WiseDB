"""Lexer for the WQL query language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
	KEYWORD = "KEYWORD"
	IDENTIFIER = "IDENTIFIER"
	STRING = "STRING"
	NUMBER = "NUMBER"
	OPERATOR = "OPERATOR"
	PUNCTUATION = "PUNCTUATION"
	PARAM_REF = "PARAM_REF"
	EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
	"""One token and its 1-based source location."""

	type: TokenType
	value: str
	line: int
	column: int


class LexerError(ValueError):
	"""Raised when the source contains an invalid or incomplete token."""

	def __init__(self, message: str, line: int, column: int) -> None:
		self.line = line
		self.column = column
		super().__init__(f"{message} at line {line}, column {column}")


_KEYWORDS = frozenset(
	{
		"AND",
		"ASC",
		"BOTH",
		"BUDGET",
		"BY",
		"ASC",
		"CREATE",
		"DELETE",
		"DROP",
		"FOLDER",
		"LAYOUT",
		"MOVE",
		"PAGE",
		"SCHEMA",
		"SPAN",
		"TO",
		"DEPTH",
		"DESC",
		"DIRECTION",
		"EDGES",
		"FALSE",
		"FILE",
		"FROM",
		"IMAGE",
		"INCOMING",
		"JSON",
		"LINK",
		"INSERT",
		"INTO",
		"LIMIT",
		"LINKS",
		"MARKDOWN",
		"NEIGHBORS",
		"NOT",
		"NULL",
		"OPTIC",
		"OPTICS",
		"OR",
		"ORDER",
		"OUTGOING",
		"PAGES",
		"SEARCH",
		"SELECT",
		"SET",
		"TEXT",
		"TRUE",
		"UPDATE",
		"VALUES",
		"VIDEO",
		"WHERE",
		"WS",
	}
)
_TWO_CHAR_OPERATORS = frozenset({"!=", "<=", ">=", "==", "<>", "&&", "||"})
_ONE_CHAR_OPERATORS = frozenset("=<>+-*/%!")
_PUNCTUATION = frozenset("(),.;[]{}:")
_ESCAPES = {
	"0": "\0",
	"\\": "\\",
	"'": "'",
	'"': '"',
	"n": "\n",
	"r": "\r",
	"t": "\t",
}


def tokenize(source: str) -> list[Token]:
	"""Split WQL source into tokens, ending with an explicit EOF token."""
	if not isinstance(source, str):
		raise TypeError("source must be a string")

	tokens: list[Token] = []
	index = 0
	line = 1
	column = 1
	length = len(source)

	def advance() -> str:
		nonlocal index, line, column
		char = source[index]
		index += 1
		if char == "\n":
			line += 1
			column = 1
		else:
			column += 1
		return char

	while index < length:
		char = source[index]

		if char.isspace():
			advance()
			continue

		if source.startswith("--", index):
			while index < length and source[index] != "\n":
				advance()
			continue

		if source.startswith("/*", index):
			comment_line, comment_column = line, column
			advance()
			advance()
			while index < length and not source.startswith("*/", index):
				advance()
			if index == length:
				raise LexerError("Unterminated block comment", comment_line, comment_column)
			advance()
			advance()
			continue

		token_line, token_column = line, column

		if char in "'\"":
			quote = advance()
			value: list[str] = []
			while index < length and source[index] != quote:
				current = advance()
				if current == "\\":
					if index == length:
						raise LexerError("Unterminated escape sequence", token_line, token_column)
					escaped = advance()
					value.append(_ESCAPES.get(escaped, escaped))
				else:
					value.append(current)
			if index == length:
				raise LexerError("Unterminated string", token_line, token_column)
			advance()
			tokens.append(Token(TokenType.STRING, "".join(value), token_line, token_column))
			continue

		if char.isalpha() or char == "_":
			start = index
			while index < length and (source[index].isalnum() or source[index] == "_"):
				advance()
			value = source[start:index]
			kind = TokenType.KEYWORD if value.upper() in _KEYWORDS else TokenType.IDENTIFIER
			tokens.append(Token(kind, value, token_line, token_column))
			continue

		if char.isdigit() or (char == "." and index + 1 < length and source[index + 1].isdigit()):
			start = index
			if char == ".":
				advance()
			else:
				while index < length and source[index].isdigit():
					advance()
				if index < length and source[index] == ".":
					advance()
			while index < length and source[index].isdigit():
				advance()
			if index < length and source[index] in "eE":
				advance()
				if index < length and source[index] in "+-":
					advance()
				exponent_start = index
				while index < length and source[index].isdigit():
					advance()
				if index == exponent_start:
					raise LexerError("Invalid number exponent", token_line, token_column)
			tokens.append(Token(TokenType.NUMBER, source[start:index], token_line, token_column))
			continue

		pair = source[index : index + 2]
		if pair in _TWO_CHAR_OPERATORS:
			advance()
			advance()
			tokens.append(Token(TokenType.OPERATOR, pair, token_line, token_column))
			continue

		if char in _ONE_CHAR_OPERATORS:
			advance()
			tokens.append(Token(TokenType.OPERATOR, char, token_line, token_column))
			continue

		if char == "$":
			advance()
			if index >= length or source[index] != ".":
				raise LexerError("expected field reference after $", token_line, token_column)
			advance()
			if index >= length or not (source[index].isalpha() or source[index] == "_"):
				raise LexerError("expected identifier after $.", token_line, token_column)
			start = index
			while index < length and (source[index].isalnum() or source[index] == "_"):
				advance()
			field = source[start:index]
			tokens.append(Token(TokenType.PARAM_REF, field, token_line, token_column))
			continue

		if char in _PUNCTUATION:
			advance()
			tokens.append(Token(TokenType.PUNCTUATION, char, token_line, token_column))
			continue

		raise LexerError(f"Unexpected character {char!r}", token_line, token_column)

	tokens.append(Token(TokenType.EOF, "", line, column))
	return tokens


__all__ = ["LexerError", "Token", "TokenType", "tokenize"]
