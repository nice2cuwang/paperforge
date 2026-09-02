"""Tests for LocalStorage.save_tei placeholder format.

背景: save_tei 曾把【整篇转义后的全文】当作循环体写入 —— 论文有 N 个非空行，
TEI 文件就重复 N 份全文，导致 storage 目录膨胀到 18+ GB（见 scripts/dedupe_tei.py）。
此处回归验证: 每行一个 <p>、特殊字符逐行转义、无全文重复。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from app.services.storage import LocalStorage


def _save(tmp_path: Path, text: str) -> str:
    storage = LocalStorage(base_dir=tmp_path)
    return storage.save_tei("proj-1", "paper-1", text)


def test_save_tei_one_paragraph_per_line_no_fulltext_duplication(tmp_path):
    text = "\n".join(f"line {i}" for i in range(1, 51))  # 50 行
    path = _save(tmp_path, text)

    content = Path(path).read_text(encoding="utf-8")
    assert content.count("<p>") == 50
    # 旧 bug 会把全文写 50 遍；修复后文件大小应与原文同量级
    assert len(content) < len(text) * 2
    assert content.count("line 1\n") == 0  # 换行不再保留在 <p> 内部


def test_save_tei_escapes_xml_special_chars_per_line(tmp_path):
    text = 'a < b & c > "d" \'e\''
    path = _save(tmp_path, text)

    content = Path(path).read_text(encoding="utf-8")
    root = ET.fromstring(content)  # 必须是合法 XML
    paragraphs = [p.text for p in root.iter("p")]
    assert paragraphs == [text]


def test_save_tei_skips_blank_lines_and_handles_empty(tmp_path):
    path = _save(tmp_path, "keep\n\n  \nalso keep\n")
    content = Path(path).read_text(encoding="utf-8")
    assert content.count("<p>") == 2

    empty_path = _save(tmp_path, "")
    assert Path(empty_path).read_text(encoding="utf-8") == "<TEI><text><body></body></text></TEI>"
