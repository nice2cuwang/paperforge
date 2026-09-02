"""Tests for off-topic figure guards (post-run fixes for the DeepSeek Harness
article whose figures were polluted by green-construction / party-building
papers).

Root cause: ``relevance_score`` encodes authority (citations + recency), not
topical fit. A query with no real academic hits still filled the pool with
same-keyword journal noise, and social proof cards / extracted figures then
broadcast that noise into the article. Fixes:
1. ``title_query_hits`` - shared-content-term signal (latin tokens + CJK
   bigrams) between title and query.
2. ``generate_social_proof_cards`` gates papers through that signal.
3. ``finalize_figures`` deletes dangling ``{{ref:fig:N}}`` placeholders
   instead of neutralizing them to a stranded "下图".
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.image_service import finalize_figures
from app.services.search_service import title_query_hits
from app.services.social_proof_service import generate_social_proof_cards

QUERY = "DeepSeek Harness对比claude code等agent工具的优势"


# ── title_query_hits ─────────────────────────────────────────────────────


def test_title_query_hits_relevant_vs_noise():
    # Topical titles share multiple content terms with the query.
    assert title_query_hits("DeepSeek Harness: A verl-based RL training framework", QUERY) >= 2
    assert title_query_hits("Claude Code 与 agent 工具的对比研究", QUERY) >= 2
    # The exact garbage from the real run must score below the threshold.
    assert title_query_hits("漫谈基于绿色施工管理理念的建筑施工管理方法", QUERY) < 2
    assert title_query_hits("数字化转型背景下DeepSeek对党建工作效率提升的影响", QUERY) < 2
    assert title_query_hits("基于海绵城市理念的城市规划方法探讨", QUERY) < 2


# ── Social proof relevance gate ──────────────────────────────────────────


def test_social_proof_skipped_when_all_papers_off_topic(tmp_path):
    """The real failure: 4 papers[:4] from an off-topic pool produced cards
    for green-construction and party-building papers. All-off-topic -> no
    cards at all (and no API calls)."""
    papers = [
        SimpleNamespace(id="p1", title="漫谈基于绿色施工管理理念的建筑施工管理方法", doi=None, arxiv_id=None),
        SimpleNamespace(id="p2", title="数字化转型背景下DeepSeek对党建工作效率提升的影响", doi=None, arxiv_id=None),
    ]
    cards = generate_social_proof_cards(papers, "proj", tmp_path, research_question=QUERY)
    assert cards == []


def test_social_proof_keeps_topical_paper(tmp_path, monkeypatch):
    papers = [
        SimpleNamespace(id="p1", title="漫谈基于绿色施工管理理念的建筑施工管理方法", doi=None, arxiv_id=None),
        SimpleNamespace(id="p2", title="DeepSeek Harness RL 训练框架评测", doi=None, arxiv_id=None),
    ]
    # Keep the network collectors silent: only the gate behavior matters.
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_semantic_solar", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_github_info", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_huggingface", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_arxiv_info", lambda *a, **k: None
    )
    cards = generate_social_proof_cards(papers, "proj", tmp_path, research_question=QUERY)
    # Only paper 2 is eligible; with no API data available it may produce no
    # cards, but the off-topic paper must never appear in any output.
    assert all("施工" not in str(c) for c in cards)


def test_social_proof_no_query_keeps_old_behavior(tmp_path, monkeypatch):
    """Backward compatibility: without a query the gate is disabled."""
    papers = [SimpleNamespace(id="p1", title="任意标题", doi=None, arxiv_id=None)]
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_semantic_solar",
        lambda *a, **k: {"citations": 5},
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_github_info", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_huggingface", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.social_proof_service._fetch_arxiv_info", lambda *a, **k: None
    )
    # Should reach the rendering stage without raising (gate skipped).
    generate_social_proof_cards(papers, "proj", tmp_path, research_question="")


# ── Dangling {{ref:fig:N}} placeholders ─────────────────────────────────


def test_finalize_figures_deletes_dangling_placeholder_mid_sentence():
    images = [{"path": "/api/p/x.png", "alt": "图", "section": "Results", "source": "chart", "ref_key": "fig:1"}]
    content = (
        "## 实验\n\n"
        "结论甲。{{ref:fig:1}}\n\n"
        "![图](/api/p/x.png)\n\n"
        "第二段。{{ref:fig:9}} 对研究团队而言，答案不在跑分表里。\n\n"
        "第三段末尾悬空。{{ref:fig:9}}"
    )
    out = finalize_figures(content, images)
    # Anchored ref resolves to the real figure number.
    assert "（如图1所示）" in out
    # Dangling refs are deleted outright - no stranded "下图", no placeholder.
    assert "下图" not in out
    assert "{{ref:fig:" not in out
    # Sentence flow restored (no gap after the full stop).
    assert "。对研究团队而言" in out
    assert "第三段末尾悬空。" in out


def test_finalize_figures_resolves_multiple_dangling_positions():
    images = [{"path": "/api/p/y.png", "alt": "图", "section": "Results", "source": "chart", "ref_key": "fig:2"}]
    content = "正文。{{ref:fig:7}}\n\n![图](/api/p/y.png)\n\n另一段 {{ref:fig:2}} 结束。"
    out = finalize_figures(content, images)
    assert "（如图1所示）" in out  # ref_key fig:2 -> first injected figure
    assert "{{ref:fig:" not in out
