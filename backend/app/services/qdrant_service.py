from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "paper_chunks")
_VECTOR_SIZE = int(os.getenv("QDRANT_VECTOR_SIZE", "384"))


def _get_client() -> Any:
    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise RuntimeError("qdrant_client not installed") from exc
    return QdrantClient(url=_DEFAULT_QDRANT_URL)


def ensure_collection() -> None:
    """Create Qdrant collection if it does not exist."""
    client = _get_client()
    try:
        from qdrant_client.models import Distance, VectorParams

        client.get_collection(_COLLECTION_NAME)
        logger.info("Qdrant collection '%s' exists", _COLLECTION_NAME)
    except Exception:
        logger.info("Creating Qdrant collection '%s'", _COLLECTION_NAME)
        client.create_collection(
            collection_name=_COLLECTION_NAME,
            vectors_config=VectorParams(size=_VECTOR_SIZE, distance=Distance.COSINE),
        )


def upsert_chunks(
    chunk_ids: list[str],
    embeddings: list[list[float]],
    payloads: list[dict[str, Any]],
) -> None:
    """Upsert chunk embeddings into Qdrant."""
    if not chunk_ids:
        return
    client = _get_client()
    ensure_collection()
    from qdrant_client.models import PointStruct

    points = [
        PointStruct(id=chunk_ids[i], vector=embeddings[i], payload=payloads[i])
        for i in range(len(chunk_ids))
    ]
    client.upsert(collection_name=_COLLECTION_NAME, points=points)
    logger.info("Upserted %d chunks to Qdrant", len(points))


def search_chunks(
    query_embedding: list[float],
    top_k: int = 20,
    filter_project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search chunks by vector similarity."""
    client = _get_client()
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    query_filter = None
    if filter_project_id:
        query_filter = Filter(
            must=[FieldCondition(key="project_id", match=MatchValue(value=filter_project_id))]
        )

    results = client.search(
        collection_name=_COLLECTION_NAME,
        query_vector=query_embedding,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )
    output: list[dict[str, Any]] = []
    for r in results:
        payload = r.payload or {}
        output.append(
            {
                "id": r.id,
                "score": round(float(r.score), 6),
                **payload,
            }
        )
    return output


def delete_by_paper_id(paper_id: str) -> None:
    """Remove all chunks for a given paper."""
    client = _get_client()
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client.delete(
        collection_name=_COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="paper_id", match=MatchValue(value=paper_id))]
        ),
    )
    logger.info("Deleted Qdrant chunks for paper %s", paper_id)
