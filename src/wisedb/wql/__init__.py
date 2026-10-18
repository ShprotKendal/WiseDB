"""WiseDB WQL query language."""

from wisedb.wql.errors import WqlError
from wisedb.wql.executor import execute, iter_stream

__all__ = ["WqlError", "execute", "iter_stream"]
