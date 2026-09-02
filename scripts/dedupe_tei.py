"""一次性清理: 去除 TEI 占位文件中因 save_tei bug 造成的全文重复。

背景: LocalStorage.save_tei 旧实现把【整篇转义后的全文】当作循环体写入
（`f"<p>{escaped}</p>" for line in text.splitlines()`，误用 escaped 而非 line），
导致论文有 N 个非空行、TEI 文件就重复 N 份全文，storage 目录膨胀到 18+ GB。
bug 已在 storage.py 修复；本脚本负责清洗存量文件：

  1. 读 backend/data/storage/*/tei/*.tei.xml
  2. 校验文件体确为 `<p>全文</p>` 的完整重复（校验不过则跳过，绝不碰未知格式）
  3. 取第一份拷贝、反转义得到原始全文
  4. 按修复后的格式（每非空行一个 <p>）原子重写

用法:
  python scripts/dedupe_tei.py            # 实际执行
  python scripts/dedupe_tei.py --dry-run  # 只统计，不写入
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PREFIX = "<TEI><text><body>"
SUFFIX = "</body></text></TEI>"

# 反转义顺序: &amp; 必须最后，否则 &lt; 会被二次解出 & 字符
_UNESCAPE_ORDER = [("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")]


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _unescape(value: str) -> str:
    for old, new in _UNESCAPE_ORDER:
        value = value.replace(old, new)
    return value


def rebuild_tei(text: str) -> str:
    """按修复后的 save_tei 格式重建 XML（每非空行一个 <p>）。"""
    return (
        PREFIX
        + "".join(f"<p>{_escape(line)}</p>" for line in text.splitlines() if line.strip())
        + SUFFIX
    )


def analyze(path: Path) -> tuple[str, int, int, int, str | None]:
    """分析单个 TEI 文件，返回 (status, 原字节数, 新字节数, 重复份数, 重建内容)。

    status: duplicated(待清理) / clean(单份或空) / skipped(非预期格式) / error。
    仅 status=duplicated 时第 5 项为重建后的完整 XML 文本。
    """
    raw = path.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "error", len(raw), len(raw), 0, None

    if not content.startswith(PREFIX) or not content.endswith(SUFFIX):
        return "skipped", len(raw), len(raw), 0, None

    body = content[len(PREFIX):-len(SUFFIX)]
    if not body:
        return "clean", len(raw), len(raw), 0, None

    # 提取第一份拷贝: 转义后的全文不含裸 '<'，首个 '</p>' 即第一份拷贝的结束
    end = body.find("</p>")
    if not body.startswith("<p>") or end < 0:
        return "skipped", len(raw), len(raw), 0, None

    one_copy = body[:end + 4]  # "<p>...</p>"
    # 强校验: 文件体必须恰好是同一份拷贝的整数次重复（旧 bug 的精确指纹）
    if not one_copy or len(body) % len(one_copy) != 0:
        return "skipped", len(raw), len(raw), 0, None
    copies = len(body) // len(one_copy)
    if body != one_copy * copies:
        return "skipped", len(raw), len(raw), 0, None

    if copies == 1:
        return "clean", len(raw), len(raw), 1, None

    new_content = rebuild_tei(_unescape(one_copy[3:-4]))
    return "duplicated", len(raw), len(new_content.encode("utf-8")), copies, new_content


def main() -> int:
    parser = argparse.ArgumentParser(description="去重 TEI 占位文件中重复写入的全文")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "backend" / "data" / "storage",
        help="storage 根目录（默认 backend/data/storage）",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"storage root not found: {args.root}")
        return 1

    stats = {"duplicated": 0, "clean": 0, "skipped": 0, "error": 0}
    before_total = after_total = 0
    max_copies = 0

    for path in sorted(args.root.glob("*/tei/*.tei.xml")):
        status, before, after, copies, new_content = analyze(path)
        stats[status] += 1
        before_total += before
        after_total += after
        max_copies = max(max_copies, copies)

        if status == "error":
            print(f"[error] 无法解码: {path}")
        elif status == "duplicated" and not args.dry_run:
            assert new_content is not None
            tmp = path.with_suffix(".tei.xml.tmp")
            tmp.write_text(new_content, encoding="utf-8")
            os.replace(tmp, path)  # 原子替换，避免中途失败留下半截文件

    mode = "DRY-RUN" if args.dry_run else "DONE"
    freed = before_total - after_total
    print(f"[{mode}] files: duplicated={stats['duplicated']} clean={stats['clean']} "
          f"skipped={stats['skipped']} error={stats['error']}")
    print(f"[{mode}] size: {before_total/1e9:.2f} GB -> {after_total/1e9:.2f} GB "
          f"(可释放 {freed/1e9:.2f} GB)")
    print(f"[{mode}] 单文件最大重复份数: {max_copies}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
