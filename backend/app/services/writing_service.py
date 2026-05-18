from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.services.llm_service import chat_completion_text


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
    claim = _to_text(card.get("_clean_claim"), "该证据支持进一步讨论。")
    strength = _to_text(card.get("strength")).lower()
    lead = _section_lead(section, zh_mode)

    if zh_mode:
        text = f"{lead}{claim}"
        if strength in {"low", "weak"}:
            text += "（该证据强度较低，建议结合更多来源交叉验证）"
        return text

    text = f"{lead} {claim}"
    if strength in {"low", "weak"}:
        text += " (low-strength evidence; further validation recommended)"
    return text


def _format_cards_for_prompt(cards: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for card in cards:
        ev_id = _to_text(card.get("id"), "unknown")
        claim = _to_text(card.get("_clean_claim") or card.get("claim"), "无 claim")
        support = _to_text(card.get("supporting_text"), "")[:400]
        strength = _to_text(card.get("strength"), "unknown")
        lines.append(f"[evidence: {ev_id}] strength={strength}")
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
    cards_text = _format_cards_for_prompt(section_cards)
    if not cards_text:
        return "（本节暂无直接证据支撑，待补充资料后扩展。）"

    system_prompt = (
        "你是一位资深内容编辑，擅长将学术证据转化为流畅、连贯的中文叙述文本。"
        "你绝不使用 bullet point 或列表形式，而是写成段落。"
        "术语首次出现时需附带一句话解释。"
        "段落之间必须有自然的逻辑过渡。"
    )

    user_prompt = (
        f"请为以下章节撰写连贯的正文段落。\n\n"
        f"章节：{section}\n"
        f"文章类型：{article_type}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n\n"
        f"可用的证据卡片（请基于这些证据撰写，不要引入外部信息）：\n"
        f"{cards_text}\n\n"
        f"写作要求：\n"
        f"1. 使用流畅的中文叙述，写成连贯段落，严禁使用 bullet point 或编号列表。\n"
        f"2. 段落之间要有逻辑过渡句。\n"
        f"3. 每个核心观点必须在对应位置插入 <!-- evidence: {{id}} --> 注释。\n"
        f"4. 术语首次出现需附带一句话解释。\n"
        f"5. 本节目标字数：约 {word_count} 字。\n\n"
        f"请只输出正文段落，不要输出标题、总结或元信息。"
    )

    from app.services.llm_service import chat_completion

    base_max_tokens = min(4096, max(1024, word_count * 4))

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


def build_draft_markdown(
    project_title: str,
    research_question: str,
    article_type: str,
    citation_style: str,
    evidence_cards: list[dict[str, Any]],
) -> str:
    cards = _sorted_cards(evidence_cards, research_question)
    zh_mode = _contains_cjk(project_title + research_question)

    lines: list[str] = [
        f"# {project_title}",
        "",
        f"> article_type: {article_type}",
        f"> citation_style: {citation_style}",
        "> writing_mode: evidence-grounded",
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
        return "\n".join(lines).strip()

    top_ids = [_to_text(card.get("id")) for card in cards[: min(4, len(cards))]]
    if zh_mode:
        summary_line = (
            f"本文围绕“{research_question}”展开，优先采用高相关证据并保留 evidence_id，"
            "用于后续人工审校与追溯。"
        )
    else:
        summary_line = (
            f"This draft addresses '{research_question}' using ranked evidence cards with traceable evidence_id anchors."
        )
    lines.extend(["## 摘要", summary_line, _evidence_comment(top_ids), ""])

    sections = _article_sections(article_type)
    usable = cards[: min(36, len(cards))]
    per_section = max(1, min(4, len(usable) // max(1, len(sections))))

    # Heuristic target word count per section
    total_target = 2500 if article_type == "wechat_article" else 4000
    section_target = total_target // max(1, len(sections))

    cursor = 0
    for section in sections:
        lines.append(f"## {section}")
        section_cards = usable[cursor : cursor + per_section]
        if not section_cards:
            section_cards = usable[max(0, len(usable) - per_section) :]
        cursor += per_section

        section_text = _llm_write_section(
            section=section,
            project_title=project_title,
            research_question=research_question,
            article_type=article_type,
            section_cards=section_cards,
            word_count=section_target,
        )
        lines.append(section_text)
        lines.append("")

    lines.append("## 证据索引")
    for card in usable[:30]:
        ev_id = _to_text(card.get("id"), "unknown")
        citation_key = _to_text(card.get("citation_key"))
        paper_id = _to_text(card.get("paper_id"), "unknown-paper")
        source = citation_key or paper_id
        lines.append(f"- evidence_id={ev_id} -> source={source}")
    lines.append("")

    lines.append("## 人工终审提示")
    if zh_mode:
        lines.append("本稿为 publication-preparation 阶段文稿，必须经人工终审后对外发布。")
    else:
        lines.append("This draft is publication-preparation output and requires mandatory human final review.")

    return "\n".join(lines).strip()
