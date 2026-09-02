"""Tests for B3: exports must embed images instead of dumping raw markdown.

Previously ``![alt](/api/projects/...)`` lines were written as plain text into
DOCX/PDF (readers never saw a single figure) and markdown exports kept the
runtime API URL (broken once the backend is offline). Now images resolve back
to ``data/storage`` and are embedded (DOCX/PDF) or copied+rewritten to a
relative path (Markdown).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

import app.database as database
from app.services import export_service


@pytest.fixture()
def storage_layout(tmp_path, monkeypatch):
    """Fake backend/data/storage tree with one real figure file."""
    monkeypatch.setattr(database, "backend_dir", tmp_path)
    images_dir = tmp_path / "data" / "storage" / "p1" / "images"
    images_dir.mkdir(parents=True)
    fig_path = images_dir / "fig1.png"
    chart, ax = plt.subplots(figsize=(2, 1))
    ax.barh(["模型甲"], [86.4])
    chart.savefig(str(fig_path), dpi=60)
    plt.close(chart)
    return {
        "api_url": "/api/projects/p1/images/fig1.png",
        "fs_path": fig_path,
    }


CONTENT = (
    "# 标题\n\n"
    "正文段落。\n\n"
    "![性能对比](/api/projects/p1/images/fig1.png)\n\n"
    "**图1：** 模型甲在 MMLU 上得分 86.4\n\n"
    "## 结论\n\n"
    "结论段落。"
)


def test_export_markdown_copies_images_and_rewrites_paths(tmp_path, storage_layout):
    out = export_service.export_markdown(tmp_path, "draft.md", CONTENT)
    text = out.read_text(encoding="utf-8")
    assert "![性能对比](images/fig1.png)" in text
    assert "/api/projects/" not in text
    assert (tmp_path / "images" / "fig1.png").exists()


def test_export_docx_embeds_image_and_bold_caption(tmp_path, storage_layout):
    docx = pytest.importorskip("docx")
    out = export_service.export_docx(tmp_path, "draft.docx", CONTENT)
    document = docx.Document(str(out))
    assert len(document.inline_shapes) == 1
    # Bold caption renders as a bold run, not literal asterisks.
    bold_texts = [
        run.text for p in document.paragraphs for run in p.runs if run.bold and run.text.strip()
    ]
    assert any("图1：" in t for t in bold_texts)
    assert not any("![" in p.text for p in document.paragraphs)


def test_export_pdf_embeds_image(tmp_path, storage_layout):
    pytest.importorskip("fpdf")
    out = export_service.export_pdf(tmp_path, "draft.pdf", CONTENT)
    data = out.read_bytes()
    assert data[:4] == b"%PDF"
    # Embedded PNG must inflate the PDF well beyond text-only size (~10KB).
    assert len(data) > 15000
    assert "![性能对比]".encode("utf-8") not in data


def test_unresolvable_image_falls_back_to_text(tmp_path, storage_layout, monkeypatch):
    monkeypatch.setattr(export_service, "_resolve_image_file", lambda url: None)
    out = export_service.export_markdown(tmp_path, "draft.md", CONTENT)
    # Original markdown line preserved verbatim when the file is missing.
    assert "![性能对比](/api/projects/p1/images/fig1.png)" in out.read_text(encoding="utf-8")
