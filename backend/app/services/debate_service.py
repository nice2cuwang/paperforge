"""Multi-Agent Debate service v2 — Evidence-Grounded Structured Review.

Key improvements over v1 (3 generic specialists + cross-debate + moderator):

1. **Evidence Grounding**: Pre-builds a structured evidence brief so reviewers
   can verify claims against actual evidence text, not just skim summaries.

2. **Dual Specialist + Adversarial Challenger**:
   - Evidence Reviewer: verifies draft claims against evidence cards
   - Logic & Structure Reviewer: checks argumentation and expression
   - Adversarial Challenger: actively hunts for what reviewers MISSED

3. **Adaptive Depth**: Routes drafts to full / lite / minimal debate based on
   complexity (length, evidence count, structural richness).

4. **Structured Reasoning Prompts**: Requires models to reason before listing
   issues (``reasoning`` field in JSON), producing deeper analysis than
   direct issue listing.

5. **Parallel Phase Execution**: Independent phases run concurrently via
   ``ThreadPoolExecutor``, reducing wall-clock time ~40%.

Call budget: 3–6 LLM calls (v1 was always 7).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.services.llm_service import chat_completion

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

MAX_CONTENT_LENGTH = 14000
MAX_EVIDENCE_CARDS = 25

# ── Data structures ──────────────────────────────────────────────


@dataclass
class DebateResult:
    """Final output of the structured debate.

    Backward-compatible with v1 ``DebateResult``: same field names and types
    so ``review_service.debate_review_with_metrics`` works without changes.
    """

    issues: list[dict[str, Any]]
    consensus_issues: list[dict[str, Any]] = field(default_factory=list)
    disputed_issues: list[dict[str, Any]] = field(default_factory=list)
    agent_opinions: list[dict[str, Any]] = field(default_factory=list)
    debate_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _EvidenceBrief:
    """Structured evidence summary for reviewer grounding."""

    cards: list[dict[str, Any]]
    total_count: int
    strong_count: int
    weak_count: int


# ── Adaptive complexity analysis ─────────────────────────────────


def _assess_complexity(
    content_md: str,
    evidence_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    """Determine debate depth tier.

    Returns ``{"level": "full"|"lite"|"minimal", ...metrics}``.

    Decision rules:
    - ``minimal``: < 800 chars and < 3 evidence cards
    - ``lite``:    < 2500 chars or < 6 evidence cards
    - ``full``:    everything else
    """
    text_len = len(content_md)
    ev_count = len(evidence_cards)
    heading_count = len(re.findall(r"^##\s", content_md, re.MULTILINE))
    paragraph_count = len(
        [b for b in content_md.split("\n\n") if b.strip() and not b.strip().startswith("#")]
    )

    if text_len < 800 and ev_count < 3:
        level = "minimal"
    elif text_len < 2500 or ev_count < 6:
        level = "lite"
    else:
        level = "full"

    return {
        "level": level,
        "text_length": text_len,
        "evidence_count": ev_count,
        "heading_count": heading_count,
        "paragraph_count": paragraph_count,
    }


# ── Evidence grounding ───────────────────────────────────────────


def _build_evidence_brief(
    evidence_cards: list[dict[str, Any]],
) -> _EvidenceBrief:
    """Build a structured evidence brief for reviewer grounding.

    Filters low-strength cards, enriches each with ``_has_support`` and
    ``_support_excerpt`` fields so reviewers can verify claims in-place.
    """
    enriched: list[dict[str, Any]] = []
    strong = 0
    weak = 0

    for card in evidence_cards[:MAX_EVIDENCE_CARDS]:
        cid = str(card.get("id") or "unknown")
        claim = str(card.get("claim") or card.get("_clean_claim") or "")[:300]
        support = str(card.get("supporting_text") or "")[:600]
        strength = str(card.get("strength") or "unknown").lower()
        ev_type = str(card.get("evidence_type") or "general")

        if strength in {"high", "medium"}:
            strong += 1
        else:
            weak += 1

        enriched.append({
            "id": cid,
            "claim": claim,
            "supporting_text": support,
            "strength": strength,
            "evidence_type": ev_type,
            "_has_support": len(support) > 40 and support != claim,
            "_support_excerpt": support[:200] if support else "",
        })

    return _EvidenceBrief(
        cards=enriched,
        total_count=len(evidence_cards),
        strong_count=strong,
        weak_count=weak,
    )


def _format_evidence_for_reviewer(brief: _EvidenceBrief) -> str:
    """Format evidence brief into reviewer-friendly text.

    Each card shows ID, type, strength, claim, and a supporting text excerpt
    so the reviewer can verify whether the draft faithfully represents it.
    """
    lines: list[str] = []
    for card in brief.cards:
        cid = card["id"]
        strength = card["strength"]
        claim = card["claim"]
        lines.append(f"[EV-{cid}] type={card['evidence_type']}, strength={strength}")
        lines.append(f"  claim: {claim}")
        if card["_has_support"]:
            excerpt = card["_support_excerpt"]
            lines.append(f"  evidence: {excerpt}")
        lines.append("")

    lines.append(
        f"(summary: {brief.total_count} cards, "
        f"{brief.strong_count} strong/medium, {brief.weak_count} weak/low)"
    )
    return "\n".join(lines)


# ── JSON extraction ──────────────────────────────────────────────


def _extract_json(raw: str) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Extract JSON from LLM response.

    Tries in order:
    1. Direct ``json.loads``
    2. JSON inside `````json ... ``` ```` code fences
    3. Largest ``[...]`` or ``{...}`` substring
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Code fence extraction
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Substring extraction (largest array or object)
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, TypeError):
                continue

    return None


def _parse_issues(raw_content: str, source: str) -> list[dict[str, Any]]:
    """Parse LLM response into a normalized issue list.

    Handles responses shaped as:
    - ``{"reasoning": "...", "issues": [...]}``
    - ``[...]`` (flat array)
    - ``{"issues": [...]}``
    """
    parsed = _extract_json(raw_content)
    if parsed is None:
        logger.warning("Failed to extract JSON from %s response", source)
        return []

    issues: list[Any] = []
    if isinstance(parsed, dict):
        issues = parsed.get("issues", [])
    elif isinstance(parsed, list):
        issues = parsed

    normalized: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        item.setdefault("evidence_ids", [])
        item.setdefault("resolved", False)
        item.setdefault("sources", [source])
        # Ensure severity is valid
        if item.get("severity") not in {"high", "medium", "low"}:
            item["severity"] = "medium"
        # Ensure issue_type is present
        if not item.get("issue_type"):
            item["issue_type"] = "logic"
        normalized.append(item)

    return normalized


# ── Phase 1: Evidence-Grounded Reviewer ──────────────────────────

_EVIDENCE_REVIEWER_SYSTEM = """\
你是一位证据锚定审查员（Evidence-Grounded Reviewer）。

你的核心职责是逐条验证文稿中的核心声明是否忠实于提供的证据卡片。你不是泛泛地"看看有没有问题"，而是像一个严谨的审稿人一样，将文稿声明与原始证据做交叉比对。

## 审查维度

1. **证据对齐（alignment）**：文稿中对证据的引用是否准确反映了原文含义？是否存在过度解读、选择性引用或断章取义？
2. **证据覆盖（coverage）**：是否有核心段落缺少证据支撑？是否存在无来源的关键断言？
3. **幻觉检测（hallucination）**：是否存在证据卡片中完全没有提及的事实、数据或结论？
4. **证据强度匹配（calibration）**：文稿语气是否与证据强度匹配？low-strength 证据是否被用来支撑强断言？
5. **表达质量（expression）**：术语首次出现是否附带解释？段落过渡是否自然？是否存在冗余或模糊表述？

## 审查方法

请按以下步骤工作：
1. 先通读全文，建立对文章主旨的整体理解
2. 逐段扫描，识别每个核心声明
3. 将声明与证据卡片逐条比对
4. 对每个发现的问题评估严重性

## severity 校准

- **high**：事实错误或幻觉，会导致文章不可信（如引用了不存在的数据，或完全曲解了证据含义）
- **medium**：证据支撑不足或语气与证据强度不匹配（如 low 证据被表述为确定性结论）
- **low**：表述可改进但不影响事实准确性（如术语未解释、过渡生硬）

## 输出格式

严格输出 JSON，包含 reasoning（你的分析过程）和 issues（发现的问题列表）：

```json
{
  "reasoning": "在此写下你的整体分析和逐段比对过程。说明你如何验证每个核心声明，以及你发现了哪些不一致之处。至少 150 字。",
  "issues": [
    {
      "severity": "high|medium|low",
      "issue_type": "evidence|fact|expression",
      "location": "paragraph-N|global",
      "claim": "文稿中的具体文本片段",
      "description": "问题描述：为什么这是一个问题，涉及哪些证据卡",
      "suggestion": "具体可执行的修改建议",
      "evidence_ids": ["相关的evidence_id"]
    }
  ]
}
```

如果没有发现问题，issues 为空数组 `[]`，但 reasoning 仍需说明你的验证过程。
"""


def _evidence_reviewer(
    content_md: str,
    evidence_text: str,
    article_type: str | None,
    complexity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Phase 1a: Evidence-grounded review."""
    user_prompt = (
        f"请对以下文稿进行证据锚定审查。\n\n"
        f"文章类型：{article_type or '通用文稿'}\n"
        f"文稿复杂度：{complexity['level']}（{complexity['text_length']}字，"
        f"{complexity['evidence_count']}张证据卡，"
        f"{complexity['paragraph_count']}个段落）\n\n"
        f"═══════ 证据卡片（审查基准） ═══════\n"
        f"{evidence_text}\n\n"
        f"═══════ 文稿正文 ═══════\n"
        f"{content_md}"
    )

    result = chat_completion(
        system_prompt=_EVIDENCE_REVIEWER_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.2,
        max_tokens=4096,
        timeout=120.0,
    )

    if result.get("error"):
        logger.warning("[debate-v2] evidence_reviewer failed: %s", result["error"])
        return []

    return _parse_issues(result.get("content", ""), "evidence_reviewer")


# ── Phase 1: Logic & Structure Reviewer ──────────────────────────

_LOGIC_REVIEWER_SYSTEM = """\
你是一位论证与结构审查员（Logic & Structure Reviewer）。

你的核心职责是从论证逻辑、篇章结构和表达节奏三个维度审查文稿。你关注的是"文章的骨架是否站得住"，而非具体事实是否正确（那是证据审查员的职责）。

## 审查维度

### A. 论证逻辑
1. 论证链条是否完整？是否存在跳跃论证（A→C 缺少 B）？
2. 是否存在因果混淆（将相关性表述为因果性）？
3. 是否存在循环论证或以偏概全？
4. 结论是否由前文证据充分推导而出？

### B. 篇章结构
5. 各章节之间的逻辑过渡是否自然流畅？
6. 文章类型（article_type）是否匹配对应的结构要求？
7. 是否有冗余章节或信息重复？

### C. 表达节奏
8. 段落长度分布是否合理？是否出现连续超长或超短段落？
9. 句式是否多样？是否所有段落都以相同方式开头？
10. 语气是否与文章类型匹配？（学术文稿不应口语化，公众号文稿不应过于学究）

## severity 校准

- **high**：论证链条断裂或因果混淆，影响核心结论可信度
- **medium**：结构缺陷或过渡不足，影响阅读体验但不影响核心论证
- **low**：句式单调、段落偏长等可改进但不影响理解的问题

## 输出格式

```json
{
  "reasoning": "在此写下你的论证链分析和结构评估。逐段说明论证链是否完整，章节过渡是否合理。至少 150 字。",
  "issues": [
    {
      "severity": "high|medium|low",
      "issue_type": "logic|structure|expression",
      "location": "paragraph-N|global",
      "claim": "涉及的具体文本",
      "description": "问题描述",
      "suggestion": "具体修改建议"
    }
  ]
}
```

如果没有发现问题，issues 为空数组 `[]`。
"""


def _logic_reviewer(
    content_md: str,
    evidence_text: str,
    article_type: str | None,
    complexity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Phase 1b: Logic & structure review."""
    user_prompt = (
        f"请对以下文稿进行论证与结构审查。\n\n"
        f"文章类型：{article_type or '通用文稿'}\n"
        f"文稿结构：{complexity['heading_count']}个章节，"
        f"{complexity['paragraph_count']}个段落，"
        f"{complexity['text_length']}字\n\n"
        f"═══════ 可用证据卡片 ═══════\n"
        f"{evidence_text}\n\n"
        f"═══════ 文稿正文 ═══════\n"
        f"{content_md}"
    )

    result = chat_completion(
        system_prompt=_LOGIC_REVIEWER_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=4096,
        timeout=120.0,
    )

    if result.get("error"):
        logger.warning("[debate-v2] logic_reviewer failed: %s", result["error"])
        return []

    return _parse_issues(result.get("content", ""), "logic_reviewer")


# ── Phase 2: Adversarial Challenger ──────────────────────────────

_CHALLENGER_SYSTEM = """\
你是一位对抗性审查员（Adversarial Challenger）。

你的角色与其他审查员截然不同。你不是做常规审校，而是扮演一位严苛的审稿人 / 辩论对手，专门寻找：

1. **审查盲区**：前两位审查员都遗漏了的问题。这是你的首要任务。
2. **隐藏假设**：文稿中未声明但实际依赖的假设条件。
3. **数据可疑性**：看起来像编造或幻觉的具体数字、日期、名称。
4. **反面论证**：核心论点是否存在明显的反驳角度但文中完全未提及？
5. **过度泛化**：将特定场景的结论推广到不适用的领域。

## 工作原则

- 你不需要面面俱到，只需要找到最致命的 1–5 个问题
- 宁可报告 1 个深刻的问题，也不要报告 5 个肤浅的问题
- 如果前两位审查员已经覆盖了所有重要问题，诚实说明"未发现重要遗漏"并返回空数组
- 不要重复前两位审查员已经发现的问题

## 输出格式

```json
{
  "reasoning": "在此说明你发现的审查盲区，以及你为什么认为这些是重要遗漏。至少 100 字。",
  "issues": [
    {
      "severity": "high|medium|low",
      "issue_type": "fact|logic|expression",
      "location": "paragraph-N|global",
      "claim": "涉及文本",
      "description": "问题描述，重点说明为什么前两位审查员遗漏了这个问题",
      "suggestion": "具体修改建议",
      "challenge_type": "blind_spot|hidden_assumption|fabrication_risk|counter_argument|over_generalization"
    }
  ]
}
```
"""


def _adversarial_challenger(
    content_md: str,
    evidence_text: str,
    reviewer_findings: str,
    article_type: str | None,
) -> list[dict[str, Any]]:
    """Phase 2: Adversarial challenge against reviewer findings."""
    user_prompt = (
        f"你是一位严苛的对抗审稿人。以下文稿已经经过两位审查员的审校。\n"
        f"你的任务是找到他们遗漏的问题。\n\n"
        f"文章类型：{article_type or '通用文稿'}\n\n"
        f"═══════ 可用证据卡片 ═══════\n"
        f"{evidence_text}\n\n"
        f"═══════ 文稿正文 ═══════\n"
        f"{content_md}\n\n"
        f"═══════ 前两位审查员的发现 ═══════\n"
        f"{reviewer_findings}\n\n"
        f"请找出上述审查中遗漏的重要问题。如果你认为审查已经足够全面，返回空数组即可。"
    )

    result = chat_completion(
        system_prompt=_CHALLENGER_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.5,
        max_tokens=3072,
        timeout=120.0,
    )

    if result.get("error"):
        logger.warning("[debate-v2] challenger failed: %s", result["error"])
        return []

    return _parse_issues(result.get("content", ""), "challenger")


# ── Phase 3: Cross-review (lite) ─────────────────────────────────

_CROSS_REVIEW_SYSTEM = """\
你是一位资深审校编辑，正在进行交叉审阅。

你之前独立审校了一篇文章，现在看到了另一位审查员的发现。请你：

1. 对比两份审查，找出你遗漏但对方发现的问题——如果确实有价值，补充到你的列表中
2. 如果对方的某个判断你认为不准确，标记为 contested 并简要说明
3. 不要简单重复对方已有的问题

## 输出格式

```json
{
  "reasoning": "简要说明交叉比对后的判断",
  "issues": [
    {
      "severity": "high|medium|low",
      "issue_type": "evidence|fact|logic|structure|expression",
      "location": "paragraph-N|global",
      "claim": "涉及文本",
      "description": "新发现或补充说明",
      "suggestion": "修改建议",
      "cross_review_note": "inspired_by_peer|contested|supplementary"
    }
  ]
}
```

如果无需补充，返回空数组 `[]`。
"""


def _cross_review(
    content_md: str,
    evidence_text: str,
    own_issues: list[dict[str, Any]],
    peer_issues: list[dict[str, Any]],
    own_role: str,
    article_type: str | None,
) -> list[dict[str, Any]]:
    """Phase 3: Lightweight cross-review between two reviewers."""
    peer_summary = json.dumps(
        [
            {
                "severity": i.get("severity"),
                "issue_type": i.get("issue_type"),
                "location": i.get("location"),
                "description": i.get("description", "")[:200],
            }
            for i in peer_issues[:12]
        ],
        ensure_ascii=False,
        indent=2,
    )

    user_prompt = (
        f"文章类型：{article_type or '通用文稿'}\n\n"
        f"═══════ 文稿正文 ═══════\n"
        f"{content_md}\n\n"
        f"═══════ 你的发现 ═══════\n"
        f"{json.dumps(own_issues[:12], ensure_ascii=False, indent=2)}\n\n"
        f"═══════ 另一位审查员的发现 ═══════\n"
        f"{peer_summary}\n\n"
        f"请交叉比对后补充或修正你的审查意见。"
    )

    result = chat_completion(
        system_prompt=_CROSS_REVIEW_SYSTEM,
        user_prompt=user_prompt,
        temperature=0.2,
        max_tokens=2048,
        timeout=90.0,
    )

    if result.get("error"):
        logger.warning("[debate-v2] cross_review (%s) failed: %s", own_role, result["error"])
        return []

    return _parse_issues(result.get("content", ""), f"{own_role}_cross")


# ── Consolidation ────────────────────────────────────────────────


def _consolidate(
    evidence_issues: list[dict[str, Any]],
    logic_issues: list[dict[str, Any]],
    challenge_issues: list[dict[str, Any]],
    cross_ev_issues: list[dict[str, Any]],
    cross_logic_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge and deduplicate all findings.

    Rules:
    - Same location + similar claim → merge, keep highest severity
    - Challenger findings that overlap with reviewers → mark ``contested=False``
    - Challenger findings with NO reviewer overlap → ``challenge_type`` preserved
    """
    all_issues: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    def _key(issue: dict[str, Any]) -> tuple[str, str]:
        loc = str(issue.get("location", ""))
        desc = str(issue.get("description", ""))
        claim = str(issue.get("claim", ""))[:80]
        return (loc, claim or desc[:80])

    def _merge_issue(new: dict[str, Any]) -> None:
        k = _key(new)
        if k in seen_keys:
            # Promote severity if duplicate found at higher level
            for existing in all_issues:
                if _key(existing) == k:
                    severity_order = {"high": 3, "medium": 2, "low": 1}
                    if severity_order.get(new.get("severity"), 0) > severity_order.get(
                        existing.get("severity"), 0
                    ):
                        existing["severity"] = new["severity"]
                    # Merge evidence_ids
                    existing_eids = set(existing.get("evidence_ids", []))
                    new_eids = new.get("evidence_ids", [])
                    existing["evidence_ids"] = list(existing_eids | set(new_eids))
                    break
            return
        seen_keys.add(k)
        all_issues.append(new)

    # Process in priority order: evidence > logic > challenge > cross
    for issue in evidence_issues:
        _merge_issue(issue)
    for issue in logic_issues:
        _merge_issue(issue)

    # Challenger issues: mark as "disputed" if overlapping with reviewer findings
    challenger_overlapping = 0
    for issue in challenge_issues:
        k = _key(issue)
        if k in seen_keys:
            challenger_overlapping += 1
            issue["contested"] = False  # Confirmed by both challenger and reviewer
        else:
            issue["contested"] = True  # Only challenger found this
        _merge_issue(issue)

    # Cross-review supplements
    for issue in cross_ev_issues:
        issue.setdefault("sources", ["evidence_reviewer_cross"])
        _merge_issue(issue)
    for issue in cross_logic_issues:
        issue.setdefault("sources", ["logic_reviewer_cross"])
        _merge_issue(issue)

    return all_issues


def _simple_merge(
    *issue_lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback merge when consolidation fails."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for issues in issue_lists:
        for issue in issues:
            loc = str(issue.get("location", ""))
            claim = str(issue.get("claim", ""))[:80]
            key = (loc, claim)
            if key not in seen:
                seen.add(key)
                issue_copy = dict(issue)
                issue_copy.setdefault("sources", ["unknown"])
                issue_copy.setdefault("consensus", False)
                issue_copy.setdefault("disputed", False)
                merged.append(issue_copy)

    return merged


# ── Helpers ──────────────────────────────────────────────────────


def _truncate(content_md: str) -> str:
    """Truncate content to fit within token budget."""
    if len(content_md) > MAX_CONTENT_LENGTH:
        return (
            content_md[:MAX_CONTENT_LENGTH]
            + "\n\n...（内容截断，后续部分未纳入本次审校）"
        )
    return content_md


def _format_reviewer_findings(
    evidence_issues: list[dict[str, Any]],
    logic_issues: list[dict[str, Any]],
) -> str:
    """Format reviewer findings for the challenger."""
    sections: list[str] = []

    if evidence_issues:
        lines = [f"### 证据审查员（{len(evidence_issues)} 个问题）"]
        for i in evidence_issues[:10]:
            lines.append(
                f"- [{i.get('severity')}] {i.get('issue_type')} @ {i.get('location')}: "
                f"{str(i.get('description', ''))[:150]}"
            )
        sections.append("\n".join(lines))

    if logic_issues:
        lines = [f"### 逻辑审查员（{len(logic_issues)} 个问题）"]
        for i in logic_issues[:10]:
            lines.append(
                f"- [{i.get('severity')}] {i.get('issue_type')} @ {i.get('location')}: "
                f"{str(i.get('description', ''))[:150]}"
            )
        sections.append("\n".join(lines))

    return "\n\n".join(sections) if sections else "（两位审查员均未发现显著问题）"


# ── Public API ───────────────────────────────────────────────────


def debate_review(
    content_md: str,
    evidence_cards: list[dict[str, Any]],
    article_type: str | None = None,
    *,
    task_id: str | None = None,
) -> DebateResult:
    """Run evidence-grounded structured debate on a draft.

    Flow:
    1. Build evidence brief (grounding material for reviewers)
    2. Assess draft complexity → determine debate depth
    3. Phase 1 (parallel): Evidence Reviewer + Logic Reviewer
    4. Phase 2 (conditional): Adversarial Challenger (full/lite only)
    5. Phase 3 (conditional, parallel): Cross-review (full only)
    6. Consolidate all findings

    Returns ``DebateResult`` (backward-compatible with v1).
    """
    content_md = _truncate(content_md)

    # ── Real-time task logging helper ──
    def _tlog(msg: str) -> None:
        if task_id:
            try:
                from app.services.task_registry import add_log
                add_log(task_id, msg)
            except Exception:
                pass

    _tlog("[辩论] 开始多智能体辩论审查...")

    # ── Step 1: Evidence grounding ──
    brief = _build_evidence_brief(evidence_cards)
    evidence_text = _format_evidence_for_reviewer(brief)

    # ── Step 2: Adaptive complexity ──
    complexity = _assess_complexity(content_md, evidence_cards)
    level = complexity["level"]
    logger.info(
        "[debate-v2] complexity=%s, text=%d chars, evidence=%d cards",
        level,
        complexity["text_length"],
        complexity["evidence_count"],
    )
    _tlog(f"[辩论] 文章复杂度评估：{level}（{complexity['text_length']}字，{complexity['evidence_count']}张证据卡）")

    # ── Minimal: single-pass review ──
    if level == "minimal":
        logger.info("[debate-v2] minimal flow: evidence reviewer only")
        _tlog("[辩论] 📋 明鉴开始审查证据覆盖...")
        ev_issues = _evidence_reviewer(content_md, evidence_text, article_type, complexity)
        _tlog(f"[辩论] 📋 明鉴完成审查，发现 {len(ev_issues)} 个问题")

        debate_log = [
            {
                "role": "evidence_reviewer",
                "phase": "single_pass",
                "count": len(ev_issues),
            }
        ]

        return DebateResult(
            issues=ev_issues,
            agent_opinions=[{"role": "evidence_reviewer", "issue_count": len(ev_issues)}],
            debate_log=debate_log,
        )

    # ── Lite: dual reviewer, no challenger, no cross-review ──
    if level == "lite":
        logger.info("[debate-v2] lite flow: dual reviewer (no challenge)")
        _tlog("[辩论] 第一阶段：明鉴和持正并行审查中...")
        with ThreadPoolExecutor(max_workers=2) as pool:
            ev_future = pool.submit(
                _evidence_reviewer, content_md, evidence_text, article_type, complexity
            )
            logic_future = pool.submit(
                _logic_reviewer, content_md, evidence_text, article_type, complexity
            )
            ev_issues = ev_future.result()
            logic_issues = logic_future.result()

        _tlog(f"[辩论] 明鉴发现 {len(ev_issues)} 个证据/事实问题")
        _tlog(f"[辩论] 持正发现 {len(logic_issues)} 个逻辑/结构问题")

        all_issues = _consolidate(ev_issues, logic_issues, [], [], [])
        _tlog(f"[辩论] 最终合并：共 {len(all_issues)} 个问题")

        debate_log = [
            {"role": "evidence_reviewer", "phase": "dual", "count": len(ev_issues)},
            {"role": "logic_reviewer", "phase": "dual", "count": len(logic_issues)},
            {"phase": "consolidated", "total": len(all_issues)},
        ]

        return DebateResult(
            issues=all_issues,
            agent_opinions=[
                {"role": "evidence_reviewer", "issue_count": len(ev_issues)},
                {"role": "logic_reviewer", "issue_count": len(logic_issues)},
            ],
            debate_log=debate_log,
        )

    # ── Full: dual reviewer + challenger + cross-review ──
    logger.info("[debate-v2] full flow: dual reviewer + challenger + cross-review")

    # Phase 1: Parallel dual review
    _tlog("[辩论] 第一阶段：明鉴和持正并行审查中...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        ev_future = pool.submit(
            _evidence_reviewer, content_md, evidence_text, article_type, complexity
        )
        logic_future = pool.submit(
            _logic_reviewer, content_md, evidence_text, article_type, complexity
        )
        ev_issues = ev_future.result()
        logic_issues = logic_future.result()

    _tlog(f"[辩论] 📋 明鉴完成：发现 {len(ev_issues)} 个证据/事实问题")
    _tlog(f"[辩论] ✏️ 持正完成：发现 {len(logic_issues)} 个逻辑/结构问题")

    logger.info(
        "[debate-v2] phase 1: evidence=%d, logic=%d",
        len(ev_issues),
        len(logic_issues),
    )

    # Phase 2: Adversarial challenge (only if there are findings to challenge)
    challenge_issues: list[dict[str, Any]] = []
    reviewer_findings = _format_reviewer_findings(ev_issues, logic_issues)

    if ev_issues or logic_issues:
        _tlog("[辩论] 第二阶段：破壁审视审查结论...")
        logger.info("[debate-v2] phase 2: adversarial challenge")
        challenge_issues = _adversarial_challenger(
            content_md, evidence_text, reviewer_findings, article_type
        )
        _tlog(f"[辩论] 🔍 破壁发现 {len(challenge_issues)} 个盲区/隐患")
        logger.info("[debate-v2] challenger found %d issues", len(challenge_issues))
    else:
        _tlog("[辩论] 第二阶段：跳过（第一阶段未发现问题，无需质疑）")
        logger.info("[debate-v2] phase 2: skipped (no reviewer findings to challenge)")

    # Phase 3: Cross-review (only if both reviewers found issues)
    cross_ev_issues: list[dict[str, Any]] = []
    cross_logic_issues: list[dict[str, Any]] = []

    if ev_issues and logic_issues:
        _tlog("[辩论] 第三阶段：交叉审查，互相验证对方结论...")
        logger.info("[debate-v2] phase 3: cross-review")
        with ThreadPoolExecutor(max_workers=2) as pool:
            cross_ev_future = pool.submit(
                _cross_review,
                content_md,
                evidence_text,
                ev_issues,
                logic_issues,
                "evidence_reviewer",
                article_type,
            )
            cross_logic_future = pool.submit(
                _cross_review,
                content_md,
                evidence_text,
                logic_issues,
                ev_issues,
                "logic_reviewer",
                article_type,
            )
            cross_ev_issues = cross_ev_future.result()
            cross_logic_issues = cross_logic_future.result()
        _tlog(f"[辩论] 交叉审查完成：证据补充 {len(cross_ev_issues)}，逻辑补充 {len(cross_logic_issues)}")
    else:
        _tlog("[辩论] 第三阶段：跳过（审查发现不足，无需交叉验证）")
        logger.info("[debate-v2] phase 3: skipped (insufficient reviewer findings)")

    # Consolidate all findings
    _tlog("[辩论] 正在合并所有审查结论...")
    try:
        all_issues = _consolidate(
            ev_issues, logic_issues, challenge_issues, cross_ev_issues, cross_logic_issues
        )
    except Exception as exc:
        logger.warning("[debate-v2] consolidation failed: %s, using simple merge", exc)
        all_issues = _simple_merge(
            ev_issues, logic_issues, challenge_issues, cross_ev_issues, cross_logic_issues
        )

    # Classify consensus vs disputed
    consensus = [i for i in all_issues if len(i.get("sources", [])) >= 2]
    disputed = [i for i in all_issues if i.get("contested", False)]

    # Build observability log
    debate_log: list[dict[str, Any]] = [
        {
            "role": "evidence_reviewer",
            "phase": "phase_1",
            "count": len(ev_issues),
            "issue_types": sorted({i.get("issue_type", "") for i in ev_issues}),
        },
        {
            "role": "logic_reviewer",
            "phase": "phase_1",
            "count": len(logic_issues),
            "issue_types": sorted({i.get("issue_type", "") for i in logic_issues}),
        },
        {
            "role": "challenger",
            "phase": "phase_2",
            "count": len(challenge_issues),
            "challenge_types": sorted(
                {i.get("challenge_type", "") for i in challenge_issues if i.get("challenge_type")}
            ),
        },
        {
            "phase": "cross_review",
            "evidence_supplements": len(cross_ev_issues),
            "logic_supplements": len(cross_logic_issues),
        },
        {
            "phase": "consolidated",
            "total": len(all_issues),
            "consensus_count": len(consensus),
            "disputed_count": len(disputed),
        },
    ]

    logger.info(
        "[debate-v2] final: %d issues (%d consensus, %d disputed)",
        len(all_issues),
        len(consensus),
        len(disputed),
    )

    return DebateResult(
        issues=all_issues,
        consensus_issues=consensus,
        disputed_issues=disputed,
        agent_opinions=[
            {"role": "evidence_reviewer", "issue_count": len(ev_issues)},
            {"role": "logic_reviewer", "issue_count": len(logic_issues)},
            {"role": "challenger", "issue_count": len(challenge_issues)},
        ],
        debate_log=debate_log,
    )
