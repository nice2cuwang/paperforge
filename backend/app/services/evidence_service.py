from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


PDF_GARBAGE_RE = re.compile(
    r"(%PDF-|/FlateDecode|/DecodeParms|/Type/Catalog|\bxref\b|\bendobj\b|\bendstream\b|\bstream\b|^\d+\s+\d+\s+obj\b|^<<.*>>$)",
    re.IGNORECASE,
)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
WORD_RE = re.compile(r"[A-Za-z]{2,}")
CJK_WORD_RE = re.compile(r"[\u4E00-\u9FFF]{2,}")
COMMON_PUNCTUATION = set(".,;:!?()[]{}'\"-_/+%&*@#，。；：！？（）《》、")


def _looks_like_meaningful_text(text: str) -> bool:
    t = " ".join((text or "").split())
    if len(t) < 20:
        return False
    if PDF_GARBAGE_RE.search(t):
        return False
    if CONTROL_CHAR_RE.search(t):
        return False
    if "\ufffd" in t:
        return False
    content = [ch for ch in t if not ch.isspace()]
    if not content:
        return False
    natural = sum(1 for ch in content if ch.isalnum() or (0x4E00 <= ord(ch) <= 0x9FFF))
    if natural / len(content) < 0.55:
        return False
    weird = sum(
        1
        for ch in content
        if (not ch.isalnum()) and (not (0x4E00 <= ord(ch) <= 0x9FFF)) and ch not in COMMON_PUNCTUATION
    )
    if weird / len(content) > 0.22:
        return False
    natural_tokens = len(WORD_RE.findall(t)) + len(CJK_WORD_RE.findall(t))
    return natural_tokens >= 3


def _first_sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "No claim extracted."
    for separator in ["。", ". ", "! ", "? ", "\n"]:
        if separator in cleaned:
            return cleaned.split(separator, 1)[0].strip()[:260]
    return cleaned[:260]


# S3: per-source credibility weights. Low-credibility sources must clear a
# higher relevance bar in the filter and be labeled, not stated as fact, in prose.
CREDIBILITY_BY_SOURCE: dict[str, float] = {
    "academic": 1.0,
    "web": 0.5,
    "community": 0.3,
    "llm_knowledge": 0.2,
}


def credibility_weight(source_type: str | None, has_doi: bool = False) -> float:
    """S3: credibility of an evidence source in [0, 1]. Verified DOI adds 0.1."""
    weight = CREDIBILITY_BY_SOURCE.get((source_type or "academic").lower().strip(), 0.5)
    if has_doi:
        weight = min(1.0, weight + 0.1)
    return round(weight, 2)


# S4: polarity markers used by the heuristic conflict fallback.
_CONFLICT_POSITIVE_MARKERS = (
    "提升", "提高", "增长", "显著优于", "更好", "优势", "改善", "benefits", "improve",
    "outperform", "higher", "increase", "faster", "superior",
)
_CONFLICT_NEGATIVE_MARKERS = (
    "下降", "下滑", "下跌", "反降", "不升反降", "恶化", "低于", "退化", "无显著",
    "没有提升", "无益", "劣势", "worse", "degrade", "lower", "decrease",
    "no significant", "no benefit", "inferior", "fail", "falls short",
)


def _claim_text(card: dict[str, Any]) -> str:
    return str(card.get("_clean_claim") or card.get("claim") or "")[:200]


def _claim_tokens(text: str) -> set[str]:
    lowered = (text or "").lower()
    tokens = set(re.findall(r"[a-z0-9]{2,}", lowered))
    cjk = [ch for ch in lowered if "一" <= ch <= "鿿"]
    for idx in range(len(cjk) - 1):
        tokens.add(cjk[idx] + cjk[idx + 1])
    for idx in range(len(cjk) - 2):
        tokens.add(cjk[idx] + cjk[idx + 1] + cjk[idx + 2])
    return tokens


def _heuristic_conflict_groups(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback: same-topic claims with opposite polarity markers conflict."""
    tokenized = [(_claim_tokens(_claim_text(c)), _claim_text(c).lower()) for c in cards]
    groups: list[set[int]] = []
    n = len(cards)
    for i in range(n):
        for j in range(i + 1, n):
            if len(tokenized[i][0] & tokenized[j][0]) < 1:
                continue
            has_positive = (
                any(k in tokenized[i][1] for k in _CONFLICT_POSITIVE_MARKERS)
                or any(k in tokenized[j][1] for k in _CONFLICT_POSITIVE_MARKERS)
            )
            has_negative = (
                any(k in tokenized[i][1] for k in _CONFLICT_NEGATIVE_MARKERS)
                or any(k in tokenized[j][1] for k in _CONFLICT_NEGATIVE_MARKERS)
            )
            if has_positive and has_negative:
                merged = [g for g in groups if i in g or j in g]
                if merged:
                    target = merged[0]
                    for g in merged[1:]:
                        target |= g
                        groups.remove(g)
                    target.update({i, j})
                else:
                    groups.append({i, j})
    return [
        {
            "group_id": f"G{idx + 1}",
            "card_ids": [str(cards[k].get("id") or f"idx{k}") for k in sorted(g)],
            "topic": "相关证据结论相反",
            "summary": "启发式检测：同一主题的结论出现方向性相反表述",
        }
        for idx, g in enumerate(groups)
    ]


def detect_conflict_groups(
    cards: list[dict[str, Any]],
    research_question: str,
    max_groups: int = 5,
) -> list[dict[str, Any]]:
    """S4: group evidence claims that contradict each other.

    Returns ``[{"group_id": "G1", "card_ids": [...], "topic": "...", "summary": "..."}]``.
    Uses an LLM to judge contradiction (different viewpoints are NOT conflicts;
    only opposite conclusions on the same topic are), falling back to a
    polarity-keyword heuristic so the workflow never blocks.
    """
    if len(cards) < 2:
        return []

    claims_text = "\n".join(f"[{i}] {_claim_text(c)}" for i, c in enumerate(cards))

    from app.services.llm_service import chat_completion

    system_prompt = (
        "你是一位证据冲突检测专家。请找出结论相互矛盾的证据卡——即针对同一主题，"
        "主张直接相反（如一方说'提升'、另一方说'下降'）。\n"
        "注意：视角不同、侧重不同、互补性结论都不算冲突；只有直接矛盾才算。"
    )
    user_prompt = (
        f"研究问题：{research_question}\n\n以下证据卡按序号列出：\n{claims_text}\n\n"
        "请以 JSON 数组输出所有矛盾组，格式："
        '[{"card_ids": ["0", "1"], "topic": "冲突主题"}]。\n'
        "card_ids 为证据卡的序号（字符串），每组至少 2 张卡；无矛盾则输出 []。只输出 JSON 数组。"
    )

    groups: list[dict[str, Any]] = []
    try:
        for attempt in range(2):
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=800,
                timeout=60.0,
            )
            if result.get("error") and "reasoning consumed" in (result.get("error") or "").lower():
                continue
            text = (result.get("content") or "").strip()
            if not text:
                continue
            if "```" in text:
                import re as _re
                fence = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
                if fence:
                    text = fence.group(1)
            start, end = text.find("["), text.rfind("]")
            if start == -1 or end <= start:
                continue
            parsed = json.loads(text[start : end + 1])
            for g in parsed:
                ids = [str(x) for x in (g.get("card_ids") or [])]
                valid = []
                for x in ids:
                    try:
                        idx = int(x)
                        if 0 <= idx < len(cards):
                            valid.append(str(cards[idx].get("id") or x))
                    except (TypeError, ValueError):
                        continue
                if len(valid) >= 2:
                    groups.append(
                        {
                            "group_id": "",
                            "card_ids": valid,
                            "topic": str(g.get("topic") or "相关证据结论相反")[:80],
                            "summary": "LLM 判定为结论直接矛盾",
                        }
                    )
            if groups:
                break
    except Exception:
        logger.exception("Conflict detection LLM call failed")

    if not groups:
        groups = _heuristic_conflict_groups(cards)

    for idx, g in enumerate(groups[:max_groups]):
        g["group_id"] = f"G{idx + 1}"
    return groups[:max_groups]


def infer_evidence_type(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ["experiment", "trial", "empirical", "dataset"]):
        return "empirical_result"
    if any(keyword in lowered for keyword in ["survey", "questionnaire", "respondent"]):
        return "survey_result"
    if any(keyword in lowered for keyword in ["model", "simulation"]):
        return "model_result"
    return "textual_evidence"


# S2: strength is decided by research design / statistical signals, not word count.
# A concise "p<0.001, n=1000" is stronger evidence than a 300-word methods paragraph.
_HIGH_STRENGTH_MARKERS = (
    "meta-analysis", "meta analysis", "systematic review",
    "randomized", "randomised", "controlled trial", "cohort", "double-blind", "double blind",
    "荟萃分析", "系统综述", "元分析", "随机对照", "队列研究", "双盲",
)
_MEDIUM_STRENGTH_MARKERS = (
    "empirical", "experimental", "experiment", "benchmark", "evaluation",
    "survey", "regression", "simulation", "dataset", "case study", "measured", "reported",
    "实证", "实验", "基准", "调研", "回归", "模拟", "统计",
)
_STRONG_STAT_RE = re.compile(
    r"p\s*[<≤=]\s*\d|n\s*=\s*\d{2,}|statistically significant|effect size|hazard ratio|confidence interval"
)
_QUANT_RE = re.compile(r"\d+(?:\.\d+)?\s*%|\bpercent\b")


def infer_strength(text: str) -> str:
    """S2: evidence strength from research design + statistical signals.

    Before: strength was judged by word count (>=180 => high, >=80 => medium),
    which ranked long methods prose above precise statistical results. Now:
    RCT / meta-analysis / systematic review / cohort / double-blind / reported
    statistics (p<, n=, effect size) => high; empirical / experimental /
    benchmark / quantitative (% ) => medium; everything else => low.
    """
    lowered = (text or "").lower()
    if any(k in lowered for k in _HIGH_STRENGTH_MARKERS) or _STRONG_STAT_RE.search(lowered):
        return "high"
    if any(k in lowered for k in _MEDIUM_STRENGTH_MARKERS) or _QUANT_RE.search(lowered):
        return "medium"
    return "low"


def build_evidence_from_chunks(paper_id: str, chunks: list[dict[str, Any]], limit: int = 120) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for chunk in chunks[: max(1, min(limit, 300))]:
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        if not _looks_like_meaningful_text(text):
            continue
        evidence.append(
            {
                "paper_id": paper_id,
                "chunk_ids": [chunk["id"]],
                "claim": _first_sentence(text),
                "supporting_text": text[:1200],
                "evidence_type": infer_evidence_type(text),
                "strength": infer_strength(text),
                "limitations": "Auto-generated evidence card; manual verification required.",
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "citation_key": None,
                "used_in_draft": False,
            }
        )
    return evidence


def evidence_coverage(text: str, evidence_cards: list[dict[str, Any]]) -> float:
    if not text.strip():
        return 0.0
    if not evidence_cards:
        return 0.0
    tagged = text.count("[evidence:") + text.count("<!-- evidence:")
    paragraphs = len([line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")])
    if paragraphs == 0:
        return 0.0
    return min(1.0, tagged / max(1, paragraphs))


# ── Cross-card dedup (批次7) ────────────────────────────────────────────

_STRENGTH_ORDER = {"high": 2, "medium": 1, "low": 0}
_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def _claim_shingles(text: str) -> set[str]:
    """Character-bigram shingles for CJK + word tokens for latin text."""
    lowered = (text or "").lower()
    grams = set(_WORD_RE.findall(lowered))
    chars = _CJK_RE.findall(lowered)
    grams.update(chars[i] + chars[i + 1] for i in range(len(chars) - 1))
    return grams


def _shingle_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe_evidence_cards(
    cards: list[Any],
    threshold: float = 0.72,
) -> tuple[list[Any], int]:
    """Drop near-duplicate evidence cards (same finding, multiple chunks/papers).

    The same finding restated across papers used to enter the pool as N
    separate cards; with the per-section bucket cap they crowded out more
    diverse evidence. Similarity = Jaccard over *claim* shingles (supporting
    text differs legitimately between restatements and would dilute the
    score); the keeper is chosen by strength, then by longer supporting text.
    Returns (kept_cards, dropped_count).
    """
    kept: list[tuple[Any, set[str]]] = []
    dropped = 0
    for card in cards:
        shingles = _claim_shingles(card.claim or "")
        if not shingles:
            kept.append((card, shingles))
            continue
        duplicate_index = None
        for i, (_, existing) in enumerate(kept):
            if not existing:
                continue
            if _shingle_jaccard(shingles, existing) >= threshold:
                duplicate_index = i
                break
        if duplicate_index is None:
            kept.append((card, shingles))
            continue
        dropped += 1
        loser, winner = card, kept[duplicate_index][0]
        # Keep the stronger card: strength rank, then richer supporting text.
        if (_STRENGTH_ORDER.get(str(loser.strength or ""), 0),
                len(loser.supporting_text or "")) > \
           (_STRENGTH_ORDER.get(str(winner.strength or ""), 0),
                len(winner.supporting_text or "")):
            kept[duplicate_index] = (loser, shingles)
    return [card for card, _ in kept], dropped
