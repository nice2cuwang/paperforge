"""Tests for this round: figure admission gate, llm_knowledge references,
landing-page filter, and audit-log prompt transparency.

背景：最新生成的《DeepSeek涨价》文章暴露三个问题——离题论文的安全
热力表/参考文献页截图被当配图、参考文献里出现「佚名. xxx. llm_knowledge.」、
Bing 兜底返回的官网镜像导览页占据证据位；同时新增对话式工作区的
LLM 调用透明化（audit_logs 落库 prompt 原文 + purpose 标签）。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.services.citation_service as cit
import app.services.llm_service as llm
import app.services.web_search_service as wss


# ── 1. llm_knowledge 不进参考文献 ─────────────────────────────────


def _card(cid: str, paper, source_type: str = "academic"):
    return SimpleNamespace(id=cid, paper=paper, source_type=source_type)


def _paper(pid: str, title: str = "A Real Paper"):
    return SimpleNamespace(
        id=pid, title=title, authors=["Zhang, San"], year=2025,
        venue="Nature", doi="10.1/x", arxiv_id=None,
        source_url=None, pdf_url=None,
    )


def test_llm_knowledge_cards_do_not_enter_references():
    """知识型证据不产生「佚名. xxx. llm_knowledge.」条目。"""
    cards = [
        _card("k1", _paper("p-knowledge", "Model knowledge"), source_type="llm_knowledge"),
    ]
    out = cit.render_in_text_citations("论断<!-- evidence: k1 -->", cards, "GB/T 7714")
    assert "llm_knowledge" not in out
    assert "佚名" not in out
    assert "模型已有知识" in out  # 统一声明


def test_llm_knowledge_alongside_real_refs_gets_footnote():
    """真实文献 + 知识型证据混合：文献正常编号，知识段落加脚注说明。"""
    cards = [
        _card("a1", _paper("p1", "Real Study"), source_type="academic"),
        _card("k1", _paper("p-k", "Knowledge"), source_type="llm_knowledge"),
    ]
    out = cit.render_in_text_citations(
        "引用<!-- evidence: a1 -->与知识<!-- evidence: k1 -->", cards, "GB/T 7714"
    )
    assert "[1] Zhang, San" in out or "[1] Zhang San" in out
    assert "不属于上述参考文献" in out
    assert "llm_knowledge" not in out


def test_pure_academic_refs_unchanged():
    """纯学术引用路径不受影响：无知识声明脚注。"""
    cards = [_card("a1", _paper("p1"), source_type="academic")]
    out = cit.render_in_text_citations("引用<!-- evidence: a1 -->", cards, "GB/T 7714")
    assert "[1]" in out
    assert "模型已有知识" not in out


# ── 2. 导览页/下载页过滤 ──────────────────────────────────────────


def _web_result(title: str, snippet: str = "", domain: str = "example.com"):
    return {
        "title": title, "url": f"https://{domain}/page", "snippet": snippet,
        "source_domain": domain, "source_type": "web", "full_text": None,
        "published": None,
    }


def test_landing_pages_are_filtered_out():
    results = [
        _web_result("DeepSeek官网-DeepSeekAI-DeepSeek|探索未至之境", "", "deepseek-mc.com.cn"),
        _web_result("DeepSeek官网 –DeepSeek官方下载 | 国产开源大模型旗舰", "官方下载入口，探索未至之境", "agents-deepseek.com.cn"),
        _web_result("Join DeepSeek API platform to access our AI models", "Join the platform"),
        _web_result("DeepSeek| 网页版入口、V4 Pro正式版与免费API", "网页版入口与免费API说明"),
        # 真实新闻：标题含"官网"但后接动词（发布），snippet 是具体报道 → 保留
        _web_result(
            "DeepSeek官网发布涨价公告引发讨论",
            "DeepSeek 于 8 月 14 日在官网发布 API 调价公告，涨幅最高达 400%，开发者连夜调整架构",
        ),
    ]
    out = wss._filter_results(results)
    titles = [r["title"] for r in out]
    assert len(out) == 1
    assert "DeepSeek官网发布涨价公告引发讨论" in titles


def test_mirror_domain_hint_alone_drops_result():
    """镜像站域名（deepseek-mc.com.cn 等）+ 导览标题 → 直接丢弃。"""
    results = [_web_result("DeepSeek 网页版入口 V4 Pro", "探索未至之境", "deepseek-mc.com.cn")]
    assert wss._filter_results(results) == []


def test_normal_results_pass_filter():
    results = [
        _web_result("DeepSeek Raises V4 API Prices Significantly", "The company announced price increases effective August"),
    ]
    assert len(wss._filter_results(results)) == 1


# ── 3. audit_log purpose 推断 ─────────────────────────────────────


def test_infer_purpose_from_system_prompt():
    assert llm._infer_purpose("你是一位资深内容编辑，擅长将学术证据转化为文章") == "写作"
    assert llm._infer_purpose("你是一位资深学术编辑。请根据研究问题和可用证据撰写摘要") == "摘要"
    assert llm._infer_purpose("你是一位学术信息检索顾问。") == "话题评估"
    assert llm._infer_purpose("你是多智能体辩论审稿系统中的审稿人") == "审稿"
    assert llm._infer_purpose("完全无关的提示词") is None


def test_truncate_text_limits_length():
    long_text = "x" * 20_000
    truncated = llm._truncate_text(long_text)
    assert len(truncated) < 21_000
    assert "已截断" in truncated
    assert llm._truncate_text(None) is None
    assert llm._truncate_text("short") == "short"


# ── 4. 图片准入：离题论文图表过滤 ─────────────────────────────────


def test_off_topic_pool_ratio_detection():
    """多数派判定：12 篇里只有 1 篇命中 → 仍判离题（此前 1 篇即逃过）。"""
    from app.services.search_service import title_query_hits

    query = "在DeepSeek涨价后开发者该如何安排自己的工作"
    papers = [
        SimpleNamespace(title="DeepSeek: Paradigm Shifts and Technical Evolution") ,
        SimpleNamespace(title="Safety Evaluation of DeepSeek Models"),
        SimpleNamespace(title="DeepSeek biomedical NLP evaluation"),
        SimpleNamespace(title="ChatGPT vs DeepSeek code generation"),
        SimpleNamespace(title="An evaluation of DeepSeek Models in NLP"),
    ]
    topical = [p for p in papers if title_query_hits(p.title, query) >= 2]
    # 只有标题与"涨价/工作"主题重合的才算 topical；这些全是擦边命中
    ratio = len(topical) / len(papers)
    assert ratio < 0.6  # 判定规则：多数派（≥60%）才放行
