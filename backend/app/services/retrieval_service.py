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


def rank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int = 20,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid retrieval: vector recall + lexical fallback + rerank.

    Strategy:
    1. Try vector search in Qdrant (project-scoped).
    2. If Qdrant returns fewer than top_k/2 results, fall back to lexical scoring.
    3. Merge, deduplicate, and return top_k.
    """
    # Phase 1: Vector recall
    vector_results: list[dict[str, Any]] = []
    try:
        vector_results = _vector_rank(query, project_id, top_k=max(top_k, 20))
    except Exception:
        # If Qdrant is unavailable, proceed with lexical fallback only
        pass

    # Phase 2: Lexical fallback on local chunks
    lexical_results = _lexical_rank(query, chunks)

    # Phase 3: Merge and deduplicate by chunk id
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []

    # Normalize scores to [0, 1] for both sets
    def _normalize(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not scored:
            return []
        max_score = max(r["score"] for r in scored)
        if max_score == 0:
            return scored
        return [{**r, "score": round(r["score"] / max_score, 6)} for r in scored]

    vector_norm = _normalize(vector_results)
    lexical_norm = _normalize(lexical_results)

    # Boost vector results slightly (they tend to have better semantic relevance)
    for r in vector_norm:
        r["score"] = round(r["score"] * 1.1, 6)

    for source in [vector_norm, lexical_norm]:
        for r in source:
            cid = str(r.get("id") or r.get("chunk_id") or "")
            if cid in seen:
                continue
            seen.add(cid)
            merged.append(r)

    merged.sort(key=lambda item: item["score"], reverse=True)
    return merged[: max(1, min(top_k, 200))]
