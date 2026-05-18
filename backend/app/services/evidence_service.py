from __future__ import annotations

import re
from typing import Any


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


def infer_evidence_type(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ["experiment", "trial", "empirical", "dataset"]):
        return "empirical_result"
    if any(keyword in lowered for keyword in ["survey", "questionnaire", "respondent"]):
        return "survey_result"
    if any(keyword in lowered for keyword in ["model", "simulation"]):
        return "model_result"
    return "textual_evidence"


def infer_strength(text: str) -> str:
    words = len(text.split())
    if words >= 180:
        return "high"
    if words >= 80:
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
