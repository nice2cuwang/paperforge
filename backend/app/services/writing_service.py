from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
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


# LLM 输出偶发泄漏的包装标签（如 <refine>...</refine> 只剥掉一半留下的
# "</refine>"），必须在写入正文前剥离，否则会出现在成稿里。
_STRAY_TAG_RE = re.compile(
    r"</?(?:refine|output|answer|result|response|draft|section|think|thinking)>",
    re.IGNORECASE,
)

# Honest 模式段落的知识性标记（与 review_service.KNOWLEDGE_MARKER 对应）。
KNOWLEDGE_MARKER_TEXT = "<!-- evidence: llm-knowledge -->"

# honest 章节结尾的可见标注：读者需要知道本节基于模型知识而非检索证据。
KNOWLEDGE_SECTION_NOTE = "> ⚠️ 注：本节内容基于模型已有知识整理，未引用外部检索证据，建议读者核实最新信息。"


def strip_stray_llm_tags(text: str) -> str:
    """剥离 LLM 输出泄漏的包装标签（</refine> 等），并清理残留空白。"""
    if not text:
        return text
    cleaned = _STRAY_TAG_RE.sub("", text)
    if cleaned != text:
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned).strip()
    return cleaned


def _ensure_knowledge_markers(text: str) -> str:
    """Honest 模式输出后置补齐 llm-knowledge 标记。

    prompt 规则（每段末尾添加标记）LLM 并不总遵守，导致知识性段落
    仍被证据门禁判为 high issue、进而被修订循环整段删除。
    这里按空行分块（与 review 的 _split_blocks 一致）逐段确定性补齐：
    无任何 evidence 注释的段落追加标记，使 evidence_ids 恰为
    [llm-knowledge] 而豁免门禁。
    """
    if not text:
        return text
    out: list[str] = []
    for para in text.split("\n\n"):
        stripped = para.strip()
        if (
            stripped
            and "<!-- evidence:" not in stripped
            and not stripped.startswith(("#", "!", "|"))
        ):
            para = para.rstrip() + f" {KNOWLEDGE_MARKER_TEXT}"
        out.append(para)
    return "\n\n".join(out)


def _to_text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


# 时效话题的证据分层：metadata-only（只有标题/摘要、论文未真正解析）或
# 离题论文池中的学术卡不再进入正文强制引用，降级为文末「延伸阅读」背景参考。
def _is_background_card(card: dict[str, Any], papers_off_topic: bool) -> bool:
    # 历史数据里元数据兜底卡的 source_type 可能为空串/None，同样按学术卡处理
    st = _to_text(card.get("source_type")).lower()
    if st not in ("academic", ""):
        return False
    if papers_off_topic:
        return True
    # 权威标记：建卡时写下的 Metadata-only limitation。
    # 不用 chunk_ids 缺失推断（正常学术卡在某些路径下也不带该键）。
    limitations = _to_text(card.get("limitations")).lower()
    return "metadata-only" in limitations


# 有证据章节里，无 evidence 注释的段落视为知识性补写；占比超过上限时
# 从段落序列尾部裁掉超额部分，防止证据正文被无标注推演稀释。
KNOWLEDGE_PARAGRAPH_MAX_RATIO = 0.3


def _balance_knowledge_paragraphs(text: str, max_ratio: float = KNOWLEDGE_PARAGRAPH_MAX_RATIO) -> str:
    if not text:
        return text
    paras = [p for p in text.split("\n\n") if p.strip()]
    # 结构性行（标题/引用块/表格）原样保留，不参与占比计算
    structural = lambda p: p.strip().startswith(("#", ">", "!", "|"))
    body = [p for p in paras if not structural(p)]
    if not body:
        return text
    uncited = [p for p in body if "<!-- evidence:" not in p]
    # 保底 1 个：过渡段/单段补充分析不应因小节段落少而被整段裁空
    allowed = max(1, int(len(body) * max_ratio + 0.5))
    drop_count = max(0, len(uncited) - allowed)
    # 从尾部裁掉超额的知识性段落：LLM 补写多出现在章节末尾，
    # 砍尾部对行文连贯性伤害最小
    to_drop: set[int] = set()
    if drop_count:
        for p in reversed(uncited):
            if len(to_drop) >= drop_count:
                break
            to_drop.add(id(p))
    out: list[str] = []
    for p in paras:
        if structural(p) or "<!-- evidence:" in p:
            out.append(p)
        elif id(p) in to_drop:
            continue
        else:
            out.append(_ensure_knowledge_markers(p))
    return "\n\n".join(out)


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


def _recency_adjustment(card: dict[str, Any]) -> float:
    """时效话题里 web 源的发布时间调分：一周内 +0.25 → 一年外 -0.2。

    published_hint 缺失（学术卡 / 解析失败的页面）时为 0，不影响学术排序。
    """
    hint = _to_text(card.get("published_hint"))
    if not hint or _to_text(card.get("source_type")).lower() not in ("web", "community"):
        return 0.0
    try:
        published = datetime.strptime(hint[:10], "%Y-%m-%d")
    except Exception:
        return 0.0
    age_days = (datetime.now() - published).days
    if age_days < 0:
        return 0.25
    if age_days <= 7:
        return 0.25
    if age_days <= 30:
        return 0.15
    if age_days <= 90:
        return 0.05
    if age_days <= 365:
        return -0.05
    return -0.2


def _current_date_line() -> str:
    return datetime.now().strftime("当前日期：%Y年%m月%d日。撰写时以该日期为「现在」，")


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
        score = (
            _strength_rank(strength) * 0.35
            + relevance * 0.5
            + quality
            + _recency_adjustment(card)
        )
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
    or returns an invalid result after retries.
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

    from app.services.llm_service import chat_completion
    import json as _json

    if zh_mode:
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
    else:
        system_prompt = (
            "You are a senior content architect specializing in structuring technical articles. "
            "Plan the chapter structure that best conveys the core message based on the research topic and available evidence."
        )
        article_type_label = {
            "wechat_article": "in-depth technical article",
            "literature_review": "literature review",
            "policy_report": "policy analysis report",
            "academic_draft": "academic draft",
        }.get(article_type, article_type)

        user_prompt = (
            f"Article type: {article_type_label}\n"
            f"Research topic: {project_title}\n"
            f"Research question: {research_question}\n\n"
            f"Available evidence themes:\n{evidence_summary}\n\n"
            f"Plan 6 section headings for this article.\n\n"
            "Requirements:\n"
            "1. Section headings must be tightly focused on the specific research topic\n"
            "2. Do NOT use generic names like: Introduction, Key Findings, Case Studies, Recommendations, Conclusion\n"
            "3. Sections should follow a clear cognitive logic: background → core methods → data/evidence → analysis → outlook\n"
            "4. Headings should be concise (2-6 words) and use domain-specific terminology\n"
            "5. Output a JSON array only, e.g.: [\"Section 1\", \"Section 2\", \"Section 3\", \"Section 4\", \"Section 5\", \"Section 6\"]\n"
            "6. Output ONLY the JSON array, nothing else"
        )

    for attempt in range(2):
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
                        logger.info("Generated %d topic-specific sections via LLM (attempt %d)", len(sections), attempt + 1)
                        return sections
                    logger.warning("LLM returned %d sections (need 4-8), attempt %d", len(sections), attempt + 1)
            else:
                logger.warning("LLM returned empty response for section generation, attempt %d", attempt + 1)
        except Exception:
            logger.exception("Topic-specific section generation failed (attempt %d)", attempt + 1)

    logger.warning("Falling back to default sections for article_type=%s after 2 LLM attempts", article_type)
    return default_sections


def plan_article_sections(
    article_type: str,
    project_title: str,
    research_question: str,
    evidence_cards: list[dict[str, Any]],
) -> list[str]:
    """Public wrapper around ``_generate_topic_sections``.

    Called by the ``thesis_thread`` graph node so section headings exist at
    outline stage -- before drafting -- letting ``plan_figures`` (F1) and the
    writing prompt operate on the real section list.
    """
    return _generate_topic_sections(article_type, project_title, research_question, evidence_cards)


def build_thesis_statement(
    project_title: str,
    research_question: str,
    article_type: str,
    evidence_cards: list[dict[str, Any]],
) -> str:
    """Distill the article's argument line (W1): core claim + 2-3 evidence pillars + expected conclusion.

    Runs as the ``thesis_thread`` node before draft generation so every section
    writes toward one shared thesis instead of standalone paragraphs. Falls back
    to a template when the LLM call fails -- the workflow must never block on it.
    """
    evidence_themes: list[str] = []
    for card in evidence_cards[:16]:
        etype = _to_text(card.get("evidence_type"), "general")
        claim = _to_text(card.get("_clean_claim") or card.get("claim"), "")[:120]
        if claim:
            evidence_themes.append(f"- [{etype}] {claim}")
    evidence_summary = "\n".join(evidence_themes[:10]) if evidence_themes else "（暂无证据概要）"

    zh_mode = _contains_cjk(project_title + research_question)

    from app.services.llm_service import chat_completion

    if zh_mode:
        system_prompt = (
            "你是一位论文主笔，擅长从研究问题与证据中提炼全文的论点主线。"
            "你输出的论点主线将作为各章节写作的统一上下文，确保全文论证连贯、不互相矛盾。"
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
            f"请提炼本文的论点主线，直接输出 3-5 句话：\n"
            f"1. 核心主张：本文最终要论证什么观点（1 句）\n"
            f"2. 证据支柱：支撑该主张的 2-3 个关键证据支柱，须出自上面的证据概览（2-3 句）\n"
            f"3. 预期结论：文章将如何收束论证（1 句）\n\n"
            f"要求：论点必须能在现有证据支撑下成立，禁止编造证据；"
            f"输出纯文本，不要编号、不要标题、不要 markdown 标记。"
        )
    else:
        system_prompt = (
            "You are a lead paper writer. Distill a thesis the whole article argues for. "
            "Your output is used as shared context for every section, so it must be coherent and evidence-grounded."
        )
        user_prompt = (
            f"Article type: {article_type}\nTopic: {project_title}\nResearch question: {research_question}\n\n"
            f"Evidence overview:\n{evidence_summary}\n\n"
            f"Output 3-5 plain sentences: (1) the core claim this article argues for; "
            f"(2) 2-3 evidence pillars from the overview that support it; (3) the expected conclusion. "
            f"Do not invent evidence. Plain text only, no numbering, no markdown."
        )

    for attempt in range(2):
        try:
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=600,
                timeout=60.0,
            )
            if result.get("error") and "reasoning consumed" in result["error"].lower():
                logger.warning("Thesis LLM attempt %d failed (reasoning consumed tokens), retrying...", attempt + 1)
                continue
            text = (result.get("content") or "").strip()
            if text:
                return text
        except Exception as exc:
            logger.exception("Thesis LLM attempt %d raised unexpected error", attempt + 1)

    # Fallback: template thesis so the workflow continues without blocking.
    pillars = "; ".join(claim.split("] ", 1)[-1] for claim in evidence_themes[:3]) or "现有证据"
    logger.warning("Falling back to template thesis for %s after 2 LLM attempts", project_title)
    return (
        f"本文围绕研究问题「{research_question}」展开，核心主张是：{project_title}。"
        f"论证将建立在三条证据支柱之上：{pillars}。"
        f"文章最后将综合这些证据，给出可验证的结论与展望。"
    )


def _section_tail(text: str, max_len: int = 120) -> str:
    """Last 2-3 sentences of a written section, passed to the next section as opening context (W2)."""
    body = "\n".join(
        line
        for line in (text or "").splitlines()
        if line.strip() and not line.strip().startswith("<!--")
    )
    sentences = _split_sentences(body)
    tail = "".join(sentences[-3:]).strip()
    if len(tail) > max_len:
        tail = tail[-max_len:]
    return tail


def _section_lead(section: str, zh_mode: bool, section_index: int = 0, total_sections: int = 1) -> str:
    if not zh_mode:
        if section_index == 0:
            return "To begin with,"
        if section_index == total_sections - 1:
            return "In summary,"
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
    if section in mapping:
        return mapping[section]
    # Position-based fallback for LLM-generated section names
    if section_index == 0:
        return "首先来看"
    if section_index == total_sections - 1:
        return "综合以上分析"
    # Rotate through diverse leads for middle sections
    _middle_leads = ["进一步分析", "值得关注的是", "从另一个角度看", "深入来看"]
    return _middle_leads[section_index % len(_middle_leads)]


def _render_paragraph(section: str, card: dict[str, Any], zh_mode: bool, section_index: int = 0, total_sections: int = 1) -> str:
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
        lines = [f"{_section_lead(section, zh_mode, section_index, total_sections)}{claim}", f"<!-- evidence: {ev_id} -->"]
        if support and support != claim:
            lines.append(f"具体而言，{support}")
        if strength in {"low", "weak"}:
            lines.append("（该证据强度有限，需结合更多来源验证。）")
        return "\n\n".join(lines)

    lines = [f"{_section_lead(section, zh_mode, section_index, total_sections)} {claim}", f"<!-- evidence: {ev_id} -->"]
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


# W4: validate evidence ids the LLM cites — hallucinated ids must not reach
# the draft. This regex captures the id from ``<!-- evidence: card-1 -->``.
_EVIDENCE_ID_RE = re.compile(r"<!--\s*evidence:\s*([^>\s]+?)\s*-->")


def _cited_evidence_ids(text: str) -> set[str]:
    return set(_EVIDENCE_ID_RE.findall(text or ""))


def _invalid_evidence_ids(text: str, valid_ids: set[str]) -> list[str]:
    return sorted(_cited_evidence_ids(text) - valid_ids)


def _strip_invalid_evidence_comments(text: str, invalid_ids: list[str]) -> str:
    """Remove only the invalid evidence comments, keeping valid citations."""
    for iid in invalid_ids:
        text = re.sub(rf"<!--\s*evidence:\s*{re.escape(iid)}\s*-->", "", text)
    return text


def _format_cards_for_prompt(cards: list[dict[str, Any]]) -> str:
    from app.services.evidence_service import credibility_weight

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
        # S3: surface credibility so the writer weights weak sources explicitly.
        cred = card.get("credibility_weight") or credibility_weight(source_type, bool(card.get("doi")))
        published = _to_text(card.get("published_hint"))
        meta_bits = [f"strength={strength}", f"source={source_label}", f"credibility={cred}"]
        if published:
            meta_bits.append(f"published={published}")
        lines.append(f"[evidence: {ev_id}] " + " ".join(meta_bits))
        lines.append(f"  claim: {claim}")
        if support:
            lines.append(f"  supporting_text: {support}")
        lines.append("")
    return "\n".join(lines).strip()


# W6: per-article-type writing emphasis.
_TYPE_SPECIFIC_PROMPTS: dict[str, str] = {
    "academic_draft": (
        "- **学术严谨**：保持客观中立的学术语气，术语使用规范定义，"
        "论述须有明确的方法论依据，避免口语化与营销化表达\n"
        "- **结构规范**：遵循学术写作的引言-方法-结果-讨论结构，"
        "数据表述须附来源与统计说明\n"
    ),
    "policy_report": (
        "- **政策导向**：突出可操作性，每个论点尽量落到具体举措、"
        "成本效益或治理建议\n"
        "- **面向决策者**：先给结论再给依据，关键信息用加粗或列表突出，避免理论空转\n"
    ),
    "literature_review": (
        "- **批判综述**：按研究脉络与主题组织内容，明确各研究的方法论差异、"
        "共识与分歧，指出证据缺口与未来研究方向\n"
    ),
    "wechat_article": (
        "- **可读性优先**：善用案例、故事化引入与类比，让非专业读者也能读懂\n"
    ),
}


def _writing_system_prompt(article_type: str) -> str:
    """W6: base writing style prompt + article-type-specific emphasis."""
    prompt = (
        "你是一位资深内容编辑，擅长将学术证据转化为流畅、结构清晰的中文技术文章。\n"
        "写作风格要求：\n"
        "- 以连贯的段落叙述为主，同时适度使用**加粗**标注核心概念和关键数据\n"
        "- 每个章节可使用 **小标题**（如 **1. 多智能体训练机制**）组织子话题\n"
        "- 当需要列举对比数据或关键发现时，可使用编号列表（1. 2. 3.）呈现\n"
        "- 术语首次出现时需附带一句话解释\n"
        "- 段落之间必须有自然的逻辑过渡\n"
        "- 每个段落控制在 150-250 字以内，禁止出现超过 300 字的长段落\n"
        "- 每个举例都必须有具体的数据、具名的案例或可查证的来源支撑，"
        "绝不使用'某公司'、'某些企业'等模糊指代\n"
        "- **去 AI 腔（W5）**：段落长度要有变化（长短交替），"
        "禁止以'随着…的发展'、'近年来'、'总的来说'、'值得注意的是'等模板句开头；"
        "句式多样化，长短句穿插，偶尔使用短句制造节奏；避免每段都是'先背景后结论'的三段式结构\n"
    )
    extra = _TYPE_SPECIFIC_PROMPTS.get(article_type)
    if extra:
        prompt += "\n文章类型专属要求（W6）：\n" + extra
    return prompt


def _section_role_instruction(
    section: str,
    section_index: int,
    total_sections: int,
) -> str:
    """W6: role-specific emphasis per section (intro/method/results/discussion/conclusion)."""
    s = section.lower()
    if section_index == 0:
        return (
            "章节角色：引言。请铺垫背景、点出研究问题与意义，"
            "并自然预告后文将如何展开；篇幅可略短，避免堆砌细节。"
        )
    if section_index == total_sections - 1:
        return (
            "章节角色：结论。请收束全文论点主线，总结关键发现，"
            "给出展望或行动建议；禁止引入本节之前未出现的新证据或新观点。"
        )
    if any(k in s for k in ("方法", "方案", "实现", "架构", "技术", "机制", "approach", "method", "framework")):
        return (
            "章节角色：方法/技术。请侧重技术原理与实现细节的深入剖析，"
            "篇幅可适当最长，多用具体机制、流程与设计决策支撑论述。"
        )
    if any(k in s for k in ("结果", "实验", "评测", "性能", "数据", "result", "evaluation", "experiment", "benchmark")):
        return (
            "章节角色：结果。请以数据呈现为主，关键指标用加粗标注，"
            "并说明结果与预期/文献的对照关系。"
        )
    if any(k in s for k in ("讨论", "分析", "对比", "评估", "启示", "discussion", "analysis")):
        return (
            "章节角色：讨论。请综合解读证据，对比不同来源观点，"
            "坦诚说明局限性与证据缺口。"
        )
    return ""


def _llm_write_section(
    section: str,
    project_title: str,
    research_question: str,
    article_type: str,
    section_cards: list[dict[str, Any]],
    word_count: int,
    section_index: int = 0,
    total_sections: int = 1,
    thesis_statement: str = "",
    prev_tail: str = "",
    next_section: str = "",
    figure_plan: str = "",
    conflict_note: str = "",
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
            section_index=section_index,
            total_sections=total_sections,
            thesis_statement=thesis_statement,
            prev_tail=prev_tail,
            next_section=next_section,
            figure_plan=figure_plan,
        )

    system_prompt = _writing_system_prompt(article_type)

    user_prompt = (
        f"{_current_date_line()}\n\n"
        f"请为以下章节撰写连贯的正文段落。\n\n"
        f"章节：{section}\n"
        f"文章类型：{article_type}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n"
    )
    if thesis_statement:
        user_prompt += f"全文论点主线：{thesis_statement}\n"
    if prev_tail:
        user_prompt += f"上一节结尾：{prev_tail}\n"
    if next_section:
        user_prompt += f"下一节标题：{next_section}\n"
    if figure_plan:
        user_prompt += (
            f"本节配图规划：{figure_plan}。请在正文最合适的位置自然插入引用占位符 "
            f"{{{{ref:fig:N}}}}（N 为规划中的图号，后台会自动替换为'如图N所示'）。"
            f"占位符应紧跟论述该图内容的句子之后，不要单独成段；若位置不自然可省略。\n"
        )
    if conflict_note:
        user_prompt += f"**证据冲突提示（S4）**：{conflict_note}\n"
    role_instruction = _section_role_instruction(section, section_index, total_sections)
    if role_instruction:
        user_prompt += f"{role_instruction}\n"
    user_prompt += (
        f"\n可用的证据卡片（每条标注了来源类型）：\n"
        f"{cards_text}\n\n"
        f"写作要求：\n"
        f"1. **主题守卫**：所有内容必须紧密围绕研究主题「{project_title}」。如果某条证据与研究主题关联性弱，请忽略该证据，不要将其写入正文。绝不引入与研究主题无关的内容。\n"
        f"2. **来源分层**：优先使用学术论文来源的证据作为核心论点支撑，网络来源和社区讨论可作为补充视角和案例，背景知识用于铺垫和过渡。在引用不同来源时，请自然地融入来源描述（如'据学术研究显示'、'行业分析指出'、'社区讨论中提到'）。\n"
        f"   **时效规则**：证据卡带 published= 日期时，优先引用发布时间最新的来源；引用 90 天以前的信息须在文中注明时间背景（如'2025年9月的报道'），禁止把旧闻当作当前事件陈述。\n"
        f"   **可信度规则（S3）**：低可信度来源（credibility<0.7，即网络来源/社区讨论/背景知识）的内容必须以「据网络资料」「社区讨论中提到」「背景知识显示」等限定语标注，只能作为观点和案例呈现，禁止将其作为确定事实直接陈述；学术来源（credibility>=0.7）可作为事实性结论使用。\n"
        f"3. **全文连贯（W2）**：全文围绕论点主线展开，本节内容必须支持主线主张，禁止与论点主线矛盾。"
        f"如提供了上一节结尾，本节开头需自然承接其内容，避免重复其论述；"
        f"如提供了下一节标题，本节结尾应抛出引子或悬念，自然引出下一节。\n"
        f"4. **格式要求**：\n"
        f"   - 每个章节写 3-5 个段落，每段控制在 150-250 字\n"
        f"   - 段落之间用空行分隔，保证视觉呼吸感\n"
        f"   - 在关键概念、核心发现、重要数据处使用 **加粗** 标注\n"
        f"   - 当需要对比数据或列举要点时，可使用编号列表（1. 2. 3.）\n"
        f"   - 对于复杂的子话题，可使用 **小标题**（如 **1. 训练效率对比**）分隔\n"
        f"5. **内容去重**：本节内容必须与其他章节不重复。每个章节聚焦独特的分析维度，禁止在不同章节中反复陈述相同的论点或数据。\n"
        f"6. 每个核心观点必须在对应位置插入 <!-- evidence: {{id}} --> 注释。\n"
        f"7. 术语首次出现需附带一句话解释。\n"
        f"8. **深度展开**：每个章节应写 3-5 个段落（约{word_count}字），从多个角度深入论述——技术原理、性能数据、因果关系、实践案例。不要只停留在表面描述。\n"
        f"9. **批判性综合（W3）**：论述中须识别证据之间的共识点、分歧点与证据缺口。"
        f"当不同来源结论存在差异时，必须对比呈现并解释差异（如'Smith(2023) 报告 89%，而 Lee(2024) 仅 72%，差异可能源于评测标准或样本规模不同'），"
        f"不得只罗列结果而不分析。\n"
        f"10. 如果证据不足以支撑深度论述，请基于研究问题进行合理的推演和分析性论述，用'从技术角度分析'、'可以合理推断'等表述引导推理过程。\n"
    )
    if article_type == "literature_review":
        user_prompt += (
            f"**文献综述要求（W3）**：本章为文献综述的一部分，请按研究脉络组织内容："
            f"说明各研究的先后演进关系、方法论差异、共识与分歧，避免平铺直叙地逐篇罗列。\n"
        )
    user_prompt += (
        f"11. **举例必须有据**：每当文中出现举例，必须附带至少一项具体论据支撑——具体的性能数据或基准测试分数、具名的真实项目或产品、已发表的研究报告结论。严禁使用'某公司'、'某些企业'、'相关领域'等模糊指代。\n"
        f"12. 你输出的 <!-- evidence: id --> 注释将被后台自动处理，无需在正文中提及或解释这些标记。正常撰写内容即可。"
        f"\n\n"
        f"目标字数：本节约 {word_count} 字。\n"
        f"请只输出正文段落，不要输出标题、总结或元信息。"
    )

    from app.services.llm_service import chat_completion

    # W4: the writer may only cite evidence ids that actually exist.
    valid_evidence_ids = {_to_text(card.get("id"), "") for card in section_cards}
    valid_ids_text = ", ".join(f"'{i}'" for i in sorted(valid_evidence_ids))
    user_prompt += (
        f"\n可用引用 ID（只能引用以下 ID，禁止编造任何其他 ID）："
        f"{valid_ids_text or '（无）'}\n"
    )

    # Token budget: ensure enough tokens for substantive content
    base_max_tokens = min(16384, max(2048, word_count * 4))

    last_text = ""
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
            text = strip_stray_llm_tags(result.get("content", ""))
            if text:
                # W4: hallucinated evidence ids -> regenerate once with the
                # correction; if it still fails, drop the invalid citations.
                last_text = text
                invalid_ids = _invalid_evidence_ids(text, valid_evidence_ids)
                if invalid_ids:
                    logger.warning(
                        "LLM cited non-existent evidence ids %s, regenerating section",
                        invalid_ids,
                    )
                    user_prompt += (
                        f"\n注意：上一轮生成的引用包含不存在的 ID：{', '.join(invalid_ids)}。"
                        f"可用引用 ID 只有：{valid_ids_text}。请全部改为真实存在的 ID，重新输出本节。"
                    )
                    continue
                return _balance_knowledge_paragraphs(text).strip()
        except Exception as exc:
            logger.exception("LLM writing attempt %d raised unexpected error", attempt + 1)

    # W4 safety net: strip any hallucinated citations that survived retries.
    if last_text:
        balanced = _balance_knowledge_paragraphs(last_text)
        return _strip_invalid_evidence_comments(
            balanced, _invalid_evidence_ids(balanced, valid_evidence_ids)
        ).strip()

    # Fallback: template rendering
    zh_mode = _contains_cjk(project_title + research_question)
    paragraphs: list[str] = []
    for card in section_cards:
        paragraphs.append(_render_paragraph(section, card, zh_mode, section_index, total_sections))
    return "\n\n".join(paragraphs)


def _llm_write_section_honest(
    section: str,
    project_title: str,
    research_question: str,
    article_type: str,
    word_count: int,
    section_index: int = 0,
    total_sections: int = 1,
    thesis_statement: str = "",
    prev_tail: str = "",
    next_section: str = "",
    figure_plan: str = "",
) -> str:
    """Write a section using LLM's own knowledge when no evidence cards are available.

    This is the 'honest mode' fallback — the LLM writes based on its training knowledge
    and explicitly labels the content as knowledge-based rather than evidence-cited.
    """
    system_prompt = _writing_system_prompt(article_type)

    user_prompt = (
        f"{_current_date_line()}\n\n"
        f"请为以下章节撰写深度分析段落。\n\n"
        f"章节：{section}\n"
        f"文章类型：{article_type}\n"
        f"研究主题：{project_title}\n"
        f"研究问题：{research_question}\n"
    )
    if thesis_statement:
        user_prompt += f"全文论点主线：{thesis_statement}\n"
    if prev_tail:
        user_prompt += f"上一节结尾：{prev_tail}\n"
    if next_section:
        user_prompt += f"下一节标题：{next_section}\n"
    if figure_plan:
        user_prompt += (
            f"本节配图规划：{figure_plan}。请在正文最合适的位置自然插入引用占位符 "
            f"{{{{ref:fig:N}}}}（N 为规划中的图号，后台会自动替换为'如图N所示'）。"
            f"占位符应紧跟论述该图内容的句子之后，不要单独成段；若位置不自然可省略。\n"
        )
    role_instruction = _section_role_instruction(section, section_index, total_sections)
    if role_instruction:
        user_prompt += f"{role_instruction}\n"
    user_prompt += (
        f"\n**重要说明**：当前该章节没有直接可用的文献证据。"
        f"请基于你对该主题的已有知识撰写深度分析内容，但需遵守以下规则：\n\n"
        f"1. 只陈述你有较高置信度的事实和分析\n"
        f"2. 对于不确定的信息，请用'据报道'、'业内分析认为'等措辞标注不确定性，但仍需给出具体的名称或数据\n"
        f"3. **时间基准**：以上方给定的当前日期为「现在」。如需提及行业事件（价格调整、版本发布等），必须核对事件的真实时间并在文中写明年月，禁止把过去的旧闻当作正在发生的当前事件\n"
        f"4. **可以引用**公开的基准测试数据、已知名模型/产品的性能指标、已发表的技术报告结论\n"
        f"5. **举例必须具体**：禁止使用'某公司'、'某些企业'、'相关领域'等模糊指代。每个例子必须包含具体的公司/产品/模型名称、具体的性能数据或技术指标、可查证的来源\n"
        f"6. 重点放在技术架构分析、性能对比、成本效益评估和行业趋势上\n"
        f"7. **格式要求**：\n"
        f"   - 每个章节写 3-5 个段落，每段 150-250 字\n"
        f"   - 段落之间用空行分隔\n"
        f"   - 在关键概念、核心数据处使用 **加粗** 标注\n"
        f"   - 当需要对比数据或列举要点时，可使用编号列表\n"
        f"   - 对于复杂的子话题，可使用 **小标题** 分隔\n"
        f"8. **内容去重**：本节内容必须与其他章节不重复。每个章节聚焦独特的分析维度。\n"
        f"9. 在段落开头自然地标注本节为知识性分析（如'从行业分析角度来看'、'综合公开信息可知'等）\n"
        f"10. **深度要求**：写 3-5 个段落，从多个维度展开论述，包括技术原理、实际表现、对比分析、应用场景等\n"
        f"11. **知识性标注**：每段结尾必须添加 `<!-- evidence: llm-knowledge -->` 注释（后台据此识别该段为知识性分析而非文献引用，避免被证据门禁误判）。此注释不可省略。\n\n"
        f"目标字数：本节约 {word_count} 字。\n"
        f"请只输出正文段落，不要输出标题、总结或元信息。"
    )

    from app.services.llm_service import chat_completion

    # 重试一次：honest 模式没有模板兜底，一次瞬时失败就直接落占位符
    # 会把整节变成空节，代价太高。
    for attempt in range(2):
        try:
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max(4096, word_count * 4) * (2 ** attempt),
                timeout=120.0,
            )
            text = strip_stray_llm_tags(result.get("content", ""))
            if text:
                return (
                    _ensure_knowledge_markers(text).rstrip()
                    + f"\n\n{KNOWLEDGE_SECTION_NOTE}"
                )
            logger.warning(
                "Honest mode section writing attempt %d returned empty content", attempt + 1
            )
        except Exception:
            logger.exception(
                "Honest mode section writing attempt %d failed", attempt + 1
            )

    return f"（本节暂无直接证据支撑。基于已有知识的分析内容待补充。）"


def _estimate_word_count(
    article_type: str,
    evidence_card_count: int,
    sections: list[str] | None = None,
) -> tuple[int, list[int]]:
    """Estimate total and per-section word count based on article type and evidence richness.

    The heuristic uses a type-specific baseline and scales it by evidence density,
    capped to avoid runaway token usage.

    When *sections* is provided (LLM-generated headings), weights are inferred
    positionally: first section ≈ intro (0.8×), last section ≈ conclusion (0.7×),
    middle sections ≈ core analysis (1.15×).  Known template names still get
    precise weights for backwards compatibility.
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

    # Known-name weights for backwards compat with hardcoded templates
    _known_weights: dict[str, float] = {
        "引言": 0.8, "问题引入": 0.8, "问题界定": 0.9,
        "方法": 1.3, "方法与证据": 1.3,
        "关键证据": 1.2, "结果": 1.2, "关键发现": 1.2, "证据对比": 1.2,
        "讨论": 1.1, "核心争议": 1.1, "案例与启发": 1.1, "政策建议": 1.1,
        "实施路径": 1.0, "风险与限制": 0.9, "局限": 0.9,
        "结论": 0.7, "结语": 0.7, "研究空白": 0.9,
    }

    if sections is None:
        sections = _article_sections(article_type)

    n = len(sections)
    raw_weights: list[float] = []
    for i, s in enumerate(sections):
        if s in _known_weights:
            raw_weights.append(_known_weights[s])
        elif i == 0:
            raw_weights.append(0.8)       # first section ≈ intro
        elif i == n - 1:
            raw_weights.append(0.7)       # last section ≈ conclusion
        else:
            raw_weights.append(1.15)      # middle sections ≈ core analysis

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
    thesis_statement: str = "",
    sections: list[str] | None = None,
    figure_plans: list[dict[str, Any]] | None = None,
    conflict_groups: list[dict[str, Any]] | None = None,
    papers_off_topic: bool = False,
) -> tuple[str, list[str]]:
    """Generate the full draft article as Markdown.

    Returns a tuple of ``(content_md, section_names)`` so the caller can
    pass the actual section names to downstream nodes (e.g. image injection).
    """
    all_cards = _sorted_cards(evidence_cards, research_question)
    zh_mode = _contains_cjk(project_title + research_question)

    # ── 证据分层：擦边论文（metadata-only / 离题池）→ 背景参考 ──────────
    # 时效话题往往没有对口论文；这类卡片强塞进正文只会产生伪引用，
    # 统一降级到文末「延伸阅读」，正文让位给 web/社区/知识证据。
    # 注意：背景层从原始输入判定 —— _sorted_cards 在存在强相关卡时会
    # 静默丢弃弱相关池，从其输出取背景卡会漏掉全部擦边论文。
    background_ids = {
        _to_text(c.get("id")) for c in evidence_cards if _is_background_card(c, papers_off_topic)
    }
    cards = [c for c in all_cards if _to_text(c.get("id")) not in background_ids]
    background_cards: list[dict[str, Any]] = []
    seen_papers: set[str] = set()
    for c in evidence_cards:
        cid = _to_text(c.get("id"))
        if cid in background_ids:
            paper_key = _to_text(c.get("paper_id")) or cid
            if paper_key not in seen_papers:
                seen_papers.add(paper_key)
                background_cards.append(c)

    lines: list[str] = [
        f"# {project_title}",
        "",
    ]

    if not cards and not background_cards:
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
    if sections is None:
        sections = _generate_topic_sections(article_type, project_title, research_question, cards)
    usable = cards[: min(36, len(cards))]

    total_target, section_targets = _estimate_word_count(article_type, len(usable), sections)

    # Distribute cards to sections by topic relevance (not round-robin)
    n_sec = max(1, len(sections))
    section_buckets: list[list[dict[str, Any]]] = [[] for _ in sections]
    section_tokens = [_tokenize_query(s) for s in sections]
    assigned_ids: set[str] = set()

    for card in usable:
        card_text = (
            (card.get("claim") or "") + " " + (card.get("supporting_text") or "")
        ).lower()
        best_idx, best_score = 0, -1.0
        for si, st in enumerate(section_tokens):
            score = _text_relevance(card_text, st)
            if score > best_score:
                best_score, best_idx = score, si
        if best_score > 0.0 and best_idx >= 0:
            section_buckets[best_idx].append(card)
            assigned_ids.add(_to_text(card.get("id")))

    # Distribute unassigned cards to the emptiest buckets
    for card in usable:
        cid = _to_text(card.get("id"))
        if cid not in assigned_ids:
            min_idx = min(range(len(section_buckets)), key=lambda i: len(section_buckets[i]))
            section_buckets[min_idx].append(card)

    # Cap each bucket to 4 cards
    for bucket in section_buckets:
        del bucket[4:]

    # Map figure plans to sections by exact title (plans come from the same list).
    plan_by_section: dict[str, dict[str, Any]] = {}
    if figure_plans:
        plan_by_section = {p.get("section", ""): p for p in figure_plans}

    # S4: group_id lookup for conflict hints inside sections.
    group_by_card_id: dict[str, dict[str, Any]] = {}
    if conflict_groups:
        for group in conflict_groups:
            for cid in group.get("card_ids", []):
                group_by_card_id[str(cid)] = group

    prev_tail = ""
    for idx, section in enumerate(sections):
        lines.append(f"## {section}")
        section_cards = section_buckets[idx] if idx < len(section_buckets) else []

        plan = plan_by_section.get(section)
        figure_plan = ""
        if plan:
            figure_plan = (
                f"图{plan.get('fig_index', idx + 1)}（{plan.get('kind', 'illustration')}）："
                f"{plan.get('caption', '')}；数据证据：{plan.get('evidence_id', '') or '（无）'}"
            )

        conflict_note = ""
        conflict_groups_in_section: dict[str, dict[str, Any]] = {}
        for card in section_cards:
            group = group_by_card_id.get(_to_text(card.get("id")))
            if group:
                conflict_groups_in_section[group.get("group_id", "")] = group
        if conflict_groups_in_section:
            parts = []
            for gid, group in conflict_groups_in_section.items():
                parts.append(
                    f"{gid}（冲突主题：{group.get('topic', '相关证据结论相反')}，"
                    f"涉及证据卡：{', '.join(group.get('card_ids', []))}）"
                )
            conflict_note = (
                f"本节证据存在冲突：{'；'.join(parts)}。"
                f"引用这些证据时必须批判性对比双方结论与可能的分歧原因"
                f"（如研究设计、样本、指标差异），不得只取其中一方，也不得含糊其辞。"
            )

        section_text = _llm_write_section(
            section=section,
            project_title=project_title,
            research_question=research_question,
            article_type=article_type,
            section_cards=section_cards,
            word_count=section_targets[idx] if idx < len(section_targets) else section_targets[-1],
            section_index=idx,
            total_sections=n_sec,
            thesis_statement=thesis_statement,
            prev_tail=prev_tail,
            next_section=sections[idx + 1] if idx + 1 < len(sections) else "",
            figure_plan=figure_plan,
            conflict_note=conflict_note,
        )
        lines.append(section_text)
        lines.append("")
        prev_tail = _section_tail(section_text)

    # ── 延伸阅读（背景文献）──────────────────────────────────────
    # 擦边论文不进正文，但以"背景参考"身份集中列出：保留可追溯性，
    # 同时向读者明示这些文献与主题只是侧面相关。
    if background_cards:
        lines.append("## 延伸阅读（背景文献）")
        lines.append(
            "以下文献与本文主题为侧面相关（关键词相关但非直接研究本话题），"
            "作为背景参考列出，不构成正文论点的直接证据。"
        )
        lines.append("")
        for card in background_cards[:8]:
            title = _to_text(
                card.get("paper_title") or card.get("_clean_claim") or card.get("claim"),
                "（无标题）",
            )[:120]
            ev_id = _to_text(card.get("id"))
            lines.append(f"- {title} {_evidence_comment([ev_id]) if ev_id else ''}".rstrip())
        lines.append("")

    raw = "\n".join(lines).strip()
    # W5: anti-AI-tone is enforced in the writing prompt itself; the
    # post-processing pass is only a light fallback.
    processed = de_ai_markdown(raw, intensity=0.2)
    # NOTE: evidence comments (<!-- evidence: id -->) are intentionally kept here
    # so that review/revision nodes can compute evidence_coverage correctly.
    # They are stripped in the export node before final output.
    return processed, sections
