"""Локальные эмбеддинги (sentence-transformers), опционально."""

from __future__ import annotations

from functools import lru_cache
from typing import List, Optional, Protocol, Sequence


class EmbeddingProvider(Protocol):
    @property
    def dimension(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> List[List[float]]: ...


class _SentenceTransformerProvider:
    def __init__(self, model_id: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_id)

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        out: List[List[float]] = []
        for v in vectors:
            if hasattr(v, "tolist"):
                out.append([float(x) for x in v.tolist()])
            else:
                out.append([float(x) for x in list(v)])
        return out


@lru_cache
def get_embedding_provider(model_id: Optional[str]) -> Optional[EmbeddingProvider]:
    if not model_id:
        return None
    return _SentenceTransformerProvider(model_id)
