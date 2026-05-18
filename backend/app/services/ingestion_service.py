from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

PDF_BINARY_LINE_RE = re.compile(
    r"(%PDF-|/FlateDecode|/DecodeParms|/Type/Catalog|\bxref\b|\bendobj\b|\bendstream\b|\bstream\b|^\d+\s+\d+\s+obj\b|^<<.*>>$)",
    flags=re.IGNORECASE,
)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
XML_TAG_RE = re.compile(r"<[^>]{1,200}>")
WORD_RE = re.compile(r"[A-Za-z]{2,}")
CJK_WORD_RE = re.compile(r"[\u4E00-\u9FFF]{2,}")
REPLACEMENT_CHAR = "\ufffd"

ALLOWED_SYMBOLS = set(
    ".,;:!?()[]{}<>\"'`~*_#@|\\$%^&+-/=，。；：！？（）【】《》“”‘’、·—…"
)


def save_uploaded_pdf(base_dir: Path, project_id: str, paper_id: str, filename: str, content: bytes) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", filename or "upload.pdf")
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    target_dir = base_dir / "storage" / project_id / "pdf"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{paper_id}_{safe_name}"
    target.write_bytes(content)
    return target


def save_tei_placeholder(base_dir: Path, project_id: str, paper_id: str, text: str) -> Path:
    target_dir = base_dir / "storage" / project_id / "tei"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{paper_id}.tei.xml"
    target.write_text(
        "<TEI><text><body>"
        + "".join(f"<p>{escape_xml(line)}</p>" for line in text.splitlines() if line.strip())
        + "</body></text></TEI>",
        encoding="utf-8",
    )
    return target


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def extract_pdf_text(pdf_path: Path) -> str:
    # 1) Pure-Python parser.
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        pages: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Page {index}]\n{text}")
        merged = _normalize_extracted_text("\n\n".join(pages).strip())
        if _looks_like_meaningful_text(merged):
            return merged
    except Exception:
        pass

    # 2) PyMuPDF fallback if available.
    try:
        import fitz  # type: ignore

        pages = []
        with fitz.open(pdf_path) as doc:
            for index, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                if text:
                    pages.append(f"[Page {index}]\n{text}")
        merged = _normalize_extracted_text("\n\n".join(pages).strip())
        if _looks_like_meaningful_text(merged):
            return merged
    except Exception:
        pass

    # 3) Last fallback: decode bytes, then aggressively filter binary/object noise.
    raw = pdf_path.read_bytes()
    fallback = _sanitize_fallback_text(raw.decode("utf-8", errors="ignore"))
    if _looks_like_meaningful_text(fallback):
        return fallback
    return "Unable to parse PDF content."


def _sanitize_fallback_text(text: str) -> str:
    if not text:
        return ""
    text = _normalize_extracted_text(text)
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if PDF_BINARY_LINE_RE.search(line):
            continue
        if XML_TAG_RE.search(line):
            continue
        if not _looks_like_natural_line(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return 0x4E00 <= code <= 0x9FFF


def _is_allowed_symbol(ch: str) -> bool:
    if ch in ALLOWED_SYMBOLS:
        return True
    cat = unicodedata.category(ch)
    # P*: punctuation categories
    return cat.startswith("P")


def _normalize_extracted_text(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace("\x00", "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.strip()


def _looks_like_natural_line(text: str) -> bool:
    compact = " ".join((text or "").split())
    if len(compact) < 12:
        return False
    if CONTROL_CHAR_RE.search(compact):
        return False
    if REPLACEMENT_CHAR in compact:
        return False

    content = [ch for ch in compact if not ch.isspace()]
    if not content:
        return False

    printable = sum(1 for ch in content if ch.isprintable())
    if printable / len(content) < 0.97:
        return False

    natural = sum(1 for ch in content if ch.isalnum() or _is_cjk(ch))
    if natural / len(content) < 0.4:
        return False

    weird = sum(
        1
        for ch in content
        if (not ch.isalnum()) and (not _is_cjk(ch)) and (not _is_allowed_symbol(ch))
    )
    if weird / len(content) > 0.4:
        return False

    natural_tokens = len(WORD_RE.findall(compact)) + len(CJK_WORD_RE.findall(compact))
    return natural_tokens >= 1


def _looks_like_meaningful_text(text: str) -> bool:
    normalized = _normalize_extracted_text(text)
    if not normalized:
        return False
    if len(normalized) < 20:
        return False
    if CONTROL_CHAR_RE.search(normalized):
        return False
    if PDF_BINARY_LINE_RE.search(normalized):
        return False
    if REPLACEMENT_CHAR in normalized:
        return False

    content_chars = [ch for ch in normalized if not ch.isspace()]
    if not content_chars:
        return False

    natural = sum(1 for ch in content_chars if ch.isalpha() or _is_cjk(ch) or ch.isdigit())
    natural_ratio = natural / len(content_chars)
    if natural_ratio < 0.35:
        return False

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines:
        good_lines = sum(1 for line in lines if _looks_like_natural_line(line))
        if good_lines == 0:
            return False
        if len(lines) >= 8 and (good_lines / len(lines)) < 0.1:
            return False

    natural_tokens = len(WORD_RE.findall(normalized)) + len(CJK_WORD_RE.findall(normalized))
    return natural_tokens >= 3


def chunk_text(raw_text: str, chunk_size: int = 900) -> list[dict[str, Any]]:
    if raw_text.strip().lower().startswith("unable to parse pdf content"):
        return []

    blocks = [block.strip() for block in re.split(r"\n{2,}", raw_text) if block.strip()]
    if not blocks:
        return []

    chunks: list[dict[str, Any]] = []
    page_hint = 1
    for block in blocks:
        page_match = re.search(r"\[Page\s+(\d+)\]", block, flags=re.IGNORECASE)
        if page_match:
            page_hint = int(page_match.group(1))
        text = re.sub(r"\[Page\s+\d+\]\s*", "", block).strip()
        if not text:
            continue
        cursor = 0
        while cursor < len(text):
            segment = text[cursor : cursor + chunk_size].strip()
            cursor += chunk_size
            if not segment:
                continue
            if not _looks_like_meaningful_text(segment):
                continue
            chunks.append(
                {
                    "id": str(uuid4()),
                    "section": "Body",
                    "subsection": None,
                    "page_start": page_hint,
                    "page_end": page_hint,
                    "text": segment,
                    "token_count": max(1, len(segment.split())),
                    "vector_id": None,
                    "metadata_json": {},
                }
            )
    return chunks
