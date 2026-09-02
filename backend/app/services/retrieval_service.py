from __future__ import annotations

import math
from collections import Counter
from typing import Any

from app.services.embedding_service import encode_single
from app.services.qdrant_service import search_chunks


def _tokenize(text: str) -> list[str]:
    return [token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if token]


def _cosine(lhs: Counter[str], rhs: Counter[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    common = set(lhs.keys()) & set(rhs.keys())
    numerator = sum(lhs[token] * rhs[token] for token in common)
    left = math.sqrt(sum(value * value for value in lhs.values()))
    right = math.sqrt(sum(value * value for value in rhs.values()))
    if left == 0 or right == 0:
        return 0.0
    return numerator / (left * right)


def _lexical_rank(query: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_vec = Counter(_tokenize(query))
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        score = _cosine(query_vec, Counter(_tokenize(text)))
        if score <= 0:
            continue
        ranked.append({**chunk, "score": round(float(score), 6)})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def _vector_rank(query: str, project_id: str | None, top_k: int) -> list[dict[str, Any]]:
    embedding = encode_single(query)
    results = search_chunks(
        query_embedding=embedding,
        top_k=top_k,
        filter_project_id=project_id,
    )
    return results


def recall_chunks(
    query: str,
    project_id: str | None = None,
    top_k: int = 50,
) -> list[dict[str, Any]]:
    """Vector-only recall from Qdrant (project-scoped).

    Used by the main workflow evidence node to supplement lexical chunk
    scoring. Raises on Qdrant/embedding failure; callers should catch.
    """
    return _vector_rank(query, project_id, top_k=top_k)


# Reciprocal Rank Fusion constant: 1/(K + rank). 60 is the standard value
# from the original RRF paper; it dampens the influence of top ranks so one
# list cannot dominate the fusion.
_RRF_K = 60


def rank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 20,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid retrieval: vector recall + lexical scoring fused via RRF.

    Vector similarity and lexical cosine are on incomparable scales; the
    previous per-list max-normalization let a weak list's top item masquerade
    as 1.0 and outrank genuinely strong hits in the other list. Reciprocal
    Rank Fusion only uses *ranks*, so both lists contribute comparably.
    """
    # Phase 1: Vector recall
    vector_results: list[dict[str, Any]] = []
    try:
        vector_results = _vector_rank(query, project_id, top_k=max(top_k, 20))
    except Exception:
        # If Qdrant is unavailable, proceed with lexical scoring only
        pass

    # Phase 2: Lexical scoring on local chunks
    lexical_results = _lexical_rank(query, chunks)

    # Phase 3: Reciprocal Rank Fusion over both lists
    scores: dict[str, float] = {}
    entries: dict[str, dict[str, Any]] = {}
    for results in (vector_results, lexical_results):
        for rank, record in enumerate(results):
            cid = str(record.get("id") or record.get("chunk_id") or "")
            if not cid or cid in entries:
                continue
            entries[cid] = record
            scores[cid] = 0.0
        for rank, record in enumerate(results):
            cid = str(record.get("id") or record.get("chunk_id") or "")
            if cid:
                scores[cid] += 1.0 / (_RRF_K + rank + 1)

    fused = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [{**entries[cid], "score": round(score, 6)} for cid, score in fused[: max(1, min(top_k, 200))]]
