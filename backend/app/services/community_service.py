"""Community source service for gathering expert opinions from forums.

Supports Reddit (via JSON API) and Zhihu (via web scraping).
Creates evidence cards with source_type="community".
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

COMMUNITY_FETCH_TIMEOUT = 15.0
MAX_COMMUNITY_RESULTS = 8


def search_reddit(query: str, max_results: int = MAX_COMMUNITY_RESULTS) -> list[dict[str, Any]]:
    """Search Reddit for relevant discussions via JSON API."""
    results: list[dict[str, Any]] = []
    try:
        headers = {
            "User-Agent": "PaperForge/1.0 (academic research bot)",
        }
        search_url = "https://www.reddit.com/search.json"
        params = {
            "q": query,
            "limit": max_results,
            "sort": "relevance",
            "type": "link",
        }
        with httpx.Client(timeout=COMMUNITY_FETCH_TIMEOUT) as client:
            resp = client.get(search_url, headers=headers, params=params)
            if resp.status_code != 200:
                logger.warning("Reddit search returned status %d", resp.status_code)
                return results
            data = resp.json()

        children = data.get("data", {}).get("children", [])
        for child in children[:max_results]:
            post = child.get("data", {})
            title = (post.get("title") or "").strip()
            selftext = (post.get("selftext") or "").strip()
            url = f"https://www.reddit.com{post.get('permalink', '')}"
            subreddit = post.get("subreddit", "")

            if not title:
                continue
            if post.get("over_18"):
                continue

            # Combine title and body for richer evidence
            full_text = f"{title}\n\n{selftext}" if selftext else title

            results.append({
                "title": title,
                "url": url,
                "snippet": selftext[:300] if selftext else title,
                "full_text": full_text[:3000],
                "source_domain": f"r/{subreddit}",
                "source_type": "community",
                "community_platform": "reddit",
            })

    except Exception:
        logger.exception("Reddit search failed")

    return results


def search_zhihu(query: str, max_results: int = 6) -> list[dict[str, Any]]:
    """Search Zhihu for expert discussions (Chinese platform).

    Uses the Zhihu search API endpoint.
    """
    results: list[dict[str, Any]] = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.zhihu.com/",
        }
        search_url = "https://www.zhihu.com/api/v4/search_v3"
        params = {
            "t": "general",
            "q": query,
            "correction": 1,
            "offset": 0,
            "limit": max_results,
        }
        with httpx.Client(timeout=COMMUNITY_FETCH_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(search_url, headers=headers, params=params)
            if resp.status_code != 200:
                logger.debug("Zhihu search returned status %d", resp.status_code)
                return results
            data = resp.json()

        items = data.get("data", [])
        for item in items[:max_results]:
            obj = item.get("object", {})
            item_type = item.get("type", "")

            if item_type == "search_result":
                title = _strip_html(obj.get("title", "") or "")
                excerpt = _strip_html(obj.get("excerpt", "") or obj.get("description", "") or "")
                url = obj.get("url", "")
                if url and not url.startswith("http"):
                    url = f"https://www.zhihu.com{url}"

                if not title:
                    continue

                results.append({
                    "title": title,
                    "url": url or "https://www.zhihu.com/",
                    "snippet": excerpt[:300],
                    "full_text": f"{title}\n\n{excerpt}" if excerpt else title,
                    "source_domain": "zhihu.com",
                    "source_type": "community",
                    "community_platform": "zhihu",
                })

    except Exception:
        logger.exception("Zhihu search failed")

    return results


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text).strip()


def generate_llm_knowledge(
    project_title: str,
    research_question: str,
    existing_evidence_count: int = 0,
    depth: str = "standard",
) -> list[dict[str, Any]]:
    """Use LLM to generate background knowledge cards when evidence is sparse.

    This supplements the evidence pipeline with the model's training knowledge
    for context, definitions, and well-established facts.

    Args:
        depth: "standard" generates 3-5 cards, "deep" generates 8-12 cards with
               more specific technical details, benchmarks, and market data.
    """
    from app.services.llm_service import chat_completion

    results: list[dict[str, Any]] = []

    max_threshold = 15 if depth == "standard" else 8
    if existing_evidence_count >= max_threshold:
        # Enough evidence already; skip LLM knowledge generation
        return results

    if depth == "deep":
        card_count_range = "8-12"
        detail_instruction = (
            "请提供 8-12 个深入的技术知识点，每个知识点应包含具体的数据、名称或技术细节。"
            "覆盖以下维度（如适用）：\n"
            "- 技术架构和核心创新\n"
            "- 性能基准测试数据和对比\n"
            "- API 定价和成本对比\n"
            "- 实际应用场景和用户反馈\n"
            "- 与竞品的差异化分析\n"
            "- 开源生态和社区发展\n"
        )
    else:
        card_count_range = "3-5"
        detail_instruction = (
            "请提供 3-5 个关键知识点或背景分析，帮助丰富文章的内容深度。"
        )

    system_prompt = (
        "你是一位学术领域的资深研究员和行业分析师。请基于你的专业知识，为以下研究问题提供深入分析。"
        "每个知识点应该是一个独立的事实陈述或分析观点，包含具体的信息而非泛泛而谈。"
        "请注意：只陈述你有较高置信度的事实和分析，对于不确定的内容请明确标注'（待验证）'。"
        "请尽量使用具体的数字、名称和日期，避免模糊的描述。"
    )

    user_prompt = (
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n\n"
        f"当前已有 {existing_evidence_count} 条来自学术论文和网络搜索的证据。\n\n"
        f"{detail_instruction}"
        f"每个知识点请写成一段完整的陈述（100-250字），格式如下：\n\n"
        f"知识点1：[标题]\n[具体内容]\n\n"
        f"知识点2：[标题]\n[具体内容]\n\n"
        f"请确保内容与研究主题直接相关，不要生成与研究主题无关的内容。"
    )

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096 if depth == "deep" else 2048,
            timeout=90.0 if depth == "deep" else 60.0,
        )
        text = result.get("content", "").strip()
        if not text:
            return results

        # Parse the knowledge points
        current_title = ""
        current_text_lines: list[str] = []

        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Detect knowledge point headers
            match = re.match(r"知识点\s*\d+\s*[：:．.]\s*(.+)", line)
            if match:
                # Save previous point
                if current_title and current_text_lines:
                    full_text = " ".join(current_text_lines)
                    results.append({
                        "title": current_title,
                        "url": "",
                        "snippet": full_text[:300],
                        "full_text": full_text,
                        "source_domain": "llm_knowledge",
                        "source_type": "llm_knowledge",
                    })
                current_title = match.group(1).strip()
                current_text_lines = []
            else:
                current_text_lines.append(line)

        # Save last point
        if current_title and current_text_lines:
            full_text = " ".join(current_text_lines)
            results.append({
                "title": current_title,
                "url": "",
                "snippet": full_text[:300],
                "full_text": full_text,
                "source_domain": "llm_knowledge",
                "source_type": "llm_knowledge",
            })

    except Exception:
        logger.exception("LLM knowledge generation failed")

    return results[:12 if depth == "deep" else 5]


def build_community_evidence(
    project_id: str,
    community_results: list[dict[str, Any]],
    db: Any,
) -> list[Any]:
    """Create Paper + EvidenceCard records from community sources."""
    from app.models import Paper, EvidenceCard
    from app.services.evidence_service import build_evidence_from_chunks
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    created_evidence = []

    for result in community_results:
        title = result["title"]
        url = result.get("url", "")
        full_text = result.get("full_text") or result.get("snippet", "")
        platform = result.get("community_platform", "community")

        if not full_text or len(full_text) < 40:
            continue

        paper_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            project_id=project_id,
            title=title,
            authors=[],
            year=None,
            doi=None,
            arxiv_id=None,
            venue=result.get("source_domain", platform),
            abstract=result.get("snippet", "")[:2000],
            source=platform,
            source_type="community",
            source_url=url,
            pdf_url=None,
            oa_status=None,
            license=None,
            local_pdf_path=None,
            local_tei_path=None,
            relevance_score=0.4,
            selected=True,
            parse_status="parsed",
            metadata_json={
                "community_platform": platform,
                "source_domain": result.get("source_domain", ""),
            },
            created_at=now,
            updated_at=now,
        )
        db.add(paper)

        chunk_payload = [{
            "id": str(uuid4()),
            "text": full_text[:2400],
            "page_start": None,
            "page_end": None,
        }]

        evidence_items = build_evidence_from_chunks(paper_id, chunk_payload, limit=2)
        for item in evidence_items:
            ev = EvidenceCard(
                id=str(uuid4()),
                project_id=project_id,
                paper_id=paper_id,
                chunk_ids=item["chunk_ids"],
                claim=item["claim"],
                supporting_text=item["supporting_text"],
                evidence_type="community_opinion",
                source_type="community",
                strength=item.get("strength", "low"),
                limitations="Community source; represents individual opinions, not peer-reviewed.",
                page_start=None,
                page_end=None,
                citation_key=None,
                used_in_draft=False,
                created_at=now,
                updated_at=now,
            )
            db.add(ev)
            created_evidence.append(ev)

    return created_evidence
