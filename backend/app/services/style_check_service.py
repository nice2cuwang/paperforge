"""Style consistency checker for publication-grade text.

Detects:
- Terminology inconsistency (same concept, different words)
- Passive voice overuse
- Monotonous sentence length
- Pronoun/person mixing
- Mixed CJK/Latin punctuation
- Register shifts (formal vs informal)
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

_SENTENCE_RE = re.compile(r"(?<=[。！？!?\.])\s*")
_EVIDENCE_RE = re.compile(r"<!--\s*evidence:[^>]+\s*-->")


def _sentences(text: str) -> list[str]:
    clean = _EVIDENCE_RE.sub("", text)
    parts = _SENTENCE_RE.split(clean)
    return [p.strip() for p in parts if p.strip() and not p.strip().startswith("#")]


# ---------------------------------------------------------------------------
# 1. Terminology consistency
# ---------------------------------------------------------------------------

# Maps of common synonyms that should NOT be mixed in the same document
_TERM_VARIATIONS: dict[str, list[str]] = {
    "机器学习": ["机器学习", "machine learning", "ML"],
    "深度学习": ["深度学习", "deep learning", "DL"],
    "人工智能": ["人工智能", "artificial intelligence", "AI"],
    "神经网络": ["神经网络", "neural network", "NN"],
    "大型语言模型": ["大型语言模型", "大语言模型", "LLM", "large language model"],
    "数据集": ["数据集", "data set", "dataset"],
    "算法": ["算法", "algorithm"],
    "模型": ["模型", "model"],
    "实验": ["实验", "experiment", "试验"],
    "结果": ["结果", "outcome", "成果"],
    "方法": ["方法", "method", "methodology", "approach"],
    "分析": ["分析", "analysis", "解析"],
    "性能": ["性能", "performance", "表现"],
    "准确率": ["准确率", "accuracy", "精确度"],
    "精确率": ["精确率", "precision"],
    "召回率": ["召回率", "recall"],
    "F1分数": ["F1分数", "F1 score", "F1值"],
}


def check_terminology(text: str) -> list[dict[str, Any]]:
    """Detect mixed terminology for the same concept."""
    issues: list[dict[str, Any]] = []
    text_lower = text.lower()
    for concept, variants in _TERM_VARIATIONS.items():
        found = [v for v in variants if v.lower() in text_lower]
        if len(found) > 1:
            issues.append({
                "severity": "low",
                "issue_type": "style",
                "location": "global",
                "claim": f"Mixed terms for '{concept}'",
                "description": f"文中同时使用了 {', '.join(found)}，建议统一术语。",
                "suggestion": f"统一使用一个术语表达'{concept}'。",
                "evidence_ids": [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# 2. Passive voice detection
# ---------------------------------------------------------------------------

_CJK_PASSIVE_PATTERNS = [
    re.compile(r"被\s*\w+\s*(用于|采用|应用|提出|设计|开发|训练|测试|验证|证明|实现)"),
    re.compile(r"由\s*\w+\s*(提出|设计|开发|完成|实现|构建)"),
    re.compile(r"通过\s*\w+\s*(方法|技术|模型|算法)\s*(实现|完成|达到)"),
]

_ENG_PASSIVE_PATTERNS = [
    re.compile(r"\b\w+ed\s+by\b", re.IGNORECASE),
    re.compile(r"\b(is|are|was|were|has been|have been|had been)\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\b(is|are|was|were)\s+(made|done|found|shown|given|taken|used|built|developed)\b", re.IGNORECASE),
]


def check_passive_voice(text: str) -> tuple[int, list[dict[str, Any]]]:
    """Count passive constructions. Returns (hit_count, issue_list).

    NOTE: Passive voice is normal in academic writing; only flag
    when density is unusually high (>5 hits in a short text).
    """
    hits = 0
    lines = text.splitlines()
    line_hits_list: list[tuple[int, int, str]] = []  # (line_no, hits, line_text)
    for idx, line in enumerate(lines, start=1):
        line_hits = 0
        for pat in _CJK_PASSIVE_PATTERNS + _ENG_PASSIVE_PATTERNS:
            line_hits += len(pat.findall(line))
        if line_hits:
            hits += line_hits
            line_hits_list.append((idx, line_hits, line[:120]))

    issues: list[dict[str, Any]] = []
    # Only flag if passive density is high relative to text length
    text_lines = max(1, len([l for l in lines if l.strip()]))
    if hits > 5 and hits / text_lines > 0.15:
        for idx, line_hits, line_text in line_hits_list[:3]:
            issues.append({
                "severity": "low",
                "issue_type": "style",
                "location": f"line-{idx}",
                "claim": line_text,
                "description": f"检测到 {hits} 处被动语态，密度偏高。",
                "suggestion": "适当减少被动语态以提升可读性。",
                "evidence_ids": [],
                "resolved": False,
            })
    return hits, issues


# ---------------------------------------------------------------------------
# 3. Sentence length monotony
# ---------------------------------------------------------------------------


def check_sentence_variety(text: str) -> tuple[float, list[dict[str, Any]]]:
    """Check if sentence lengths are too uniform (AI signature).

    Returns (score, issues). Score 1.0 = good variety, 0.0 = monotonous.
    """
    sents = _sentences(text)
    if len(sents) < 5:
        return 1.0, []

    lengths = [len(s) for s in sents]
    avg = sum(lengths) / len(lengths)
    variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
    std = variance ** 0.5
    cv = std / max(1, avg)  # coefficient of variation

    score = min(1.0, cv * 4)  # cv > 0.25 → score 1.0
    issues: list[dict[str, Any]] = []
    if score < 0.5:
        issues.append({
            "severity": "low",
            "issue_type": "style",
            "location": "global",
            "claim": "Sentence lengths too uniform",
            "description": f"句子长度变异系数仅 {cv:.2f}，可能显露出 AI 写作特征。",
            "suggestion": "混合长短句，增加句式变化。",
            "evidence_ids": [],
            "resolved": False,
        })
    return score, issues


# ---------------------------------------------------------------------------
# 4. Pronoun / person mixing
# ---------------------------------------------------------------------------


def check_person_consistency(text: str) -> list[dict[str, Any]]:
    """Detect switching between first/third person within a section."""
    issues: list[dict[str, Any]] = []
    first_person = len(re.findall(r"\b(我|我们|our|we|my|myself)\b", text, re.IGNORECASE))
    third_person = len(re.findall(r"\b(本文|本研究|该研究|本文作者|this paper|this study|the authors)\b", text, re.IGNORECASE))

    if first_person > 0 and third_person > 0:
        issues.append({
            "severity": "medium",
            "issue_type": "style",
            "location": "global",
            "claim": "Mixed first and third person references",
            "description": f"文中同时出现第一人称({first_person}次)和第三人称({third_person}次)指代。",
            "suggestion": "统一使用第一人称('我们')或第三人称('本文')，不要混用。",
            "evidence_ids": [],
            "resolved": False,
        })
    return issues


# ---------------------------------------------------------------------------
# 5. Mixed punctuation
# ---------------------------------------------------------------------------


def check_punctuation_consistency(text: str) -> list[dict[str, Any]]:
    """Warn if CJK and Latin punctuation are mixed haphazardly."""
    issues: list[dict[str, Any]] = []
    cjk_commas = text.count("，")
    lat_commas = text.count(",")
    cjk_periods = text.count("。")
    lat_periods = text.count(".")

    if cjk_commas > 5 and lat_commas > 5:
        issues.append({
            "severity": "low",
            "issue_type": "style",
            "location": "global",
            "claim": "Mixed CJK and Latin commas",
            "description": f"中文逗号({cjk_commas})和英文逗号({lat_commas})混用。",
            "suggestion": "统一使用中文标点（中文文档）或英文标点（英文文档）。",
            "evidence_ids": [],
            "resolved": False,
        })
    if cjk_periods > 5 and lat_periods > 5:
        issues.append({
            "severity": "low",
            "issue_type": "style",
            "location": "global",
            "claim": "Mixed CJK and Latin periods",
            "description": f"中文句号({cjk_periods})和英文句点({lat_periods})混用。",
            "suggestion": "统一使用中文标点（中文文档）或英文标点（英文文档）。",
            "evidence_ids": [],
            "resolved": False,
        })
    return issues


# ---------------------------------------------------------------------------
# 6. Register / formality shifts
# ---------------------------------------------------------------------------

_INFORMAL_MARKERS = [
    "挺", "挺不错", "还行", "蛮", "蛮好", "蛮不错",
    "蛮重要的", " kinda", " sorta", " pretty good", " basically",
    " you know", " I mean", " well,", " like,",
]


def check_register(text: str) -> list[dict[str, Any]]:
    """Detect informal markers in what should be formal writing."""
    issues: list[dict[str, Any]] = []
    for marker in _INFORMAL_MARKERS:
        if marker.lower() in text.lower():
            issues.append({
                "severity": "low",
                "issue_type": "style",
                "location": "global",
                "claim": f"Informal marker: '{marker.strip()}'",
                "description": f"检测到非正式表达'{marker.strip()}'。",
                "suggestion": "改用更正式的学术表达。",
                "evidence_ids": [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# 7. Repetitive sentence openers
# ---------------------------------------------------------------------------


def check_repetitive_openers(text: str) -> list[dict[str, Any]]:
    """Detect paragraphs that all start with the same word/phrase."""
    issues: list[dict[str, Any]] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    openers: list[str] = []
    for p in paragraphs:
        first_sent = _sentences(p)[0] if _sentences(p) else ""
        # Extract first 2-4 chars (CJK) or first word (ENG)
        cjk_prefix = re.match(r"[一-鿿]{2,4}", first_sent)
        if cjk_prefix:
            openers.append(cjk_prefix.group())
        else:
            first_word = re.match(r"\b\w+\b", first_sent)
            if first_word:
                openers.append(first_word.group().lower())

    if not openers:
        return issues

    counter = Counter(openers)
    total = len(openers)
    for opener, count in counter.most_common(3):
        if count >= 3 and count / total > 0.3:
            issues.append({
                "severity": "low",
                "issue_type": "style",
                "location": "global",
                "claim": f"Repetitive opener: '{opener}'",
                "description": f"{count}/{total} 个段落以'{opener}'开头，显得单调。",
                "suggestion": "变换段落开头方式，使用过渡词或不同的切入角度。",
                "evidence_ids": [],
                "resolved": False,
            })
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_style(content_md: str, article_type: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run full style check suite.

    Returns (issues, metrics).
    """
    all_issues: list[dict[str, Any]] = []

    all_issues.extend(check_terminology(content_md))

    passive_hits, passive_issues = check_passive_voice(content_md)
    all_issues.extend(passive_issues)

    variety_score, variety_issues = check_sentence_variety(content_md)
    all_issues.extend(variety_issues)

    all_issues.extend(check_person_consistency(content_md))
    all_issues.extend(check_punctuation_consistency(content_md))
    all_issues.extend(check_register(content_md))
    all_issues.extend(check_repetitive_openers(content_md))

    metrics = {
        "style_issue_count": len(all_issues),
        "passive_voice_hits": passive_hits,
        "sentence_variety_score": round(variety_score, 3),
        "terminology_mixed_concepts": len([i for i in all_issues if "Mixed terms" in i.get("claim", "")]),
    }
    return all_issues, metrics
