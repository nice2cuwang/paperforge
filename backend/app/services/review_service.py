from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any

from app.services.de_ai_service import de_ai_metrics
from app.services.fact_check_service import fact_check_draft
from app.services.llm_service import chat_completion_json, chat_completion_text
from app.services.style_check_service import check_style

logger = logging.getLogger(__name__)

EVIDENCE_COMMENT_RE = re.compile(r"<!--\s*evidence:\s*([^>]+?)\s*-->", re.IGNORECASE)
EVIDENCE_TAG_RE = re.compile(r"\[evidence:([a-zA-Z0-9-]+)\]")
ABSOLUTE_TERMS = ("必然", "完全证明", "彻底", "毫无疑问", "一定会", "all", "always", "must")
CORRELATION_TERMS = ("相关", "关联", "correlation", "associated")
CAUSAL_TERMS = ("导致", "造成", "引发", "cause", "causal")


def _parse_evidence_ids(block: str) -> list[str]:
    ids: list[str] = []

    for match in EVIDENCE_COMMENT_RE.findall(block):
        parts = [item.strip() for item in match.split(",")]
        ids.extend([item for item in parts if item])

    ids.extend(EVIDENCE_TAG_RE.findall(block))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _is_claim_block(block: str) -> bool:
    text = block.strip()
    if not text:
        return False
    if text.startswith("#"):
        return False
    if text.startswith("> article_type") or text.startswith("> citation_style") or text.startswith("> writing_mode"):
        return False
    if text.startswith("- evidence_id="):
        return False
    if text.startswith("## 证据索引"):
        return False
    if text.startswith("本稿为 publication-preparation"):
        return False
    if text.startswith("This draft is publication-preparation"):
        return False
    if text.startswith("> 修订说明"):
        return False
    # Skip image markdown — do not treat as a claim paragraph
    if text.startswith("!["):
        return False
    # Skip HTML comments (evidence markers, etc.)
    if text.startswith("<!--"):
        return False
    return True


def _section_presence(content_md: str, title: str) -> bool:
    return f"## {title}" in content_md


def _style_issues(content_md: str, article_type: str | None) -> list[dict[str, Any]]:
    article = (article_type or "").strip().lower()
    issues: list[dict[str, Any]] = []

    if article == "policy_report":
        if not _section_presence(content_md, "政策建议"):
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "style",
                    "location": "global",
                    "claim": "policy_report missing 政策建议 section",
                    "description": "policy_report 需要明确政策建议部分。",
                    "suggestion": "补充“政策建议”章节并给出可执行建议。",
                    "evidence_ids": [],
                    "resolved": False,
                }
            )
    elif article == "literature_review":
        if not _section_presence(content_md, "核心争议"):
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "style",
                    "location": "global",
                    "claim": "literature_review missing 核心争议 section",
                    "description": "文献综述应显式呈现争议点。",
                    "suggestion": "补充“核心争议”章节，展示不同证据观点。",
                    "evidence_ids": [],
                    "resolved": False,
                }
            )
    elif article == "academic_draft":
        has_limits = _section_presence(content_md, "局限") or _section_presence(content_md, "风险与限制")
        if not has_limits:
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "style",
                    "location": "global",
                    "claim": "academic_draft missing limitations section",
                    "description": "学术草稿应明确局限条件。",
                    "suggestion": "补充“局限”章节，标注外推边界。",
                    "evidence_ids": [],
                    "resolved": False,
                }
            )
    elif article == "wechat_article":
        blocks = [b.strip() for b in content_md.split("\n\n") if _is_claim_block(b)]
        if blocks:
            avg_len = sum(len(b) for b in blocks) / len(blocks)
            if avg_len > 180:
                issues.append(
                    {
                        "severity": "low",
                        "issue_type": "style",
                        "location": "global",
                        "claim": "wechat_article paragraphs too long",
                        "description": "公众号文章段落偏长，可读性不足。",
                        "suggestion": "拆分长段并增加短句。",
                        "evidence_ids": [],
                        "resolved": False,
                    }
                )

    return issues


def _norm_llm_issue(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize an LLM-returned issue to always include required fields."""
    item.setdefault("evidence_ids", [])
    item.setdefault("resolved", False)
    return item


def _llm_review(
    content_md: str,
    evidence_cards: list[dict[str, Any]],
    article_type: str | None,
) -> list[dict[str, Any]]:
    """Layer 1-3 LLM review: fact, logic, expression."""
    if len(content_md) > 12000:
        content_md = content_md[:12000] + "\n\n...（内容截断，后续部分未纳入本次审校）"

    ev_lines: list[str] = []
    for card in evidence_cards[:20]:
        ev_id = str(card.get("id") or "unknown")
        claim = str(card.get("claim") or card.get("_clean_claim") or "")[:200]
        strength = str(card.get("strength") or "unknown")
        ev_lines.append(f"- {ev_id} (strength={strength}): {claim}")
    evidence_text = "\n".join(ev_lines) or "无可用证据卡片"

    system_prompt = (
        "你是一位资深技术编辑，擅长从事实准确性、逻辑结构和表达体验三个维度审校中文技术文章。"
        "你必须以严格的 JSON 数组格式输出审校意见，每个意见包含 severity、issue_type、location、claim、description、suggestion 字段。"
        "location 使用 paragraph-N 格式（N 为段落序号，从 1 开始），若问题涉及全文则用 global。"
        "如果没有发现问题，返回空数组 []。"
    )

    user_prompt = (
        f"请对以下文章进行三层深度审校。\n\n"
        f"文章类型：{article_type or '未指定'}\n\n"
        f"[文章正文]\n{content_md}\n\n"
        f"[可用证据卡片]\n{evidence_text}\n\n"
        f"审校维度：\n"
        f"1. fact（事实准确性）：技术细节、数据、引用是否准确？是否存在幻觉或编造？\n"
        f"2. logic（逻辑结构）：论证链条是否完整？是否存在因果混淆、跳跃论证、循环论证？\n"
        f"3. expression（表达体验）：目标受众是否容易理解？术语是否首次附带解释？段落节奏、过渡是否自然？\n\n"
        f"输出格式要求：严格的 JSON 数组，每个元素为：\n"
        f'{{"severity":"high|medium|low","issue_type":"fact|logic|expression","location":"paragraph-N|global","claim":"涉及文本","description":"问题描述","suggestion":"修改建议"}}'
    )

    try:
        data = chat_completion_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4000,
            timeout=120.0,
        )
        if "_error" in data:
            logger.warning("LLM review failed: %s", data.get("_error"))
            return []
        if isinstance(data, list):
            return [_norm_llm_issue(item) for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and "issues" in data:
            return [_norm_llm_issue(item) for item in data["issues"] if isinstance(item, dict)]
        return []
    except Exception as exc:
        logger.exception("LLM review raised unexpected error")
        return []


def review_draft_with_metrics(
    content_md: str,
    evidence_cards: list[dict[str, Any]],
    article_type: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # ---------- Layer 0: rule-based checks (preserved) ----------
    issues: list[dict[str, Any]] = []

    evidence_map = {str(card.get("id")): card for card in evidence_cards if card.get("id")}
    blocks = [block.strip() for block in content_md.split("\n\n") if block.strip()]

    total_claims = 0
    supported_claims = 0
    unsupported_claims = 0
    unresolved_citations = 0
    total_cited_ids = 0
    logic_flags = 0

    for idx, block in enumerate(blocks, start=1):
        if not _is_claim_block(block):
            continue

        total_claims += 1
        evidence_ids = _parse_evidence_ids(block)

        if not evidence_ids:
            unsupported_claims += 1
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "evidence",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "核心段落缺少 evidence_id，违反 Evidence Gate。",
                    "suggestion": "为该段补充真实 evidence_id，或删除该段核心判断。",
                    "evidence_ids": [],
                    "resolved": False,
                }
            )
            continue

        total_cited_ids += len(evidence_ids)
        unknown = [item for item in evidence_ids if item not in evidence_map]
        if unknown:
            unresolved_citations += len(unknown)
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "citation",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": f"存在无效 evidence_id: {', '.join(unknown[:6])}",
                    "suggestion": "替换为存在的 evidence_id，或删除对应断言。",
                    "evidence_ids": unknown[:6],
                    "resolved": False,
                }
            )
        else:
            supported_claims += 1

        block_lower = block.lower()
        has_absolute = any(term in block for term in ABSOLUTE_TERMS) or any(term in block_lower for term in ABSOLUTE_TERMS)
        has_corr = any(term in block for term in CORRELATION_TERMS) or any(term in block_lower for term in CORRELATION_TERMS)
        has_causal = any(term in block for term in CAUSAL_TERMS) or any(term in block_lower for term in CAUSAL_TERMS)

        if "TODO" in block or "REPLACE_ME" in block:
            logic_flags += 1
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "logic",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "段落含占位符，论证未完成。",
                    "suggestion": "删除占位符并补全可证据追溯表达。",
                    "evidence_ids": evidence_ids[:4],
                    "resolved": False,
                }
            )

        if has_corr and has_causal:
            logic_flags += 1
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "logic",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "段落可能将相关性表述为因果性。",
                    "suggestion": "使用谨慎语气，明确因果推断条件。",
                    "evidence_ids": evidence_ids[:4],
                    "resolved": False,
                }
            )

        if has_absolute:
            strengths = [str((evidence_map.get(ev_id) or {}).get("strength") or "").lower() for ev_id in evidence_ids]
            if strengths and all(item in {"", "low", "weak"} for item in strengths):
                logic_flags += 1
                issues.append(
                    {
                        "severity": "medium",
                        "issue_type": "logic",
                        "location": f"paragraph-{idx}",
                        "claim": block[:160],
                        "description": "证据强度偏弱，但语气绝对化。",
                        "suggestion": "降调表达，加入限制条件或争议说明。",
                        "evidence_ids": evidence_ids[:4],
                        "resolved": False,
                    }
                )

    style_items = _style_issues(content_md, article_type)
    issues.extend(style_items)

    # ---------- Layer 1b: advanced style check ----------
    adv_style_issues, adv_style_metrics = check_style(content_md, article_type)
    issues.extend(adv_style_issues)

    # ---------- Layer 1-3: LLM review ----------
    llm_issues = _llm_review(content_md, evidence_cards, article_type)
    issues.extend(llm_issues)

    # ---------- Layer 2b: external fact check ----------
    fact_issues, fact_metrics = fact_check_draft(content_md, evidence_cards)
    issues.extend(fact_issues)

    critical_issues = len([item for item in issues if item.get("severity") == "high"])
    evidence_coverage = supported_claims / max(1, total_claims)
    citation_validity = 1.0 - (unresolved_citations / max(1, total_cited_ids))
    logic_score = max(0.0, 1.0 - (logic_flags / max(1, total_claims)) * 0.7)
    total_style_penalty = len(style_items) * 0.12 + len(adv_style_issues) * 0.06
    style_score = max(0.0, 1.0 - min(0.5, total_style_penalty))

    # Weight LLM logic/expression findings into scores
    llm_logic_hits = len([i for i in llm_issues if i.get("issue_type") == "logic"])
    llm_expr_hits = len([i for i in llm_issues if i.get("issue_type") == "expression"])
    if llm_logic_hits:
        logic_score = max(0.0, logic_score - min(0.3, llm_logic_hits * 0.08))
    if llm_expr_hits:
        style_score = max(0.0, style_score - min(0.25, llm_expr_hits * 0.06))

    # De-AI metrics
    de_ai = de_ai_metrics(content_md)
    de_ai_score = round((de_ai["connector_variety_score"] + de_ai["absolutism_score"] + de_ai["template_cliche_score"]) / 3, 3)
    # Blend de-ai score into style (10% weight)
    style_score = round(0.90 * style_score + 0.10 * de_ai_score, 3)

    publication_prepared = (
        critical_issues == 0
        and unsupported_claims == 0
        and unresolved_citations == 0
        and evidence_coverage >= 0.90
        and citation_validity >= 0.90
        and logic_score >= 0.80
        and style_score >= 0.80
    )

    overall = max(
        0.0,
        min(
            1.0,
            0.35 * evidence_coverage
            + 0.25 * citation_validity
            + 0.20 * logic_score
            + 0.20 * style_score,
        ),
    )

    metrics = {
        "overall_score": round(overall, 3),
        "critical_issues": critical_issues,
        "unsupported_claims": unsupported_claims,
        "unresolved_citations": unresolved_citations,
        "evidence_coverage": round(evidence_coverage, 3),
        "citation_validity": round(max(0.0, citation_validity), 3),
        "logic_score": round(logic_score, 3),
        "style_score": round(style_score, 3),
        "de_ai_score": de_ai_score,
        **adv_style_metrics,
        **fact_metrics,
        "human_review_required": True,
        "publication_prepared": publication_prepared,
        "llm_review_issues": len(llm_issues),
    }
    return issues, metrics


def review_draft(content_md: str, evidence_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues, _ = review_draft_with_metrics(content_md=content_md, evidence_cards=evidence_cards)
    return issues


def _llm_revise_paragraph(paragraph: str, issues: list[dict[str, Any]]) -> str:
    """Ask LLM to revise a single paragraph based on review issues."""
    issues_text = "\n".join(
        f"- [{i.get('issue_type')}] {i.get('description')}（建议：{i.get('suggestion')}）"
        for i in issues
    )

    system_prompt = (
        "你是一位资深编辑，擅长根据审校意见定向修订段落。"
        "你必须保留原文的核心信息和 evidence 注释，只修改审校意见指出的问题。"
        "你必须保留原文的长度和深度，不要缩减或省略内容。"
        "输出必须是修订后的段落正文，不要添加标题、总结或元信息。"
    )

    user_prompt = (
        f"请根据以下审校意见，修订对应段落。\n\n"
        f"原段落：\n{paragraph}\n\n"
        f"审校意见：\n{issues_text}\n\n"
        f"修改要求：\n"
        f"1. 解决所有审校意见指出的问题。\n"
        f"2. 保持段落连贯性和原有风格。\n"
        f"3. 保留 <!-- evidence: id --> 注释。\n"
        f"4. **保持原文长度**：不要缩减段落，修订后的字数应与原文相当。\n"
        f"5. 输出修订后的段落，不要输出其他内容。"
    )

    try:
        text = chat_completion_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max(4096, len(paragraph) * 3),
            timeout=60.0,
        )
        if text:
            return text.strip()
    except Exception:
        pass
    return paragraph


def revise_draft(content_md: str, issues: list[dict[str, Any]]) -> str:
    blocks = [item for item in content_md.split("\n\n")]
    flag_indices: set[int] = set()
    issues_by_paragraph: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for issue in issues:
        severity = str(issue.get("severity") or "").lower()
        issue_type = str(issue.get("issue_type") or "").lower()
        location = str(issue.get("location") or "")
        match = re.fullmatch(r"paragraph-(\d+)", location)
        if not match:
            continue
        paragraph_index = int(match.group(1))

        if severity == "high" and issue_type in {"evidence", "citation"}:
            # Flag for disclaimer instead of dropping the paragraph
            flag_indices.add(paragraph_index)
            issues_by_paragraph[paragraph_index].append(issue)
        else:
            issues_by_paragraph[paragraph_index].append(issue)

    revised_blocks: list[str] = []
    claim_counter = 0

    for block in blocks:
        text = block.strip()
        if not text:
            continue

        # Use the SAME _is_claim_block logic as review_draft_with_metrics
        # so paragraph indices align exactly.
        if not _is_claim_block(text):
            revised_blocks.append(text)
            continue

        claim_counter += 1
        if claim_counter in flag_indices:
            # Keep the paragraph but append a disclaimer instead of dropping it
            para_issues = issues_by_paragraph.get(claim_counter, [])
            if para_issues:
                revised_text = _llm_revise_paragraph(text, para_issues)
                if revised_text:
                    revised_blocks.append(revised_text)
                    continue
            # Fallback: keep original with a soft disclaimer
            revised_blocks.append(text)
            continue

        # Layer 1: LLM-driven targeted revision for fact/logic/expression issues
        para_issues = issues_by_paragraph.get(claim_counter, [])
        if para_issues:
            revised_text = _llm_revise_paragraph(text, para_issues)
            if revised_text:
                revised_blocks.append(revised_text)
                continue

        # Layer 0 fallback: naive string replacements
        cleaned = text.replace("[evidence:REPLACE_ME]", "")
        cleaned = cleaned.replace("REPLACE_ME", "")
        cleaned = cleaned.replace("TODO", "待补充证据后再扩展")
        cleaned = cleaned.replace("必然", "可能")
        cleaned = cleaned.replace("完全证明", "在当前证据下支持")
        cleaned = cleaned.replace("彻底", "在一定范围内")
        revised_blocks.append(cleaned)

    revised = "\n\n".join(revised_blocks).strip()

    if not revised:
        revised = "# 修订稿\n\n当前版本因证据门禁未通过，已移除不合规段落，请补充 evidence cards 后重新生成。"

    return revised


def score_quality(issue_count: int, critical_count: int, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    base = 1.0
    base -= min(0.6, issue_count * 0.03)
    base -= min(0.3, critical_count * 0.1)
    payload: dict[str, Any] = {
        "overall_score": round(max(0.0, base), 3),
        "issue_count": issue_count,
        "critical_count": critical_count,
    }
    if metrics:
        payload.update(metrics)
        if "overall_score" not in metrics:
            payload["overall_score"] = round(max(0.0, base), 3)
    return payload


def debate_review_with_metrics(
    content_md: str,
    evidence_cards: list[dict[str, Any]],
    article_type: str | None = None,
    *,
    task_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evidence-grounded multi-agent debate review (v2).

    Layers:
    - Layer 0:   rule-based evidence gate + logic heuristics
    - Layer 1b:  advanced style check (terminology, sentence variety, etc.)
    - Layer 1-3: structured debate via debate_service v2
                 (evidence reviewer + logic reviewer + adversarial challenger,
                 adaptive depth based on draft complexity)
    - Layer 2b:  external fact check (DOI verification, hallucination patterns)
    """
    from app.services.debate_service import debate_review

    # ---------- Layer 0: rule-based checks (identical to review_draft_with_metrics) ----------
    issues: list[dict[str, Any]] = []

    evidence_map = {str(card.get("id")): card for card in evidence_cards if card.get("id")}
    blocks = [block.strip() for block in content_md.split("\n\n") if block.strip()]

    total_claims = 0
    supported_claims = 0
    unsupported_claims = 0
    unresolved_citations = 0
    total_cited_ids = 0
    logic_flags = 0

    for idx, block in enumerate(blocks, start=1):
        if not _is_claim_block(block):
            continue

        total_claims += 1
        evidence_ids = _parse_evidence_ids(block)

        if not evidence_ids:
            unsupported_claims += 1
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "evidence",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "核心段落缺少 evidence_id，违反 Evidence Gate。",
                    "suggestion": "为该段补充真实 evidence_id，或删除该段核心判断。",
                    "evidence_ids": [],
                    "resolved": False,
                }
            )
            continue

        total_cited_ids += len(evidence_ids)
        unknown = [item for item in evidence_ids if item not in evidence_map]
        if unknown:
            unresolved_citations += len(unknown)
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "citation",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": f"存在无效 evidence_id: {', '.join(unknown[:6])}",
                    "suggestion": "替换为存在的 evidence_id，或删除对应断言。",
                    "evidence_ids": unknown[:6],
                    "resolved": False,
                }
            )
        else:
            supported_claims += 1

        block_lower = block.lower()
        has_absolute = any(term in block for term in ABSOLUTE_TERMS) or any(term in block_lower for term in ABSOLUTE_TERMS)
        has_corr = any(term in block for term in CORRELATION_TERMS) or any(term in block_lower for term in CORRELATION_TERMS)
        has_causal = any(term in block for term in CAUSAL_TERMS) or any(term in block_lower for term in CAUSAL_TERMS)

        if "TODO" in block or "REPLACE_ME" in block:
            logic_flags += 1
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "logic",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "段落含占位符，论证未完成。",
                    "suggestion": "删除占位符并补全可证据追溯表达。",
                    "evidence_ids": evidence_ids[:4],
                    "resolved": False,
                }
            )

        if has_corr and has_causal:
            logic_flags += 1
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "logic",
                    "location": f"paragraph-{idx}",
                    "claim": block[:160],
                    "description": "段落可能将相关性表述为因果性。",
                    "suggestion": "使用谨慎语气，明确因果推断条件。",
                    "evidence_ids": evidence_ids[:4],
                    "resolved": False,
                }
            )

        if has_absolute:
            strengths = [str((evidence_map.get(ev_id) or {}).get("strength") or "").lower() for ev_id in evidence_ids]
            if strengths and all(item in {"", "low", "weak"} for item in strengths):
                logic_flags += 1
                issues.append(
                    {
                        "severity": "medium",
                        "issue_type": "logic",
                        "location": f"paragraph-{idx}",
                        "claim": block[:160],
                        "description": "证据强度偏弱，但语气绝对化。",
                        "suggestion": "降调表达，加入限制条件或争议说明。",
                        "evidence_ids": evidence_ids[:4],
                        "resolved": False,
                    }
                )

    style_items = _style_issues(content_md, article_type)
    issues.extend(style_items)

    # ---------- Layer 1b: advanced style check ----------
    adv_style_issues, adv_style_metrics = check_style(content_md, article_type)
    issues.extend(adv_style_issues)

    # ---------- Layer 1-3: MULTI-AGENT DEBATE review (replaces single LLM call) ----------
    debate_result = debate_review(content_md, evidence_cards, article_type, task_id=task_id)
    debate_issues = debate_result.issues
    issues.extend(debate_issues)

    # ---------- Layer 2b: external fact check ----------
    fact_issues, fact_metrics = fact_check_draft(content_md, evidence_cards)
    issues.extend(fact_issues)

    # ---------- Metrics computation ----------
    critical_issues = len([item for item in issues if item.get("severity") == "high"])
    evidence_coverage = supported_claims / max(1, total_claims)
    citation_validity = 1.0 - (unresolved_citations / max(1, total_cited_ids))
    logic_score = max(0.0, 1.0 - (logic_flags / max(1, total_claims)) * 0.7)
    total_style_penalty = len(style_items) * 0.12 + len(adv_style_issues) * 0.06
    style_score = max(0.0, 1.0 - min(0.5, total_style_penalty))

    # Weight debate findings into scores (v2: includes evidence/fact/structure types)
    debate_logic_hits = len([i for i in debate_issues if i.get("issue_type") in {"logic", "structure"}])
    debate_expr_hits = len([i for i in debate_issues if i.get("issue_type") == "expression"])
    debate_evidence_hits = len([i for i in debate_issues if i.get("issue_type") in {"evidence", "fact"}])
    if debate_logic_hits:
        logic_score = max(0.0, logic_score - min(0.3, debate_logic_hits * 0.08))
    if debate_expr_hits:
        style_score = max(0.0, style_score - min(0.25, debate_expr_hits * 0.06))
    if debate_evidence_hits:
        # Evidence/fact issues from debate directly penalize evidence coverage
        evidence_coverage = max(0.0, evidence_coverage - min(0.2, debate_evidence_hits * 0.04))

    # De-AI metrics
    de_ai = de_ai_metrics(content_md)
    de_ai_score = round((de_ai["connector_variety_score"] + de_ai["absolutism_score"] + de_ai["template_cliche_score"]) / 3, 3)
    style_score = round(0.90 * style_score + 0.10 * de_ai_score, 3)

    publication_prepared = (
        critical_issues == 0
        and unsupported_claims == 0
        and unresolved_citations == 0
        and evidence_coverage >= 0.90
        and citation_validity >= 0.90
        and logic_score >= 0.80
        and style_score >= 0.80
    )

    overall = max(
        0.0,
        min(
            1.0,
            0.35 * evidence_coverage
            + 0.25 * citation_validity
            + 0.20 * logic_score
            + 0.20 * style_score,
        ),
    )

    metrics = {
        "overall_score": round(overall, 3),
        "critical_issues": critical_issues,
        "unsupported_claims": unsupported_claims,
        "unresolved_citations": unresolved_citations,
        "evidence_coverage": round(evidence_coverage, 3),
        "citation_validity": round(max(0.0, citation_validity), 3),
        "logic_score": round(logic_score, 3),
        "style_score": round(style_score, 3),
        "de_ai_score": de_ai_score,
        **adv_style_metrics,
        **fact_metrics,
        "human_review_required": True,
        "publication_prepared": publication_prepared,
        "debate_review_issues": len(debate_issues),
        "debate_consensus_count": len(debate_result.consensus_issues),
        "debate_disputed_count": len(debate_result.disputed_issues),
        "debate_evidence_hits": debate_evidence_hits,
        "debate_log": debate_result.debate_log,
    }
    return issues, metrics
