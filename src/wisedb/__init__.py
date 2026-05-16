"""WiseDB: контекстная БД (ОЗУ + опционально SQLite) и демо 2D-визуализации."""

from wisedb.vector_db import DirectedVector, Node, VectorDB, VectorGeometry

__all__ = ["DirectedVector", "Node", "VectorDB", "VectorGeometry", "__version__"]

__version__ = "2.1.0"
