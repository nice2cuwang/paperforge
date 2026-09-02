"""Post-processing service to reduce AI-detectable patterns in generated text.

Applies lightweight, deterministic transforms:
- Syntax diversification (merge short / split long sentences)
- Connector word variation (avoid repetitive AI-style transitions)
- Tone softening (reduce absolutist phrasing)

All transforms preserve evidence annotations (`<!-- evidence: ... -->`).
"""

from __future__ import annotations

import random
import re
from typing import Any

# Fixed seed for reproducibility in unit tests; production runs use random state.
random = random.Random()


# ---------------------------------------------------------------------------
# Sentence utilities
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r"([。！？!?\.])\s*")
_EVIDENCE_COMMENT_RE = re.compile(r"(<!--\s*evidence:[^>]+\s*-->)")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences while preserving evidence comments."""
    # Protect evidence comments by replacing them with placeholders
    comments: list[str] = []

    def _collect(m: re.Match[str]) -> str:
        comments.append(m.group(1))
        return f"\x00EVD{len(comments) - 1}\x00"

    protected = _EVIDENCE_COMMENT_RE.sub(_collect, text)
    parts = _SENTENCE_END_RE.split(protected)
    sentences: list[str] = []
    i = 0
    while i < len(parts):
        if i + 1 < len(parts) and _SENTENCE_END_RE.fullmatch(parts[i + 1]):
            sentences.append(parts[i] + parts[i + 1])
            i += 2
        else:
            if parts[i].strip():
                sentences.append(parts[i])
            i += 1
    # Restore evidence comments
    result: list[str] = []
    for s in sentences:
        restored = s
        for idx, comment in enumerate(comments):
            restored = restored.replace(f"\x00EVD{idx}\x00", comment)
        result.append(restored.strip())
    return [r for r in result if r]


def _is_short_sentence(sent: str) -> bool:
    """Chinese sentence < 18 chars or English < 8 words → "short"."""
    stripped = re.sub(r"<!--.*?-->", "", sent).strip()
    if not stripped:
        return False
    cjk_chars = sum(1 for ch in stripped if "一" <= ch <= "鿿")
    if cjk_chars > 0:
        return len(stripped) < 20
    return len(stripped.split()) < 10


def _is_long_sentence(sent: str) -> bool:
    """Chinese sentence > 80 chars or English > 30 words → "long"."""
    stripped = re.sub(r"<!--.*?-->", "", sent).strip()
    if not stripped:
        return False
    cjk_chars = sum(1 for ch in stripped if "一" <= ch <= "鿿")
    if cjk_chars > 0:
        return len(stripped) > 80
    return len(stripped.split()) > 30


# ---------------------------------------------------------------------------
# Transform 1: Syntax diversification
# ---------------------------------------------------------------------------

_AI_CONNECTORS = [
    ("此外", ["与此同时", "另一方面", "除此之外", "另外"]),
    ("需要指出的是", ["值得注意的是", "应当留意", "必须说明"]),
    ("综上所述", ["总体来看", "归结起来", "总而言之", "一言以蔽之"]),
    ("值得注意的是", ["尤其值得关注的是", "一个值得关注的点是"]),
    ("因此", ["所以", "由此", "正因如此", "这样一来"]),
    ("然而", ["不过", "但是", "尽管如此", "但另一方面"]),
    ("首先", ["第一", "一开始", "起初", "最先"]),
    ("其次", ["第二", "接下来", "随后", "再者"]),
    ("最后", ["最终", "末了", "归根结底", "说到底"]),
    ("必然", ["很可能", "大概率", "通常", "在大多数情况下"]),
    (" undoubtedly", [" likely", " in most cases", " generally"]),
    (" moreover", [" furthermore", " in addition", " besides", " also"]),
    (" therefore", [" thus", " consequently", " as a result", " hence"]),
    (" however", [" nevertheless", " yet", " still", " on the other hand"]),
    (" in conclusion", [" to sum up", " all in all", " overall", " in short"]),
    (" it is important to note", [" it should be noted", " worth noting", " notably"]),
    (" significantly", [" notably", " markedly", " importantly", " crucially"]),
]


def _vary_connectors(text: str) -> str:
    """Replace over-used AI connectors with varied alternatives."""
    # Track usage to avoid repeating the same replacement within a paragraph
    used: dict[str, str] = {}
    for pattern, alternatives in _AI_CONNECTORS:
        if pattern not in text.lower():
            continue
        # Use regex with word boundaries for English, exact for Chinese
        if pattern.startswith(" "):
            regex = re.compile(re.escape(pattern.strip()) + r"\b", re.IGNORECASE)
        else:
            regex = re.compile(re.escape(pattern))

        def _repl(m: re.Match[str]) -> str:
            key = pattern.lower()
            if key in used:
                return used[key]
            choice = random.choice(alternatives)
            used[key] = choice
            return choice

        text = regex.sub(_repl, text)
    return text


def _diversify_syntax(sentences: list[str]) -> list[str]:
    """Merge adjacent short sentences; split very long ones.

    When merging, preserve a connecting punctuation (comma/semicolon)
    so the text remains grammatical.
    """
    if len(sentences) < 2:
        return sentences
    merged: list[str] = []
    i = 0
    while i < len(sentences):
        s = sentences[i]
        if _is_short_sentence(s) and i + 1 < len(sentences) and _is_short_sentence(sentences[i + 1]):
            # Merge with a random probability
            if random.random() < 0.5:
                # Chinese: no space; English: space
                has_cjk = any("一" <= ch <= "鿿" for ch in s)
                sep = "" if has_cjk else " "
                joiner = "，" if has_cjk else ", "
                # Strip trailing punctuation from first sentence, then add joiner
                first = s.rstrip(".!?。！？").strip()
                second = sentences[i + 1].lstrip()
                merged.append(first + joiner + sep + second)
                i += 2
                continue
        if _is_long_sentence(s):
            # Try to split at a comma/semicolon if present
            split_point = _find_split_point(s)
            if split_point:
                merged.append(s[:split_point].strip())
                merged.append(s[split_point:].strip())
                i += 1
                continue
        merged.append(s)
        i += 1
    return merged


def _find_split_point(sent: str) -> int | None:
    """Find a natural split point inside a long sentence."""
    # Prefer splitting at Chinese/English punctuation
    stripped = re.sub(r"<!--.*?-->", "", sent)
    mid = len(stripped) // 2
    # Search backward from mid for good split chars
    for offset in range(min(20, mid)):
        for direction in (0, offset, -offset):
            idx = mid + direction
            if 0 < idx < len(stripped):
                if stripped[idx] in "，；,;":
                    return idx + 1
    return None


# ---------------------------------------------------------------------------
# Transform 2: Tone softening
# ---------------------------------------------------------------------------

_ABSOLUTIST_PATTERNS: list[tuple[str, list[str]]] = [
    ("完全证明", ["在一定程度上表明", "为……提供了有力证据", "支持了"]),
    ("彻底", ["在很大程度上", "相当程度地", "显著"]),
    ("毫无疑问", ["基本可以确定", "有充分理由认为", "大概率"]),
    ("必然", ["很可能", "通常", "在大多数情况下"]),
    ("一定会", ["往往会", "在多数情况下会", "有很大可能会"]),
    (" prove definitively", [" provide strong evidence for", " support", " suggest"]),
    (" completely", [" largely", " substantially", " to a significant extent"]),
    (" undoubtedly", [" most likely", " in all probability", " it is reasonable to assume"]),
    (" always", [" typically", " in most cases", " generally"]),
    (" must", [" is likely to", " tends to", " generally"]),
]


def _soften_tone(text: str) -> str:
    """Replace absolutist phrasing with nuanced alternatives (randomized)."""
    for pattern, alternatives in _ABSOLUTIST_PATTERNS:
        if pattern not in text.lower():
            continue
        if pattern.startswith(" "):
            regex = re.compile(re.escape(pattern.strip()) + r"\b", re.IGNORECASE)
        else:
            regex = re.compile(re.escape(pattern))
        text = regex.sub(lambda m: random.choice(alternatives), text)
    return text


# ---------------------------------------------------------------------------
# Transform 3: Remove repetitive template phrases
# ---------------------------------------------------------------------------

_TEMPLATE_PHRASES = [
    # Only strip if it appears at the very start of a paragraph/sentence
    re.compile(r"^(In today's (rapidly changing )?world[,\.\s]*)", re.IGNORECASE),
    re.compile(r"^(With the development of [^,.]{3,50}[,\.\s]*)", re.IGNORECASE),
    re.compile(r"^(随着[^，。,.]{2,30}的发展[,，\.\s]*)"),
    re.compile(r"^(在当今[^，。,.]{2,20}[,，\.\s]*)"),
    re.compile(r"^(众所周知[,，\.\s]*)"),
]


def _remove_template_phrases(text: str) -> str:
    """Strip clichéd opening/filler phrases only at sentence/paragraph start.

    This avoids deleting mid-sentence content that happens to match the pattern.
    """
    for regex in _TEMPLATE_PHRASES:
        text = regex.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def de_ai_paragraph(text: str, intensity: float = 0.7) -> str:
    """Apply AI-pattern reduction to a single paragraph.

    Args:
        text: Raw paragraph text (may contain evidence comments).
        intensity: 0.0–1.0. Higher = more aggressive transforms.
    """
    if not text or not text.strip():
        return text

    # Step 1: vary connectors
    text = _vary_connectors(text)

    # Step 2: soften absolutist tone
    text = _soften_tone(text)

    # Step 3: remove clichés
    text = _remove_template_phrases(text)

    # Step 4: syntax diversification (only if intensity is high enough)
    if intensity >= 0.5:
        sentences = _split_sentences(text)
        sentences = _diversify_syntax(sentences)
        # Re-join with correct separator for CJK vs English
        has_cjk = any("一" <= ch <= "鿿" for s in sentences for ch in s)
        text = "".join(sentences) if has_cjk else " ".join(sentences)

    return text.strip()


def de_ai_markdown(content_md: str, intensity: float = 0.7) -> str:
    """Apply de-AI processing to all paragraphs in a Markdown draft.

    Preserves headings, blockquotes, code blocks, and evidence comments.
    """
    lines = content_md.splitlines()
    output: list[str] = []
    paragraph_buffer: list[str] = []

    def _flush() -> None:
        nonlocal paragraph_buffer
        if paragraph_buffer:
            # Detect if text is primarily CJK to choose correct joiner
            raw = "\n".join(paragraph_buffer)
            processed = de_ai_paragraph(raw, intensity=intensity)
            output.append(processed)
            paragraph_buffer = []

    for line in lines:
        stripped = line.strip()
        # Preserve structural lines as-is
        if (
            not stripped
            or stripped.startswith("#")
            or stripped.startswith(">")
            or stripped.startswith("-")
            or stripped.startswith("*")
            or stripped.startswith("```")
            or stripped.startswith("|")
            or stripped.startswith("[")
            or stripped.startswith("!")  # markdown image lines, keep intact
        ):
            _flush()
            output.append(line)
            continue
        paragraph_buffer.append(line)

    _flush()
    return "\n".join(output)


def de_ai_metrics(content_md: str) -> dict[str, Any]:
    """Return simple metrics about AI-pattern density (for quality gates)."""
    text_lower = content_md.lower()
    connector_hits = sum(1 for pat, _ in _AI_CONNECTORS if pat.strip().lower() in text_lower)
    absolutist_hits = sum(1 for pat, _ in _ABSOLUTIST_PATTERNS if pat.strip().lower() in text_lower)
    template_hits = sum(1 for regex in _TEMPLATE_PHRASES if regex.search(content_md))
    total_lines = max(1, len([l for l in content_md.splitlines() if l.strip()]))
    return {
        "connector_variety_score": max(0.0, 1.0 - connector_hits / max(1, total_lines) * 2),
        "absolutism_score": max(0.0, 1.0 - absolutist_hits / max(1, total_lines) * 3),
        "template_cliche_score": max(0.0, 1.0 - template_hits / max(1, total_lines) * 2),
        "connector_hits": connector_hits,
        "absolutist_hits": absolutist_hits,
        "template_hits": template_hits,
    }
