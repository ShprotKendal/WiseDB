"""WQL error types."""

from __future__ import annotations


class WqlError(ValueError):
    """Semantic or syntax error in a WQL program."""

    def __init__(self, message: str, line: int = 1, column: int = 1) -> None:
        self.line = line
        self.column = column
        super().__init__(f"{message} at line {line}, column {column}")
