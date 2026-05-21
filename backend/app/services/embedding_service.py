from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384


class EmbeddingProvider:
    """Lazy-loaded sentence-transformers embedding provider."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or _DEFAULT_MODEL
        self._model: Any | None = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info("Loading embedding model: %s", self.model_name)
                self._model = SentenceTransformer(self.model_name)
                logger.info("Embedding model loaded: dim=%s", self._model.get_embedding_dimension())
            except Exception as exc:
                logger.exception("Failed to load embedding model")
                raise RuntimeError(f"Embedding model load failed: {exc}") from exc
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        # Truncate long texts to avoid model token limits
        safe_texts = [(t or "")[:8000] for t in texts]
        embeddings = model.encode(safe_texts, show_progress_bar=False, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]

    def encode_single(self, text: str) -> list[float]:
        return self.encode([text])[0]

    @property
    def dimension(self) -> int:
        if self._model is not None:
            return self._model.get_embedding_dimension()
        return _EMBEDDING_DIM


# Module-level singleton
_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = EmbeddingProvider()
    return _provider


def encode_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_provider().encode(texts)


def encode_single(text: str) -> list[float]:
    return get_embedding_provider().encode_single(text)
