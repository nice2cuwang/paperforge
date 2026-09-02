"""Tests for 批次7: hybrid retrieval (RRF fusion) and evidence dedup."""
from __future__ import annotations

from types import SimpleNamespace

import app.services.retrieval_service as retrieval
from app.services.evidence_service import dedupe_evidence_cards


def _card(claim, support="support", strength="medium"):
    return SimpleNamespace(claim=claim, supporting_text=support, strength=strength)


# ── RRF fusion ───────────────────────────────────────────────────────────


def test_rrf_fuses_vector_and_lexical_ranks(monkeypatch):
    """A chunk top-ranked in BOTH lists must outrank a chunk top in only one."""
    vector = [{"id": "c1", "score": 0.9}, {"id": "c2", "score": 0.85}]
    lexical = [{"id": "c2", "score": 0.7}, {"id": "c3", "score": 0.6}]
    monkeypatch.setattr(retrieval, "_vector_rank", lambda q, p, top_k: vector)
    monkeypatch.setattr(retrieval, "_lexical_rank", lambda q, chunks: lexical)

    merged = retrieval.rank_chunks("query", [])
    assert [r["id"] for r in merged] == ["c2", "c1", "c3"]


def test_rrf_weak_lexical_list_cannot_masquerade_as_strong(monkeypatch):
    """The old per-list max-normalization let a weak list's top score 1.0 and
    beat a genuinely strong vector hit; RRF uses ranks only."""
    vector = [{"id": "v1", "score": 0.88}, {"id": "v2", "score": 0.87}, {"id": "v3", "score": 0.86}]
    lexical = [{"id": "l1", "score": 0.05}]  # weak list, but would normalize to 1.0
    monkeypatch.setattr(retrieval, "_vector_rank", lambda q, p, top_k: vector)
    monkeypatch.setattr(retrieval, "_lexical_rank", lambda q, chunks: lexical)

    merged = retrieval.rank_chunks("query", [])
    ids = [r["id"] for r in merged]
    # l1 appears, but a rank-1 item of the weak list must not beat the rank-1
    # item of the strong vector list (which the old normalization allowed).
    assert ids.index("v1") < ids.index("l1")


def test_rank_chunks_qdrant_down_uses_lexical(monkeypatch):
    def boom(query, project_id, top_k):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(retrieval, "_vector_rank", boom)
    monkeypatch.setattr(retrieval, "_lexical_rank", lambda q, chunks: [{"id": "x1", "score": 0.5}])
    merged = retrieval.rank_chunks("query", [{"id": "x1", "text": "t"}])
    assert [r["id"] for r in merged] == ["x1"]


# ── Evidence dedup ───────────────────────────────────────────────────────


def test_near_duplicate_cards_merged_keeping_strongest():
    cards = [
        _card("GPT-4 在 MMLU 基准上达到 86.4% 的准确率", "实验结果", strength="low"),
        _card("GPT-4 在 MMLU 基准上达到了 86.4% 的准确率", "实验结果", strength="high"),
        _card("通义千问在 C-Eval 上排名国产模型第一", "评测报告", strength="medium"),
    ]
    kept, dropped = dedupe_evidence_cards(cards)
    assert dropped == 1
    assert len(kept) == 2
    assert any(c.strength == "high" for c in kept)


def test_distinct_cards_all_kept():
    cards = [
        _card("模型甲在推理任务上超过模型乙"),
        _card("训练成本随参数量线性增长"),
        _card("社区讨论显示用户更偏好长上下文"),
    ]
    kept, dropped = dedupe_evidence_cards(cards)
    assert dropped == 0
    assert len(kept) == 3


def test_richer_supporting_text_breaks_strength_tie():
    cards = [
        _card("GPT-4 在 MMLU 基准上达到 86.4% 的准确率", "短", strength="medium"),
        _card("GPT-4 在 MMLU 基准上达到了 86.4% 的准确率", "很长的支撑文本" * 10, strength="medium"),
    ]
    kept, dropped = dedupe_evidence_cards(cards)
    assert dropped == 1
    assert len(kept[0].supporting_text) > 10
