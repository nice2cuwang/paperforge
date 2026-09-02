"""Tests for web-priority evidence strategy (timely topics without papers).

Scenario: 公众号 articles on news-like topics (e.g. a price change) have no
matching academic papers. Previously the pipeline force-cited tangential
metadata-only paper cards into body sections while real content was pushed
into llm-knowledge; web search results never became evidence.

Covered here:
- topic_assessment: web_priority_effective double-trigger (assessment or
  article_type == wechat_article)
- web_sources_node: web-priority widens the query fan-out
- evidence tiering: metadata-only / off-topic academic cards go to the
  延伸阅读 (background references) section instead of body sections
- honest-mode sections get a visible reader-facing knowledge note
- mixed sections cap knowledge paragraphs at 30%
- build_web_evidence falls back to snippet-level cards when page fetch fails
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime

import app.services.writing_service as ws
import app.services.web_search_service as wss


# ── topic assessment: web_priority_effective double trigger ──────


def _fake_state(article_type: str, web_priority: bool):
    payload = SimpleNamespace(query="DeepSeek 涨价后开发者如何安排工作")
    project = SimpleNamespace(
        article_type=article_type,
        research_question="DeepSeek 涨价后如何错峰",
        title="DeepSeek 涨价",
    )
    return {
        "task_id": "t",
        "db": None,
        "project_id": "p1",
        "payload": payload,
        "project": project,
    }


def _mock_assessment_llm(web_priority: bool):
    """Patch chat_completion to return the assessment JSON."""
    import json

    def fake(system_prompt, user_prompt, **kwargs):
        payload = {
            "feasibility": "medium",
            "reason": "timely topic",
            "suggested_queries": ["q1"],
            "topic_type": "product",
            "web_priority": web_priority,
        }
        return {"content": json.dumps(payload)}

    return fake


def _run_topic_assessment(monkeypatch, article_type: str, web_priority: bool) -> dict:
    from app.services.workflow.graph import topic_assessment_node

    project = SimpleNamespace(
        article_type=article_type,
        research_question="DeepSeek 涨价后如何错峰",
        title="DeepSeek 涨价",
    )
    state = {
        "task_id": "t",
        "project_id": "p1",
        "db": None,
        "payload": SimpleNamespace(query="DeepSeek 涨价后如何安排工作"),
    }
    monkeypatch.setattr(
        "app.services.workflow.graph._get_project_or_404", lambda pid, db: project
    )
    monkeypatch.setattr(
        "app.services.llm_service.chat_completion", _mock_assessment_llm(web_priority)
    )
    return topic_assessment_node(state)


def test_web_priority_effective_when_assessment_says_so(monkeypatch):
    out = _run_topic_assessment(monkeypatch, "policy_report", web_priority=True)
    assert out["topic_assessment"]["web_priority_effective"] is True


def test_web_priority_effective_when_wechat_article_even_if_llm_says_no(monkeypatch):
    out = _run_topic_assessment(monkeypatch, "wechat_article", web_priority=False)
    assert out["topic_assessment"]["web_priority_effective"] is True


def test_web_priority_off_for_academic_topic(monkeypatch):
    out = _run_topic_assessment(monkeypatch, "literature_review", web_priority=False)
    assert out["topic_assessment"]["web_priority_effective"] is False


# ── web_sources_node: web-priority widens the fan-out ────────────


def test_web_sources_node_widens_queries_in_web_priority(monkeypatch):
    from app.services.workflow import graph

    state = {
        "task_id": "t",
        "db": None,
        "project": SimpleNamespace(
            title="DeepSeek 涨价", research_question="DeepSeek 涨价后如何安排工作"
        ),
        "query": "DeepSeek 涨价后如何安排工作",
        "rewritten_queries": ["DeepSeek 错峰调度 省钱"],
        "topic_assessment": {"web_priority_effective": True},
        "evidence_count": 0,
    }

    calls: list[tuple[str, str | None]] = []

    def fake_search_web(query, max_results=8, recency=None):
        calls.append((query, recency))
        return []

    monkeypatch.setattr(graph, "search_web", fake_search_web)
    monkeypatch.setattr(graph, "fetch_page_details", lambda url: {"text": None, "published": None})
    graph.web_sources_node(state)

    queries = [q for q, _ in calls]
    recencies = {q: r for q, r in calls}
    assert any("最新 消息" in q for q in queries), "web-priority should add a news query variant"
    assert any("news" in q for q in queries)
    assert any("DeepSeek 错峰调度 省钱" in q for q in queries)
    # 时效窗口：核心查询限 1 个月、新闻变体限 1 周、背景重写查询不限时
    assert recencies.get("DeepSeek 涨价后如何安排工作") == "month"
    assert recencies.get("DeepSeek 涨价后如何安排工作 最新 消息") == "week"
    assert recencies.get("DeepSeek 错峰调度 省钱") is None


def test_web_sources_node_default_mode_has_no_news_variant(monkeypatch):
    from app.services.workflow import graph

    state = {
        "task_id": "t",
        "db": None,
        "project": SimpleNamespace(
            title="综述", research_question="多智能体研究综述"
        ),
        "query": "多智能体研究综述",
        "rewritten_queries": [],
        "topic_assessment": {},
        "evidence_count": 0,
    }

    calls: list[tuple[str, str | None]] = []

    def fake_search_web(query, max_results=8, recency=None):
        calls.append((query, recency))
        return []

    monkeypatch.setattr(graph, "search_web", fake_search_web)
    monkeypatch.setattr(graph, "fetch_page_details", lambda url: {"text": None, "published": None})
    graph.web_sources_node(state)

    queries = [q for q, _ in calls]
    assert not any("最新 消息" in q for q in queries)
    # 非时效路径不限制检索时间窗
    assert all(r is None for _, r in calls)


# ── evidence tiering: background cards → 延伸阅读 ────────────────


def _meta_card(cid: str, title: str):
    return {
        "id": cid,
        "paper_id": f"paper-{cid}",
        "paper_title": title,
        "source_type": "academic",
        "strength": "low",
        "chunk_ids": [],
        "claim": f"{title}. An evaluation of DeepSeek models in this area shows interesting results.",
        "supporting_text": f"{title} abstract text with DeepSeek content for the claim extraction.",
        "limitations": "Metadata-only evidence (title/abstract).",
        "evidence_type": "empirical_result",
    }


def _web_card(cid: str, claim: str):
    return {
        "id": cid,
        "paper_id": f"paper-{cid}",
        "paper_title": f"DeepSeek pricing news {cid}",
        "source_type": "web",
        "strength": "medium",
        "chunk_ids": ["c1"],
        "claim": claim,
        "supporting_text": claim,
        "limitations": "Web source evidence",
        "evidence_type": "web_source",
    }


WEB_CLAIMS = [
    "DeepSeek 官方宣布 V4 API 涨价，错峰时段折扣至五折",
    "开发者可以将批量任务挪到折扣时段以降低调用成本",
    "多家云厂商跟随调整了大模型 API 的峰谷定价策略",
]


def test_background_card_detection_metadata_only():
    card = _meta_card("m1", "Biomedical NLP evaluation of DeepSeek")
    assert ws._is_background_card(card, papers_off_topic=False) is True


def test_background_card_detection_legacy_empty_source_type():
    # 历史数据：元数据兜底卡 source_type 为空串，同样应降级为背景
    card = _meta_card("m1", "Biomedical NLP evaluation of DeepSeek")
    card["source_type"] = ""
    assert ws._is_background_card(card, papers_off_topic=False) is True


def test_background_card_detection_off_topic_pool():
    # 正文里有 chunk 的学术卡，但论文池整体离题 → 仍降级为背景
    card = _web_card("w1", WEB_CLAIMS[0])
    card["source_type"] = "academic"
    card["limitations"] = None
    assert ws._is_background_card(card, papers_off_topic=True) is True


def test_web_card_is_never_background():
    assert ws._is_background_card(_web_card("w1", WEB_CLAIMS[0]), papers_off_topic=False) is False
    # papers_off_topic 只影响学术卡
    assert ws._is_background_card(_web_card("w1", WEB_CLAIMS[0]), papers_off_topic=True) is False


def test_ingested_academic_card_stays_in_body():
    card = _web_card("a1", WEB_CLAIMS[0])
    card["source_type"] = "academic"
    card["limitations"] = None
    assert ws._is_background_card(card, papers_off_topic=False) is False


def test_build_draft_moves_background_cards_to_further_reading(monkeypatch):
    """metadata-only 论文卡不进正文，出现在文末延伸阅读区。"""
    cards = [
        _web_card("w1", WEB_CLAIMS[0]),
        _web_card("w2", WEB_CLAIMS[1]),
        _web_card("w3", WEB_CLAIMS[2]),
        _meta_card("m1", "DeepSeek biomedical NLP eval"),
        _meta_card("m2", "DeepSeek-VL vision language model"),
    ]
    seen_section_cards: list[set] = []

    def fake_write(section, **kw):
        seen_section_cards.append({c["id"] for c in kw.get("section_cards", [])})
        return f"{section} 的正文段落。 <!-- evidence: w1 -->"

    with patch.object(ws, "_llm_write_section", side_effect=fake_write), patch.object(
        ws, "_llm_generate_abstract", return_value="摘要"
    ), patch.object(ws, "_generate_topic_sections", return_value=["背景", "分析", "建议"]):
        content, sections = ws.build_draft_markdown(
            project_title="DeepSeek 涨价应对",
            research_question="DeepSeek 涨价后如何安排工作",
            article_type="wechat_article",
            citation_style="GB/T 7714",
            evidence_cards=cards,
        )

    assert "延伸阅读" in content
    assert "DeepSeek biomedical NLP eval" in content
    assert "<!-- evidence: m1 -->" in content
    # 正文章节不应引用 m1/m2（背景卡）
    for ids in seen_section_cards:
        assert not ids & {"m1", "m2"}


def test_build_draft_body_only_when_all_papers_background(monkeypatch):
    """全部论文卡都是背景时，正文靠 web/知识证据仍能生成。"""
    cards = [_meta_card("m1", "tangential paper"), _meta_card("m2", "another paper")]
    seen_section_cards: list[set] = []

    def fake_write(section, **kw):
        seen_section_cards.append({c["id"] for c in kw.get("section_cards", [])})
        return "知识段落 <!-- evidence: llm-knowledge -->"

    with patch.object(
        ws, "_llm_write_section", side_effect=fake_write
    ), patch.object(ws, "_llm_generate_abstract", return_value="摘要"), patch.object(
        ws, "_generate_topic_sections", return_value=["背景", "分析"]
    ):
        content, sections = ws.build_draft_markdown(
            project_title="时效话题",
            research_question="某产品价格调整分析",
            article_type="wechat_article",
            citation_style="GB/T 7714",
            evidence_cards=cards,
        )
    # 正文没有可用卡 → 章节拿到空卡列表（honest 路径），而非把 m1/m2 塞进正文
    assert seen_section_cards
    for ids in seen_section_cards:
        assert ids == set()
    assert "延伸阅读" in content


# ── honest mode: visible knowledge note ─────────────────────────


def test_honest_section_gets_visible_note(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm_service.chat_completion",
        lambda system_prompt, user_prompt, **kw: {"content": "基于行业观察的分析段落。"},
    )
    text = ws._llm_write_section_honest(
        section="行动建议",
        project_title="DeepSeek 涨价应对",
        research_question="如何错峰",
        article_type="wechat_article",
        word_count=400,
    )
    assert "基于模型已有知识" in text
    assert "<!-- evidence: llm-knowledge -->" in text


# ── mixed sections: knowledge paragraph ratio cap ────────────────


def test_balance_knowledge_paragraphs_marks_and_keeps_within_ratio():
    text = (
        "证据段落一。 <!-- evidence: w1 -->\n\n"
        "过渡分析段（无引用）。\n\n"
        "证据段落二。 <!-- evidence: w2 -->\n\n"
        "推演段落A（无引用）。\n\n"
        "推演段落B（无引用）。"
    )
    out = ws._balance_knowledge_paragraphs(text)
    # 5 个正文段，30% 上限 → 最多 2 个知识段（round(5*0.3)=2），恰好全保留
    assert "过渡分析段" in out
    assert "推演段落A" in out
    # 无引用段落补齐 llm-knowledge 标记
    assert out.count("llm-knowledge") == 2


def test_balance_knowledge_paragraphs_drops_excess_from_tail():
    text = (
        "证据段落一。 <!-- evidence: w1 -->\n\n"
        "推演段落A（无引用）。\n\n"
        "推演段落B（无引用）。\n\n"
        "推演段落C（无引用）。"
    )
    out = ws._balance_knowledge_paragraphs(text)
    # 4 个正文段 → 上限 1 个知识段；保留开头的 A，从尾部裁掉 B/C
    assert "推演段落A" in out
    assert "推演段落B" not in out
    assert "推演段落C" not in out
    assert "<!-- evidence: w1 -->" in out


def test_balance_knowledge_paragraphs_keeps_structural_lines():
    text = (
        "**1. 小标题**\n\n"
        "证据段落。 <!-- evidence: w1 -->\n\n"
        "推演段落（无引用）。\n\n"
        "## 二级标题"
    )
    out = ws._balance_knowledge_paragraphs(text)
    assert "**1. 小标题**" in out
    assert "## 二级标题" in out


# ── web search hardening ─────────────────────────────────────────


def test_ddg_retries_once_on_rate_limit(monkeypatch):
    """DDG 第一次限流失败后应退避重试。"""
    import sys
    import types

    calls = {"n": 0}

    class FakeDDGS:
        def __init__(self, timeout=None, proxy=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, query, max_results=8, timelimit=None):
            calls["n"] += 1
            calls["timelimit"] = timelimit
            if calls["n"] == 1:
                raise RuntimeError("202 Ratelimit")
            return [
                {"title": "DeepSeek price news", "href": "https://example.com/a", "body": "snippet"}
            ]

    fake_pkg = types.ModuleType("duckduckgo_search")
    fake_pkg.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_pkg)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(
        "app.services.http_client.resolve_proxy_url", lambda: None
    )

    results = wss._search_duckduckgo("DeepSeek price", max_results=5)
    assert calls["n"] == 2
    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"


# ── recency: 时效检索与排序 ──────────────────────────────────────


def test_search_web_maps_recency_to_timelimit(monkeypatch):
    import sys
    import types

    captured: dict = {}

    class FakeDDGS:
        def __init__(self, timeout=None, proxy=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, query, max_results=8, timelimit=None):
            captured["timelimit"] = timelimit
            return [{"title": "t", "href": "https://example.com/x", "body": "b"}]

    fake_pkg = types.ModuleType("duckduckgo_search")
    fake_pkg.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_pkg)
    monkeypatch.setattr(
        "app.services.http_client.resolve_proxy_url", lambda: None
    )

    wss.search_web("DeepSeek 涨价", max_results=5, recency="month")
    assert captured["timelimit"] == "m"

    wss.search_web("DeepSeek 涨价", max_results=5)
    assert captured["timelimit"] is None


def test_search_web_recency_bing_fallback_post_filters_stale(monkeypatch):
    """DDG 不可用时回退 Bing，但把可证明超出时效窗口的旧结果丢弃。"""
    from datetime import datetime, timedelta

    fresh = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    stale = (datetime.now() - timedelta(days=500)).strftime("%Y-%m-%d")

    monkeypatch.setattr(wss, "_search_duckduckgo", lambda q, m, timelimit=None: [])
    monkeypatch.setattr(
        wss,
        "_search_bing",
        lambda q, m: [
            {"title": "fresh news", "url": "https://e.com/a", "snippet": "s",
             "source_domain": "e.com", "source_type": "web", "full_text": None,
             "published": fresh},
            {"title": "old news", "url": "https://e.com/b", "snippet": "s",
             "source_domain": "e.com", "source_type": "web", "full_text": None,
             "published": stale},
            {"title": "undated", "url": "https://e.com/c", "snippet": "s",
             "source_domain": "e.com", "source_type": "web", "full_text": None,
             "published": None},
        ],
    )
    out = wss.search_web("DeepSeek 涨价", max_results=5, recency="week")
    titles = [r["title"] for r in out]
    assert "fresh news" in titles
    assert "undated" in titles, "undated results are kept (cannot prove stale)"
    assert "old news" not in titles, "provably stale results must be dropped"


def test_extract_date_hint_parsing():
    from datetime import datetime, timedelta

    # 相对日期
    d = wss._extract_date_hint("6 天之前 · DeepSeek 官网是国产领先的开源大模型平台")
    expect = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    assert d == expect

    d2 = wss._extract_date_hint("3 hours ago — price update")
    assert d2 == (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%d")

    # 中文绝对日期
    assert wss._extract_date_hint("2026年8月15日 DeepSeek 公告") == "2026-08-15"
    # 英文绝对日期
    assert wss._extract_date_hint("Posted on Aug 15, 2026") == "2026-08-15"
    # 无日期
    assert wss._extract_date_hint("没有任何日期信息的文本") is None


def test_normalize_date_str_variants():
    assert wss._normalize_date_str("2026-08-15") == "2026-08-15"
    assert wss._normalize_date_str("2026-08-15T09:30:00Z") == "2026-08-15"
    assert wss._normalize_date_str("") is None
    assert wss._normalize_date_str(None) is None


def test_recency_adjustment_scores():
    from datetime import datetime, timedelta

    def card_with(days_ago):
        date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        return {"source_type": "web", "published_hint": date}

    assert ws._recency_adjustment(card_with(2)) == 0.25
    assert ws._recency_adjustment(card_with(20)) == 0.15
    assert ws._recency_adjustment(card_with(60)) == 0.05
    assert ws._recency_adjustment(card_with(200)) == -0.05
    assert ws._recency_adjustment(card_with(400)) == -0.2
    # 学术卡 / 无日期卡不参与时效调分
    assert ws._recency_adjustment({"source_type": "academic", "published_hint": "2026-08-15"}) == 0.0
    assert ws._recency_adjustment({"source_type": "web", "published_hint": None}) == 0.0


def test_sorted_cards_prefers_fresh_web_sources():
    """同样相关度的两张 web 卡，新的应排在旧的前面。"""
    from datetime import datetime, timedelta

    def web_card(cid, days_ago, claim):
        date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        return {
            "id": cid,
            "source_type": "web",
            "strength": "medium",
            "chunk_ids": ["c1"],
            "claim": claim,
            "supporting_text": claim,
            "published_hint": date,
            "limitations": "Web source evidence",
            "evidence_type": "web_source",
        }

    cards = [
        web_card("old", 400, "DeepSeek API 价格调整，开发者需要错峰安排任务"),
        web_card("fresh", 2, "DeepSeek API 价格调整，开发者需要重新规划调用成本"),
    ]
    ranked = ws._sorted_cards(cards, "DeepSeek API 价格调整 错峰")
    assert ranked[0]["id"] == "fresh"
    assert ranked[1]["id"] == "old"


def test_write_prompt_contains_current_date_and_freshness_rule():
    prompt = ws._current_date_line()
    assert "当前日期" in prompt
    assert str(datetime.now().year) in prompt


def test_build_web_evidence_snippet_fallback(tmp_path):
    """页面抓取失败（只有 snippet）时仍建卡，标记 low 强度。"""
    created = wss.build_web_evidence(
        "proj-1",
        [
            {
                "title": "DeepSeek raises V4 API prices",
                "url": "https://news.example.com/deepseek-price",
                "snippet": "DeepSeek announced significant V4 API price increases effective August.",
                "source_domain": "news.example.com",
                "source_type": "web",
                "full_text": None,
            }
        ],
        db=_FakeDB(),
    )
    assert len(created) >= 1
    ev = created[0]
    assert ev.source_type == "web"
    assert ev.strength == "low"
    assert "Snippet-only" in ev.limitations


class _FakeDB:
    def add(self, obj):
        pass

    def flush(self):
        pass
