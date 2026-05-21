from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def ensure_export_dir(base_dir: Path, project_id: str) -> Path:
    target = base_dir / "exports" / project_id
    target.mkdir(parents=True, exist_ok=True)
    return target


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
    entries: list[str] = []
    for idx, paper in enumerate(papers, start=1):
        authors = paper.get("authors") or []
        author_text = " and ".join(str(item) for item in authors if item)
        key_source = paper.get("doi") or paper.get("arxiv_id") or f"paper{idx}"
        key = str(key_source).replace("/", "_").replace(":", "_")
        entries.append(
            "\n".join(
                [
                    f"@article{{{key},",
                    f"  title = {{{paper.get('title') or 'Untitled'}}},",
                    f"  author = {{{author_text or 'Unknown'}}},",
                    f"  year = {{{paper.get('year') or ''}}},",
                    f"  journal = {{{paper.get('venue') or ''}}},",
                    f"  doi = {{{paper.get('doi') or ''}}},",
                    "}",
                ]
            )
        )
    path = target_dir / filename
    path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")
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

