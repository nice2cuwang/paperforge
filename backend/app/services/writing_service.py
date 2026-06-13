from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from app.services.de_ai_service import de_ai_markdown
from app.services.llm_service import chat_completion_text

logger = logging.getLogger(__name__)


NOISE_PATTERNS = [
    re.compile(r"^vol\.?:?\(", re.IGNORECASE),
    re.compile(r"https?://doi\.org/\S+", re.IGNORECASE),
    re.compile(r"\breceived:\b", re.IGNORECASE),
    re.compile(r"\baccepted:\b", re.IGNORECASE),
    re.compile(r"\bpublished\b", re.IGNORECASE),
    re.compile(r"\bopen forum\b", re.IGNORECASE),
    re.compile(r"^\d{3,4}\s*ai", re.IGNORECASE),
]


def _to_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _evidence_comment(ids: list[str]) -> str:
    clean = [item.strip() for item in ids if item and item.strip()]
    if not clean:
        return ""
    return f"<!-- evidence: {', '.join(clean)} -->"


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF


def _contains_cjk(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text)


def _normalize_spaces(text: str) -> str:
    return " ".join((text or "").replace("\u00a0", " ").replace("\n", " ").split()).strip()


def _strip_brackets(text: str) -> str:
    compact = _normalize_spaces(text)
    compact = re.sub(r"\[[^\]]{1,120}\]", " ", compact)
    compact = re.sub(r"\([^)]{1,120}\)", " ", compact)
    return _normalize_spaces(compact)


def _looks_noisy(text: str) -> bool:
    t = _normalize_spaces(text)
    if not t:
        return True
    if len(t) < 12:
        return True
    if any(p.search(t) for p in NOISE_PATTERNS):
        return True
    content = [ch for ch in t if not ch.isspace()]
    if not content:
        return True
    natural = sum(1 for ch in content if ch.isalnum() or _is_cjk(ch))
    if natural / len(content) < 0.45:
        return True
    weird = sum(1 for ch in content if not (ch.isalnum() or _is_cjk(ch) or ch in ".,;:!?，。；：！？-—/()%"))
    return weird / len(content) > 0.32


def _split_sentences(text: str) -> list[str]:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"(?<=[。！？!?\.])\s+|\n+", t)
    return [_normalize_spaces(item) for item in parts if _normalize_spaces(item)]


def _tokenize_query(text: str) -> set[str]:
    normalized = _normalize_spaces(text).lower()
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9]{2,}", normalized):
        tokens.add(token)
    cjk_chars = [ch for ch in normalized if _is_cjk(ch)]
    for idx in range(len(cjk_chars) - 1):
        tokens.add(cjk_chars[idx] + cjk_chars[idx + 1])
    for idx in range(len(cjk_chars) - 2):
        tokens.add(cjk_chars[idx] + cjk_chars[idx + 1] + cjk_chars[idx + 2])
    return tokens


def _text_relevance(text: str, query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    lower = _normalize_spaces(text).lower()
    if not lower:
        return 0.0
    hits = 0
    for token in query_tokens:
        if token in lower:
            hits += 1
    return hits / max(1, len(query_tokens))


def _best_sentence(candidate_text: str, query_tokens: set[str]) -> str:
    sentences = _split_sentences(candidate_text)
    if not sentences:
        return ""
    scored: list[tuple[float, str]] = []
    for sent in sentences:
        clean = _strip_brackets(sent)
        if _looks_noisy(clean):
            continue
        score = _text_relevance(clean, query_tokens) + min(0.25, len(clean) / 220)
        scored.append((score, clean))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1][:220]


def _extract_claim(card: dict[str, Any], query_tokens: set[str]) -> str:
    claim = _to_text(card.get("claim"))
    support = _to_text(card.get("supporting_text"))
    for source in [claim, support]:
        sentence = _best_sentence(source, query_tokens)
        if sentence:
            return sentence
    return ""


def _strength_rank(value: str) -> int:
    mapping = {"high": 3, "medium": 2, "low": 1, "weak": 1}
    return mapping.get(value.lower().strip(), 0)


def _sorted_cards(evidence_cards: list[dict[str, Any]], research_question: str) -> list[dict[str, Any]]:
    query_tokens = _tokenize_query(research_question)
    cleaned_cards: list[tuple[float, dict[str, Any]]] = []
    weak_relevance_pool: list[tuple[float, dict[str, Any]]] = []
    seen_claims: set[str] = set()

    for card in evidence_cards:
        ev_id = _to_text(card.get("id"))
        if not ev_id:
            continue
        claim = _extract_claim(card, query_tokens)
        if not claim:
            continue
        claim_key = _normalize_spaces(claim).lower()
        if claim_key in seen_claims:
            continue
        seen_claims.add(claim_key)

        strength = _to_text(card.get("strength")).lower()
        relevance = _text_relevance(claim, query_tokens)
        quality = min(0.2, len(claim) / 280)
        score = _strength_rank(strength) * 0.35 + relevance * 0.5 + quality
        payload = (
            (
                score,
                {
                    **card,
                    "_clean_claim": claim,
                    "_relevance": relevance,
                },
            )
        )
        if query_tokens and len(query_tokens) >= 4 and relevance < 0.08:
            weak_relevance_pool.append(payload)
            continue
        cleaned_cards.append(payload)

    cleaned_cards.sort(key=lambda item: item[0], reverse=True)
    if cleaned_cards:
        return [item[1] for item in cleaned_cards]
    weak_relevance_pool.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in weak_relevance_pool]


def build_outline(project_title: str, research_question: str, evidence_cards: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in _sorted_cards(evidence_cards, research_question):
        grouped[_to_text(card.get("evidence_type"), "general_evidence")].append(card)

    lines: list[str] = [
        f"# {project_title}",
        "",
        "## 研究问题",
        research_question,
        "",
        "## 写作大纲（证据驱动）",
    ]

    if not grouped:
        lines.extend(
            [
                "### 待补充",
                "当前没有可用 evidence cards，无法生成可追溯大纲。",
            ]
        )
        return "\n".join(lines).strip()

    for evidence_type, cards in grouped.items():
        lines.append(f"### {evidence_type}")
        for card in cards[:8]:
            claim = _to_text(card.get("_clean_claim"), "（无 claim）")
            ev_id = _to_text(card.get("id"), "unknown")
            lines.append(f"- {claim}")
            lines.append(f"  {_evidence_comment([ev_id])}")
        lines.append("")

    return "\n".join(lines).strip()


def _article_sections(article_type: str) -> list[str]:
    key = _to_text(article_type).lower()
    if key == "policy_report":
        return ["问题界定", "关键证据", "政策建议", "实施路径", "风险与限制", "结论"]
    if key == "literature_review":
        return ["研究脉络", "核心争议", "证据对比", "研究空白", "结论"]
    if key == "academic_draft":
        return ["引言", "方法与证据", "结果", "讨论", "局限", "结论"]
    if key == "wechat_article":
        return ["问题引入", "关键发现", "案例与启发", "行动建议", "结语"]
    return ["引言", "证据分析", "讨论", "结论"]


def _generate_topic_sections(
    article_type: str,
    project_title: str,
    research_question: str,
    evidence_cards: list[dict[str, Any]],
) -> list[str]:
    """Use LLM to generate topic-specific section headings.

    Falls back to ``_article_sections(article_type)`` when the LLM call fails
    or returns an invalid result.
    """
    default_sections = _article_sections(article_type)

    # Build a concise summary of evidence themes so the LLM can ground its sections
    evidence_themes: list[str] = []
    seen_types: set[str] = set()
    for card in evidence_cards[:20]:
        etype = _to_text(card.get("evidence_type"), "general")
        if etype not in seen_types:
            seen_types.add(etype)
        claim = _to_text(card.get("_clean_claim") or card.get("claim"), "")[:100]
        if claim:
            evidence_themes.append(f"- [{etype}] {claim}")
    evidence_summary = "\n".join(evidence_themes[:12]) if evidence_themes else "（暂无证据概要）"

    zh_mode = _contains_cjk(project_title + research_question)
    if not zh_mode:
        return default_sections  # English mode: use static sections for now

    from app.services.llm_service import chat_completion
    import json as _json

    system_prompt = (
        "你是一位资深内容架构师，擅长为微信公众号风格的技术文章规划章节结构。"
        "你需要根据具体的研究主题和可用证据，规划最能传达核心信息的文章结构。"
    )

    article_type_label = {
        "wechat_article": "微信公众号深度技术文章",
        "literature_review": "文献综述",
        "policy_report": "政策分析报告",
        "academic_draft": "学术草稿",
    }.get(article_type, article_type)

    user_prompt = (
        f"文章类型：{article_type_label}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n\n"
        f"可用证据主题概览：\n{evidence_summary}\n\n"
        f"请为这篇文章规划 6 个章节标题。\n\n"
        f"要求：\n"
        f"1. 章节标题必须紧密围绕研究主题的具体内容，体现专业深度\n"
        f"2. 禁止使用以下通用名称：问题引入、关键发现、案例与启发、行动建议、结语、引言、结论\n"
        f"3. 章节应遵循清晰的认知逻辑：铺垫背景 → 核心方法/技术 → 实验与数据 → 深度分析 → 评估与展望\n"
        f"4. 标题应简洁有力（2-8个中文字），使用研究领域的专业术语\n"
        f"5. 标题风格参考（仅作格式参考，内容须贴合具体主题）：\n"
        f"   - 对比类文章：「对比对象概览」「核心架构差异」「性能基准评测」「成本效益分析」\n"
        f"   - 方法类文章：「技术背景」「核心方法」「训练策略」「实验验证」「应用前景」\n"
        f"   - 综述类文章：「研究现状」「技术路线对比」「核心挑战」「未来趋势」\n"
        f"6. 直接输出 JSON 数组，格式：[\"章节1\", \"章节2\", \"章节3\", \"章节4\", \"章节5\", \"章节6\"]\n"
        f"7. 只输出 JSON 数组，不要输出其他任何内容"
    )

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=512,
            timeout=30.0,
        )
        text = result.get("content", "").strip()
        if text:
            # Extract JSON from possible code fences
            if "```" in text:
                match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
                if match:
                    text = match.group(1)
            sections = _json.loads(text)
            if isinstance(sections, list) and all(isinstance(s, str) for s in sections):
                sections = [s.strip() for s in sections if s.strip()]
                if 4 <= len(sections) <= 8:
                    logger.info("Generated %d topic-specific sections via LLM", len(sections))
                    return sections
    except Exception:
        logger.exception("Topic-specific section generation failed, using defaults")

    return default_sections


def _section_lead(section: str, zh_mode: bool) -> str:
    if not zh_mode:
        return "Evidence indicates that"
    mapping = {
        "问题引入": "先看一个与主题直接相关的观察：",
        "关键发现": "关键发现是：",
        "案例与启发": "一个可借鉴的经验是：",
        "行动建议": "据此可提出行动建议：",
        "结语": "综合证据可得：",
        "问题界定": "问题可界定为：",
        "关键证据": "核心证据显示：",
        "政策建议": "基于证据，建议：",
        "实施路径": "可执行路径是：",
        "风险与限制": "同时需要注意：",
        "结论": "结论为：",
    }
    return mapping.get(section, "证据显示：")


def _render_paragraph(section: str, card: dict[str, Any], zh_mode: bool) -> str:
    """Fallback paragraph renderer when LLM writing fails.

    Produces a minimal but readable paragraph instead of raw title dumps.
    """
    claim = _strip_xml(_to_text(card.get("_clean_claim") or card.get("claim"), ""))
    support = _strip_xml(_to_text(card.get("supporting_text"), ""))[:300]
    ev_id = _to_text(card.get("id"), "unknown")
    strength = _to_text(card.get("strength")).lower()

    if not claim:
        return "（本节证据暂缺，待补充后扩展。）"

    if zh_mode:
        lines = [f"{_section_lead(section, zh_mode)}{claim}", f"<!-- evidence: {ev_id} -->"]
        if support and support != claim:
            lines.append(f"具体而言，{support}")
        if strength in {"low", "weak"}:
            lines.append("（该证据强度有限，需结合更多来源验证。）")
        return "\n\n".join(lines)

    lines = [f"{_section_lead(section, zh_mode)} {claim}", f"<!-- evidence: {ev_id} -->"]
    if support and support != claim:
        lines.append(f"Specifically, {support}")
    if strength in {"low", "weak"}:
        lines.append("(This evidence is limited; further validation is recommended.)")
    return "\n\n".join(lines)


# Strip XML/HTML tags that leak from PDF parsers (e.g. <jats:p>, <html>)
_XML_TAG_RE = re.compile(r"<[^>]+>")

# Evidence traceability comments — stripped from the final reader-facing output
_EVIDENCE_HTML_RE = re.compile(r"<!--\s*evidence:[^>]*-->\s*")


def _strip_xml(text: str) -> str:
    return _XML_TAG_RE.sub("", text).strip()


def strip_evidence_comments(text: str) -> str:
    """Remove all ``<!-- evidence: ... -->`` HTML comments from markdown.

    These are used internally for citation tracing but must not appear
    in the reader-facing article.
    """
    return _EVIDENCE_HTML_RE.sub("", text)


def _format_cards_for_prompt(cards: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for card in cards:
        ev_id = _to_text(card.get("id"), "unknown")
        claim = _strip_xml(_to_text(card.get("_clean_claim") or card.get("claim"), "无 claim"))
        support = _strip_xml(_to_text(card.get("supporting_text"), ""))[:400]
        strength = _to_text(card.get("strength"), "unknown")
        source_type = _to_text(card.get("source_type"), "academic")
        source_label = {
            "academic": "学术论文",
            "web": "网络来源",
            "community": "专业社区讨论",
            "llm_knowledge": "背景知识",
        }.get(source_type, source_type)
        lines.append(f"[evidence: {ev_id}] strength={strength} source={source_label}")
        lines.append(f"  claim: {claim}")
        if support:
            lines.append(f"  supporting_text: {support}")
        lines.append("")
    return "\n".join(lines).strip()


def _llm_write_section(
    section: str,
    project_title: str,
    research_question: str,
    article_type: str,
    section_cards: list[dict[str, Any]],
    word_count: int,
) -> str:
    """Ask LLM to write a coherent section. Falls back to template rendering on error."""
    cards_text = _format_cards_for_prompt(section_cards) if section_cards else ""

    if not cards_text:
        # Honest mode: no evidence for this section, generate from LLM knowledge
        return _llm_write_section_honest(
            section=section,
            project_title=project_title,
            research_question=research_question,
            article_type=article_type,
            word_count=word_count,
        )

    system_prompt = (
        "你是一位资深内容编辑，擅长将学术证据转化为流畅、结构清晰的中文技术文章。\n"
        "写作风格要求：\n"
        "- 以连贯的段落叙述为主，同时适度使用**加粗**标注核心概念和关键数据\n"
        "- 每个章节可使用 **小标题**（如 **1. 多智能体训练机制**）组织子话题\n"
        "- 当需要列举对比数据或关键发现时，可使用编号列表（1. 2. 3.）呈现\n"
        "- 术语首次出现时需附带一句话解释\n"
        "- 段落之间必须有自然的逻辑过渡\n"
        "- 每个段落控制在 150-250 字以内，禁止出现超过 300 字的长段落\n"
        "- 每个举例都必须有具体的数据、具名的案例或可查证的来源支撑，"
        "绝不使用'某公司'、'某些企业'等模糊指代"
    )

    user_prompt = (
        f"请为以下章节撰写连贯的正文段落。\n\n"
        f"章节：{section}\n"
        f"文章类型：{article_type}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n\n"
        f"可用的证据卡片（每条标注了来源类型）：\n"
        f"{cards_text}\n\n"
        f"写作要求：\n"
        f"1. **主题守卫**：所有内容必须紧密围绕研究主题「{project_title}」。如果某条证据与研究主题关联性弱，请忽略该证据，不要将其写入正文。绝不引入与研究主题无关的内容。\n"
        f"2. **来源分层**：优先使用学术论文来源的证据作为核心论点支撑，网络来源和社区讨论可作为补充视角和案例，背景知识用于铺垫和过渡。在引用不同来源时，请自然地融入来源描述（如'据学术研究显示'、'行业分析指出'、'社区讨论中提到'）。\n"
        f"3. **格式要求**：\n"
        f"   - 每个章节写 3-5 个段落，每段控制在 150-250 字\n"
        f"   - 段落之间用空行分隔，保证视觉呼吸感\n"
        f"   - 在关键概念、核心发现、重要数据处使用 **加粗** 标注\n"
        f"   - 当需要对比数据或列举要点时，可使用编号列表（1. 2. 3.）\n"
        f"   - 对于复杂的子话题，可使用 **小标题**（如 **1. 训练效率对比**）分隔\n"
        f"4. **内容去重**：本节内容必须与其他章节不重复。每个章节聚焦独特的分析维度，禁止在不同章节中反复陈述相同的论点或数据。\n"
        f"5. 每个核心观点必须在对应位置插入 <!-- evidence: {{id}} --> 注释。\n"
        f"6. 术语首次出现需附带一句话解释。\n"
        f"7. **深度展开**：每个章节应写 3-5 个段落（约{word_count}字），从多个角度深入论述——技术原理、性能数据、因果关系、实践案例。不要只停留在表面描述。\n"
        f"8. 如果证据不足以支撑深度论述，请基于研究问题进行合理的推演和分析性论述，用'从技术角度分析'、'可以合理推断'等表述引导推理过程。\n"
        f"9. **举例必须有据**：每当文中出现举例，必须附带至少一项具体论据支撑——具体的性能数据或基准测试分数、具名的真实项目或产品、已发表的研究报告结论。严禁使用'某公司'、'某些企业'、'相关领域'等模糊指代。\n"
        f"10. 你输出的 <!-- evidence: id --> 注释将被后台自动处理，无需在正文中提及或解释这些标记。正常撰写内容即可。"
        f"\n\n"
        f"目标字数：本节约 {word_count} 字。\n"
        f"请只输出正文段落，不要输出标题、总结或元信息。"
    )

    from app.services.llm_service import chat_completion

    # Token budget: ensure enough tokens for substantive content
    base_max_tokens = min(8192, max(2048, word_count * 4))

    for attempt in range(2):
        try:
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=base_max_tokens * (2 ** attempt),
                timeout=120.0,
            )
            if result.get("error") and "reasoning consumed" in result["error"].lower():
                # Token was eaten by reasoning; retry with larger allocation
                logger.warning("LLM writing attempt %d failed (reasoning consumed tokens), retrying...", attempt + 1)
                continue
            text = result.get("content", "")
            if text:
                return text.strip()
        except Exception as exc:
            logger.exception("LLM writing attempt %d raised unexpected error", attempt + 1)

    # Fallback: template rendering
    zh_mode = _contains_cjk(project_title + research_question)
    paragraphs: list[str] = []
    for card in section_cards:
        paragraphs.append(_render_paragraph(section, card, zh_mode))
    return "\n\n".join(paragraphs)


def _llm_write_section_honest(
    section: str,
    project_title: str,
    research_question: str,
    article_type: str,
    word_count: int,
) -> str:
    """Write a section using LLM's own knowledge when no evidence cards are available.

    This is the 'honest mode' fallback — the LLM writes based on its training knowledge
    and explicitly labels the content as knowledge-based rather than evidence-cited.
    """
    system_prompt = (
        "你是一位资深内容编辑，擅长基于已有知识撰写结构清晰的中文技术文章。\n"
        "写作风格要求：\n"
        "- 以连贯的段落叙述为主，同时适度使用**加粗**标注核心概念和关键数据\n"
        "- 每个章节可使用 **小标题**（如 **1. 架构设计对比**）组织子话题\n"
        "- 当需要列举对比数据或关键发现时，可使用编号列表呈现\n"
        "- 术语首次出现时需附带一句话解释\n"
        "- 段落之间必须有自然的逻辑过渡\n"
        "- 每个段落控制在 150-250 字以内，禁止出现超过 300 字的长段落\n"
        "- 每个举例都必须有具体的数据、具名的案例或可查证的来源支撑"
    )

    user_prompt = (
        f"请为以下章节撰写深度分析段落。\n\n"
        f"章节：{section}\n"
        f"文章类型：{article_type}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n\n"
        f"**重要说明**：当前该章节没有直接可用的文献证据。"
        f"请基于你对该主题的已有知识撰写深度分析内容，但需遵守以下规则：\n\n"
        f"1. 只陈述你有较高置信度的事实和分析\n"
        f"2. 对于不确定的信息，请用'据报道'、'业内分析认为'等措辞标注不确定性，但仍需给出具体的名称或数据\n"
        f"3. **可以引用**公开的基准测试数据、已知名模型/产品的性能指标、已发表的技术报告结论\n"
        f"4. **举例必须具体**：禁止使用'某公司'、'某些企业'、'相关领域'等模糊指代。每个例子必须包含具体的公司/产品/模型名称、具体的性能数据或技术指标、可查证的来源\n"
        f"5. 重点放在技术架构分析、性能对比、成本效益评估和行业趋势上\n"
        f"6. **格式要求**：\n"
        f"   - 每个章节写 3-5 个段落，每段 150-250 字\n"
        f"   - 段落之间用空行分隔\n"
        f"   - 在关键概念、核心数据处使用 **加粗** 标注\n"
        f"   - 当需要对比数据或列举要点时，可使用编号列表\n"
        f"   - 对于复杂的子话题，可使用 **小标题** 分隔\n"
        f"7. **内容去重**：本节内容必须与其他章节不重复。每个章节聚焦独特的分析维度。\n"
        f"8. 在段落开头自然地标注本节为知识性分析（如'从行业分析角度来看'、'综合公开信息可知'等）\n"
        f"9. **深度要求**：写 3-5 个段落，从多个维度展开论述，包括技术原理、实际表现、对比分析、应用场景等\n\n"
        f"目标字数：本节约 {word_count} 字。\n"
        f"请只输出正文段落，不要输出标题、总结或元信息。"
    )

    from app.services.llm_service import chat_completion

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max(4096, word_count * 4),
            timeout=120.0,
        )
        text = result.get("content", "").strip()
        if text:
            return text
    except Exception:
        logger.exception("Honest mode section writing failed")

    return f"（本节暂无直接证据支撑。基于已有知识的分析内容待补充。）"


def _estimate_word_count(article_type: str, evidence_card_count: int) -> tuple[int, list[int]]:
    """Estimate total and per-section word count based on article type and evidence richness.

    The heuristic uses a type-specific baseline and scales it by evidence density,
    capped to avoid runaway token usage.
    """
    base: int = {
        "wechat_article": 5000,
        "literature_review": 8000,
        "academic_draft": 7000,
        "policy_report": 6000,
    }.get(article_type, 5000)

    # Evidence richness factor: 0.85x with no cards up to 1.2x with 40+ cards
    richness = 0.85 + min(evidence_card_count, 40) / 40 * 0.35
    total = int(base * richness)
    total = min(max(total, base), 15000)

    sections = _article_sections(article_type)
    # Uneven distribution: intro/conclusion shorter, core sections longer
    weights: dict[str, float] = {
        "引言": 0.8,
        "问题引入": 0.8,
        "问题界定": 0.9,
        "方法": 1.3,
        "方法与证据": 1.3,
        "关键证据": 1.2,
        "结果": 1.2,
        "关键发现": 1.2,
        "证据对比": 1.2,
        "讨论": 1.1,
        "核心争议": 1.1,
        "案例与启发": 1.1,
        "政策建议": 1.1,
        "实施路径": 1.0,
        "风险与限制": 0.9,
        "局限": 0.9,
        "结论": 0.7,
        "结语": 0.7,
    }
    raw_weights = [weights.get(s, 1.0) for s in sections]
    total_weight = sum(raw_weights)
    per_section = [int(total * w / total_weight) for w in raw_weights]
    return total, per_section


def _llm_generate_abstract(
    project_title: str,
    research_question: str,
    article_type: str,
    top_cards: list[dict[str, Any]],
    zh_mode: bool,
) -> str:
    """Use LLM to generate a real abstract based on the research question and evidence."""
    cards_summary = []
    for card in top_cards:
        claim = _to_text(card.get("_clean_claim") or card.get("claim"), "")
        if claim:
            cards_summary.append(f"- {claim[:150]}")
    evidence_text = "\n".join(cards_summary) if cards_summary else "（暂无直接证据）"

    if zh_mode:
        system_prompt = (
            "你是一位资深学术编辑。请根据研究问题和可用证据撰写一段精炼的摘要。"
            "摘要应直接概述文章的核心论点和关键发现，不要提及写作流程、证据编号或系统机制。"
            "语言风格应专业、简洁，长度在100-200字之间。"
        )
        user_prompt = (
            f"研究主题：{project_title}\n"
            f"研究问题：{research_question}\n"
            f"文章类型：{article_type}\n\n"
            f"核心证据概要：\n{evidence_text}\n\n"
            f"请撰写一段中文摘要（100-200字），直接概述文章的研究背景、核心论点和主要发现。"
            f"不要提及'evidence_id'、'证据卡'、'系统'、'追溯'等内部概念。只输出摘要正文。"
        )
    else:
        system_prompt = (
            "You are a senior academic editor. Write a concise abstract based on the research question and evidence. "
            "The abstract should directly summarize the core argument and key findings without mentioning writing processes or system internals. "
            "Keep it between 80-150 words."
        )
        user_prompt = (
            f"Topic: {project_title}\n"
            f"Research question: {research_question}\n"
            f"Article type: {article_type}\n\n"
            f"Key evidence:\n{evidence_text}\n\n"
            f"Write an English abstract (80-150 words) summarizing the research context, core argument, and key findings. "
            f"Do not mention 'evidence_id', 'evidence cards', 'system', or 'tracing'. Output only the abstract text."
        )

    from app.services.llm_service import chat_completion

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=512,
            timeout=60.0,
        )
        text = result.get("content", "").strip()
        if text:
            return text
    except Exception:
        logger.exception("LLM abstract generation failed")

    # Fallback: construct a simple but clean abstract
    if zh_mode:
        return f"本文围绕「{research_question}」展开分析，综合多方研究证据，探讨该议题的核心发现与实践启示。"
    return f"This article examines '{research_question}', synthesizing available evidence to discuss core findings and practical implications."


def build_draft_markdown(
    project_title: str,
    research_question: str,
    article_type: str,
    citation_style: str,
    evidence_cards: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Generate the full draft article as Markdown.

    Returns a tuple of ``(content_md, section_names)`` so the caller can
    pass the actual section names to downstream nodes (e.g. image injection).
    """
    cards = _sorted_cards(evidence_cards, research_question)
    zh_mode = _contains_cjk(project_title + research_question)

    lines: list[str] = [
        f"# {project_title}",
        "",
    ]

    if not cards:
        lines.extend(
            [
                "## 说明",
                "当前没有可用证据卡，系统不会生成不可追溯的终稿内容。",
                "请先完成论文解析与证据卡构建后再生成草稿。",
            ]
        )
        return "\n".join(lines).strip(), []

    # ── Evidence sufficiency check ("honest mode") ───────────────
    # Count how many cards have meaningful relevance to the topic
    relevant_cards = [c for c in cards if c.get("_relevance", 0) >= 0.08]
    honest_mode = len(relevant_cards) < 3

    if honest_mode:
        lines.append(
            "> **说明**：本文基于有限的公开资料整理，部分论述基于模型已有知识。"
            "建议读者结合最新信息做进一步验证。"
        )
        lines.append("")

    top_ids = [_to_text(card.get("id")) for card in cards[: min(4, len(cards))]]
    abstract_text = _llm_generate_abstract(
        project_title, research_question, article_type, cards[:8], zh_mode
    )
    lines.extend(["## 摘要", abstract_text, _evidence_comment(top_ids), ""])

    # ── Generate topic-specific section headings ─────────────────
    sections = _generate_topic_sections(article_type, project_title, research_question, cards)
    usable = cards[: min(36, len(cards))]

    total_target, section_targets = _estimate_word_count(article_type, len(usable))

    # Distribute cards evenly — NO wrap-around
    # Sections that run out of cards will use honest-mode LLM generation
    n_sec = max(1, len(sections))
    per_section_card_count = max(1, min(4, len(usable) // n_sec)) if usable else 0

    cursor = 0
    for idx, section in enumerate(sections):
        lines.append(f"## {section}")
        if usable and cursor < len(usable):
            section_cards = usable[cursor : cursor + per_section_card_count]
            cursor += per_section_card_count
        else:
            section_cards = []

        section_text = _llm_write_section(
            section=section,
            project_title=project_title,
            research_question=research_question,
            article_type=article_type,
            section_cards=section_cards,
            word_count=section_targets[idx] if idx < len(section_targets) else section_targets[-1],
        )
        lines.append(section_text)
        lines.append("")

    raw = "\n".join(lines).strip()
    processed = de_ai_markdown(raw, intensity=0.4)
    # Strip evidence traceability comments from the reader-facing output
    processed = strip_evidence_comments(processed)
    return processed, sections
