from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def ensure_export_dir(base_dir: Path, project_id: str) -> Path:
    from app.services.storage import get_storage_backend
    path_str = get_storage_backend().ensure_export_dir(project_id)
    return Path(path_str) if not path_str.startswith("s3://") else Path(path_str)


def export_markdown(target_dir: Path, filename: str, content_md: str) -> Path:
    path = target_dir / filename
    path.write_text(content_md, encoding="utf-8")
    return path


def export_docx(target_dir: Path, filename: str, content_md: str) -> Path:
    path = target_dir / filename
    try:
        from docx import Document  # type: ignore

        doc = Document()
        for line in content_md.splitlines():
            if line.startswith("# "):
                doc.add_heading(line[2:].strip(), level=1)
            elif line.startswith("## "):
                doc.add_heading(line[3:].strip(), level=2)
            elif line.startswith("### "):
                doc.add_heading(line[4:].strip(), level=3)
            elif line.strip():
                doc.add_paragraph(line.strip())
        doc.save(path)
        return path
    except Exception:
        # Graceful fallback to plain text with .docx suffix.
        path.write_text(content_md, encoding="utf-8")
        return path


def export_pdf(target_dir: Path, filename: str, content_md: str) -> Path:
    path = target_dir / filename
    try:
        from fpdf import FPDF  # type: ignore

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        # Register CJK font for Chinese support
        font_path = Path(__file__).resolve().parent.parent.parent / "fonts" / "simhei.ttf"
        if font_path.exists():
            pdf.add_font("SimHei", "", str(font_path), uni=True)
            pdf.set_font("SimHei", size=11)
        else:
            pdf.set_font("Helvetica", size=11)
        for line in content_md.splitlines():
            line = line.strip() or " "
            pdf.multi_cell(0, 7, txt=line)
        pdf.output(str(path))
        return path
    except Exception:
        # Final fallback: write plain text bytes to preserve endpoint availability.
        path.write_bytes(content_md.encode("utf-8", errors="ignore"))
        return path


def export_bibtex(target_dir: Path, filename: str, papers: Iterable[dict]) -> Path:
    from app.services.citation_service import format_bibliography

    class _FakePaper:
        def __init__(self, data: dict) -> None:
            self.id = data.get("id", "")
            self.title = data.get("title") or "Untitled"
            self.authors = data.get("authors") or []
            self.year = data.get("year")
            self.venue = data.get("venue") or ""
            self.doi = data.get("doi") or ""
            self.arxiv_id = data.get("arxiv_id") or ""
            self.source_url = data.get("source_url") or ""
            self.pdf_url = data.get("pdf_url") or ""

    fake_papers = [_FakePaper(p) for p in papers]
    text = format_bibliography(fake_papers, style="bibtex")
    path = target_dir / filename
    path.write_text(text + "\n", encoding="utf-8")
    return path


def export_json(target_dir: Path, filename: str, payload: dict | list) -> Path:
    path = target_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_quality_report(
    target_dir: Path,
    filename: str,
    *,
    draft_version: int,
    review_rounds: list[dict[str, Any]],
    final_metrics: dict[str, Any],
    publication_prepared: bool,
) -> Path:
    """Export a structured quality gate report alongside the draft."""
    report = {
        "report_type": "quality_gate",
        "draft_version": draft_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "publication_prepared": publication_prepared,
        "final_metrics": final_metrics,
        "review_history": review_rounds,
        "thresholds": {
            "evidence_coverage": 0.90,
            "citation_validity": 0.90,
            "logic_score": 0.80,
            "style_score": 0.80,
            "critical_issues": 0,
            "unsupported_claims": 0,
        },
    }
    return export_json(target_dir, filename, report)

