from __future__ import annotations

import math
from collections import Counter
from typing import Any


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


def rank_chunks(query: str, chunks: list[dict[str, Any]], top_k: int = 20) -> list[dict[str, Any]]:
    query_vec = Counter(_tokenize(query))
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        score = _cosine(query_vec, Counter(_tokenize(text)))
        if score <= 0:
            continue
        ranked.append({**chunk, "score": round(float(score), 6)})

    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[: max(1, min(top_k, 200))]

