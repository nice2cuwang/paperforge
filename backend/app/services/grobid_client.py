from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.services.http_client import create_httpx_client

logger = logging.getLogger(__name__)

GROBID_BASE_URL = os.getenv("GROBID_URL", "http://localhost:8070")
_GROBID_TIMEOUT = float(os.getenv("GROBID_TIMEOUT", "120"))


def _grobid_url(path: str) -> str:
    base = GROBID_BASE_URL.rstrip("/")
    return f"{base}{path}"


def is_available() -> bool:
    """Check if GROBID service is reachable."""
    try:
        with create_httpx_client(timeout=3.0) as client:
            resp = client.get(_grobid_url("/api/isalive"))
            return resp.status_code == 200 and "true" in resp.text.lower()
    except Exception:
        return False


def parse_pdf(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    """Send PDF to GROBID and return (TEI XML string, metadata dict).

    Raises on failure so caller can fall back to PyMuPDF.
    """
    from app.middleware.metrics import metrics_inc_tagged

    url = _grobid_url("/api/processFulltextDocument")
    try:
        with create_httpx_client(timeout=_GROBID_TIMEOUT) as client:
            with open(pdf_path, "rb") as f:
                files = {"input": (pdf_path.name, f, "application/pdf")}
                data = {"consolidateHeader": "0", "includeRawCitations": "1", "includeRawAffiliations": "0"}
                resp = client.post(url, data=data, files=files)
            resp.raise_for_status()
            tei_text = resp.text
            meta = _extract_metadata(tei_text)
            metrics_inc_tagged("paperforge_grobid_api_calls", "ok")
            return tei_text, meta
    except Exception:
        metrics_inc_tagged("paperforge_grobid_api_calls", "err")
        raise


def _ns(tag: str) -> str:
    return f"{{http://www.tei-c.org/ns/1.0}}{tag}"


def _extract_metadata(tei_text: str) -> dict[str, Any]:
    """Extract lightweight metadata from GROBID TEI."""
    meta: dict[str, Any] = {"title": None, "abstract": None, "sections": []}
    try:
        root = ET.fromstring(tei_text.encode("utf-8"))
    except ET.ParseError:
        return meta

    # Title
    title_elem = root.find(f".//{_ns('titleStmt')}/{_ns('title')}")
    if title_elem is not None and title_elem.text:
        meta["title"] = title_elem.text.strip()

    # Abstract
    abstract_elem = root.find(f".//{_ns('profileDesc')}/{_ns('abstract')}//{_ns('p')}")
    if abstract_elem is not None and abstract_elem.text:
        meta["abstract"] = abstract_elem.text.strip()

    # Sections (head + p)
    body = root.find(f".//{_ns('text')}/{_ns('body')}")
    if body is not None:
        for div in body.iter(_ns("div")):
            head = div.find(_ns("head"))
            head_text = (head.text or "").strip() if head is not None else ""
            paragraphs: list[str] = []
            for p in div.iter(_ns("p")):
                if p.text:
                    paragraphs.append(p.text.strip())
            if head_text or paragraphs:
                meta["sections"].append({"head": head_text, "paragraphs": paragraphs})

    return meta


def tei_to_plaintext(tei_text: str) -> str:
    """Convert GROBID TEI to plain text with [Page N] hints."""
    lines: list[str] = []
    try:
        root = ET.fromstring(tei_text.encode("utf-8"))
    except ET.ParseError:
        return ""

    body = root.find(f".//{_ns('text')}/{_ns('body')}")
    if body is None:
        return ""

    for div in body.iter(_ns("div")):
        head = div.find(_ns("head"))
        if head is not None and head.text:
            lines.append(head.text.strip())
        for p in div.iter(_ns("p")):
            if p.text:
                lines.append(p.text.strip())

    return "\n\n".join(lines)


def extract_sections_with_pages(tei_text: str) -> list[dict[str, Any]]:
    """Extract sections and attempt to infer page numbers from <pb> tags."""
    sections: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(tei_text.encode("utf-8"))
    except ET.ParseError:
        return sections

    body = root.find(f".//{_ns('text')}/{_ns('body')}")
    if body is None:
        return sections

    current_page = 1
    for div in body.iter(_ns("div")):
        head = div.find(_ns("head"))
        head_text = (head.text or "").strip() if head is not None else ""
        paragraphs: list[str] = []
        for elem in div:
            if elem.tag == _ns("pb"):
                n = elem.get("n")
                if n and re.match(r"^\d+$", n):
                    current_page = int(n)
            if elem.tag == _ns("p") and elem.text:
                paragraphs.append(elem.text.strip())
        if head_text or paragraphs:
            sections.append({"head": head_text, "paragraphs": paragraphs, "page_start": current_page})
    return sections
