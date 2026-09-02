"""Extract figures and tables from academic PDFs using PyMuPDF.

This service pulls embedded raster images and renders vector drawings/tables
as high-resolution PNGs, making real paper figures available for article
illustrations instead of relying solely on AI-generated decorative images.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _to_api_path(filepath: Path, project_id: str) -> str:
    """Convert an absolute filesystem path to a frontend-compatible API path.

    ``data/storage/{project_id}/images/foo.png``
    → ``/api/projects/{project_id}/images/foo.png``
    """
    marker = f"images{os.sep}"
    full = str(filepath)
    idx = full.find(marker)
    if idx != -1:
        relative = full[idx:]  # images/foo/bar.png
        return f"/api/projects/{project_id}/{relative.replace(os.sep, '/')}"
    # Fallback: just use the filename
    return f"/api/projects/{project_id}/images/{filepath.name}"

# Allow processing of large images from academic papers (some full-page
# figures exceed the default 89 Mpixel Pillow limit).
try:
    from PIL import Image as _PILImage

    _PILImage.MAX_IMAGE_PIXELS = 200_000_000
except Exception:
    pass

# Minimum thresholds to skip tiny logos / icons / decorative elements.
_MIN_IMAGE_BYTES = 5_000
_MIN_DIMENSION_PX = 80


def _png_from_bytes(data: bytes) -> bytes | None:
    """Convert raw image bytes to PNG via Pillow.  Returns *None* on failure."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode in ("CMYK", "P", "LA", "PA"):
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _save_png(png_data: bytes, dest: Path) -> dict[str, int]:
    """Write PNG bytes and return (width, height)."""
    from PIL import Image

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png_data)
    img = Image.open(io.BytesIO(png_data))
    return {"width": img.width, "height": img.height}


def extract_figures_from_pdf(
    pdf_path: Path,
    output_dir: Path,
    paper_id: str,
    *,
    project_id: str = "",
    min_dimension: int = _MIN_DIMENSION_PX,
    min_bytes: int = _MIN_IMAGE_BYTES,
) -> list[dict[str, Any]]:
    """Extract figures and tables from a PDF file.

    Strategy
    --------
    1.  ``page.get_images()`` + ``doc.extract_image()`` for embedded raster
        images (PNG / JPEG / etc.).
    2.  ``page.get_image_rects()`` + hi-res clip render for vector drawings
        (matplotlib / PGF figures embedded as drawing commands).

    Returns a list of dicts with API-compatible paths when *project_id*
    is provided.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not available; skipping figure extraction")
        return []

    if not pdf_path.exists():
        logger.warning("PDF not found: %s", pdf_path)
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    figures: list[dict[str, Any]] = []
    fig_index = 0

    try:
        with fitz.open(str(pdf_path)) as doc:
            for page_num, page in enumerate(doc, start=1):
                image_list = page.get_images(full=True)
                extracted_xrefs: set[int] = set()

                # ── 1. Embedded raster images ────────────────────────
                for img_info in image_list:
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        if not base_image:
                            continue
                        img_bytes = base_image.get("image", b"")
                        img_ext = base_image.get("ext", "png")
                        w = base_image.get("width", 0)
                        h = base_image.get("height", 0)

                        if len(img_bytes) < min_bytes:
                            continue
                        if w < min_dimension or h < min_dimension:
                            continue

                        if img_ext != "png":
                            png_data = _png_from_bytes(img_bytes)
                            if png_data is None:
                                continue
                        else:
                            png_data = img_bytes

                        fig_index += 1
                        dest = output_dir / f"fig_p{page_num}_{fig_index}.png"
                        dims = _save_png(png_data, dest)
                        extracted_xrefs.add(xref)

                        figures.append({
                            "path": _to_api_path(dest, project_id) if project_id else str(dest.resolve()),
                            "page": page_num,
                            "width": dims["width"],
                            "height": dims["height"],
                            "source": "embedded",
                        })
                    except Exception:
                        logger.debug(
                            "Extract failed xref=%d page=%d, will try render",
                            xref, page_num, exc_info=True,
                        )

                # ── 2. Fallback: render unextracted images at 2x ─────
                for img_info in image_list:
                    xref = img_info[0]
                    if xref in extracted_xrefs:
                        continue  # already extracted above
                    try:
                        rects = page.get_image_rects(xref)
                        if not rects:
                            continue
                        rect = rects[0]
                        if rect.width < min_dimension or rect.height < min_dimension:
                            continue

                        mat = fitz.Matrix(2.0, 2.0)  # 2x zoom ≈ 144 DPI
                        clip_pix = page.get_pixmap(matrix=mat, clip=rect)
                        pix_bytes = clip_pix.tobytes("png")

                        if len(pix_bytes) < min_bytes:
                            continue

                        fig_index += 1
                        dest = output_dir / f"fig_p{page_num}_{fig_index}.png"
                        dims = _save_png(pix_bytes, dest)

                        figures.append({
                            "path": _to_api_path(dest, project_id) if project_id else str(dest.resolve()),
                            "page": page_num,
                            "width": dims["width"],
                            "height": dims["height"],
                            "source": "rendered",
                        })
                    except Exception:
                        continue

    except Exception:
        logger.warning("Figure extraction failed for %s", pdf_path, exc_info=True)

    logger.info("Extracted %d figures from %s", len(figures), pdf_path.name)
    return figures


def collect_extracted_figures(
    papers: list[Any],
) -> list[dict[str, Any]]:
    """Gather all extracted-figure metadata from a list of Paper objects.

    Reads ``paper.metadata_json["extracted_figures"]`` for each paper and
    tags every figure dict with ``paper_id`` and ``paper_title``.
    """
    all_figures: list[dict[str, Any]] = []
    for paper in papers:
        meta = getattr(paper, "metadata_json", None) or {}
        figures = meta.get("extracted_figures", [])
        for fig in figures:
            fig_with_meta = {
                **fig,
                "paper_id": paper.id,
                "paper_title": paper.title,
            }
            all_figures.append(fig_with_meta)
    return all_figures


def render_key_pages(
    pdf_path: Path,
    output_dir: Path,
    paper_id: str,
    *,
    project_id: str = "",
    pages: list[int] | None = None,
    dpi: float = 150.0,
) -> list[dict[str, Any]]:
    """Render specific PDF pages as full-page PNG screenshots.

    Captures content that embedded-image extraction cannot reach:
    title pages, text-based tables, and vector overview diagrams.

    Parameters
    ----------
    pages : list[int] | None
        1-indexed page numbers.  *None* = first page only.
    dpi : float
        Render resolution (default 150).
    """
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF not available; skipping page rendering")
        return []

    if not pdf_path.exists():
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []

    if pages is None:
        pages = [1]

    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    try:
        with fitz.open(str(pdf_path)) as doc:
            total_pages = len(doc)
            for page_num in pages:
                if page_num < 1 or page_num > total_pages:
                    continue
                page = doc[page_num - 1]
                pix = page.get_pixmap(matrix=mat)
                png_bytes = pix.tobytes("png")

                dest = output_dir / f"page_{page_num}.png"
                dims = _save_png(png_bytes, dest)

                rendered.append({
                    "path": _to_api_path(dest, project_id) if project_id else str(dest.resolve()),
                    "page": page_num,
                    "width": dims["width"],
                    "height": dims["height"],
                    "source": "page_render",
                })
    except Exception:
        logger.warning("Page rendering failed for %s", pdf_path, exc_info=True)

    return rendered


def _detect_table_pages(pdf_path: Path) -> list[int]:
    """Heuristic: find pages likely containing result tables.

    Looks for high density of numeric tokens + structured line patterns.
    Returns 1-indexed page numbers (max 3).
    """
    try:
        import fitz
        import re as _re
    except ImportError:
        return []

    table_pages: list[int] = []
    try:
        with fitz.open(str(pdf_path)) as doc:
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")
                if not text:
                    continue

                lines = [l.strip() for l in text.splitlines() if l.strip()]
                if len(lines) < 8:
                    continue

                numeric_lines = sum(
                    1 for l in lines if len(_re.findall(r"\d+\.?\d*", l)) >= 2
                )
                structured_lines = sum(
                    1 for l in lines if "|" in l or "\t" in l
                )

                total = len(lines)
                nr = numeric_lines / total
                sr = structured_lines / total

                if nr > 0.25 or (sr > 0.4 and nr > 0.15):
                    table_pages.append(page_num)
    except Exception:
        pass

    return table_pages[:3]


# F5: semantic categories for extracted figures (LLM-tagged).
# ``decorative`` marks logos / branding banners / page ornaments that slipped
# past the size filters -- they are excluded from selection entirely.
FIGURE_CATEGORIES = ("architecture", "result_table", "experiment_curve", "framework_overview", "decorative", "other")
_CATEGORY_SECTION = {
    "result_table": "Results",
    "experiment_curve": "Results",
    "architecture": "Framework",
    "framework_overview": "Framework",
}
_CATEGORY_BONUS = {
    "architecture": 2.0,
    "framework_overview": 2.0,
    "result_table": 2.5,
    "experiment_curve": 1.5,
    "other": 0.0,
}


def _heuristic_category(fig: dict[str, Any]) -> str:
    """Fallback: classify by aspect ratio + page position when LLM tagging fails."""
    w = fig.get("width", 0)
    h = fig.get("height", 0)
    aspect = w / max(h, 1) if h else 0.0
    page = fig.get("page", 99)
    # Ultra-wide and very short banner shapes - especially near the top of
    # early pages - are logos / branding strips / journal banners. Without
    # this guard they classify as result_table and pollute the article
    # whenever the vision model is unavailable.
    if aspect >= 3.5 and h > 0 and h <= 120:
        return "decorative"
    if aspect > 1.8:
        return "result_table"
    if 0.8 < aspect <= 1.8:
        return "experiment_curve" if page > 4 else "architecture"
    if page <= 3:
        return "framework_overview"
    return "other"


# ---------------------------------------------------------------------------
# Vision-based tagging (F5b): show the figure to a multimodal LLM
# ---------------------------------------------------------------------------

# Max images per vision call — keeps payloads reasonable for providers with
# body-size limits while still amortizing one call over several figures.
_VISION_BATCH_SIZE = 6
# Downscale before base64 — full-res paper figures are 1-2 MB each.
_VISION_MAX_DIM = 1024


def _fs_path_for_figure(fig: dict[str, Any]) -> Path | None:
    """Resolve a figure dict's path to an existing file on disk.

    Figures stored via the workflow carry API paths
    (``/api/projects/{pid}/images/figures/x.png``); map them back under
    ``data/storage``. Plain filesystem paths are returned as-is.
    """
    raw = str(fig.get("path") or "")
    if not raw:
        return None
    if raw.startswith("/api/projects/"):
        from app.database import backend_dir

        rel = raw[len("/api/projects/"):]
        candidate = backend_dir / "data" / "storage" / rel
    else:
        candidate = Path(raw)
    return candidate if candidate.exists() else None


def _load_image_for_vision(path: Path) -> dict[str, str] | None:
    """Read + downscale an image for a vision LLM call.

    Returns ``{"data": <base64>, "media_type": "image/jpeg"}`` or ``None``.
    """
    try:
        import base64

        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > _VISION_MAX_DIM:
                scale = _VISION_MAX_DIM / max(im.size)
                im = im.resize(
                    (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        return {
            "data": base64.b64encode(buf.getvalue()).decode("ascii"),
            "media_type": "image/jpeg",
        }
    except Exception:
        logger.debug("Could not prepare figure for vision: %s", path, exc_info=True)
        return None


def _parse_tag_entries(text: str) -> list[dict[str, Any]]:
    """Extract the JSON array from an LLM tagging response."""
    import json as _json
    import re as _re

    text = (text or "").strip()
    if "```" in text:
        fence = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
        if fence:
            text = fence.group(1)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    parsed = _json.loads(text[start : end + 1])
    return parsed if isinstance(parsed, list) else []


def _valid_tag(entry: dict[str, Any]) -> dict[str, str] | None:
    category = str(entry.get("category") or "").strip().lower()
    if category not in FIGURE_CATEGORIES:
        category = "other"
    description = str(entry.get("description") or "")[:80]
    if not description:
        return None
    return {"category": category, "description": description}


def _tag_figures_with_vision(
    batch: list[dict[str, Any]],
    project_title: str,
) -> dict[int, dict[str, str]]:
    """Tag figures by sending the actual images to a multimodal LLM.

    Metadata-only tagging cannot tell a company logo from an architecture
    diagram -- both are wide images on early pages. Showing the model the
    pixels is the only reliable signal. Returns ``{batch_index: tag}``;
    empty dict when vision is unavailable (caller falls back to metadata).
    """
    from app.services.llm_service import active_model_supports_vision, chat_completion

    try:
        if not active_model_supports_vision():
            logger.warning(
                "Vision model unavailable; %d figures fall back to metadata-only tagging",
                len(batch),
            )
            return {}
    except Exception:
        logger.warning(
            "Vision support check failed; %d figures fall back to metadata-only tagging",
            len(batch),
            exc_info=True,
        )
        return {}

    prepared: list[tuple[int, dict[str, str]]] = []
    for i, fig in enumerate(batch):
        fs_path = _fs_path_for_figure(fig)
        if fs_path is None:
            continue
        image = _load_image_for_vision(fs_path)
        if image:
            prepared.append((i, image))
    if not prepared:
        return {}

    system_prompt = (
        "你是一位学术论文配图审核专家。我会给你若干从论文 PDF 中抽取的图片，"
        "请逐张判断语义类型：\n"
        "architecture=方法/系统架构图；result_table=结果表格或宽幅对比图；"
        "experiment_curve=实验曲线/性能图表；framework_overview=整体框架概览图；\n"
        "decorative=装饰性图片：公司/产品 logo、品牌横幅、页眉页脚装饰、二维码、"
        "纯图标、与学术内容无关的插画；\n"
        "other=其他（照片、无法归类的示意图）。\n"
        "并给出一句话中文描述（30字内），说明图中实际画了什么。"
        "宁可保守：拿不准是否装饰图时归 other，不要归 decorative。"
    )

    tags: dict[int, dict[str, str]] = {}
    for start in range(0, len(prepared), _VISION_BATCH_SIZE):
        chunk = prepared[start : start + _VISION_BATCH_SIZE]
        lines = []
        for local_idx, (orig_idx, _img) in enumerate(chunk):
            fig = batch[orig_idx]
            lines.append(
                f"[{local_idx}] page={fig.get('page', '?')} "
                f"paper={str(fig.get('paper_title', ''))[:60]}"
            )
        user_prompt = (
            f"论文主题：{project_title}\n\n"
            f"图片按顺序附上，元数据：\n" + "\n".join(lines) + "\n\n"
            '输出 JSON 数组：[{"index": 0, "category": "architecture", "description": "..."}]\n'
            "index 与图片顺序对应。只输出 JSON 数组。"
        )
        try:
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                timeout=90.0,
                images=[img for _i, img in chunk],
            )
            if result.get("error"):
                logger.warning("Vision tagging call failed: %s", result["error"])
                continue
            for entry in _parse_tag_entries(result.get("content", "")):
                local_idx = entry.get("index")
                if not isinstance(local_idx, int) or not 0 <= local_idx < len(chunk):
                    continue
                tag = _valid_tag(entry)
                if tag:
                    tags[chunk[local_idx][0]] = tag
        except Exception:
            logger.warning("Vision tagging batch failed; continuing", exc_info=True)
    return tags


def tag_figures_with_categories(
    figures: list[dict[str, Any]],
    project_title: str,
    max_figures: int = 12,
) -> list[dict[str, Any]]:
    """F5: tag extracted figures with content category + description.

    Tagging cascades through three signals, most reliable first:

    1. **Vision** — the figure images themselves are sent to a multimodal LLM
       (``_tag_figures_with_vision``). This is the only stage that can spot
       logos / branding banners (``decorative``), which metadata cannot
       distinguish from real figures.
    2. **Metadata LLM** — page / aspect / paper-title classification for any
       figure vision missed (image file unreadable, provider not multimodal).
    3. **Heuristics** — aspect-ratio + page-position rules when no LLM is
       available at all, so figure selection never blocks.

    Adds ``category`` (see ``FIGURE_CATEGORIES``) and ``description`` (one
    line) to each figure dict (in place) and returns the list.
    """
    if not figures:
        return figures
    batch = figures[:max_figures]

    # ── 1. Vision tagging ──────────────────────────────────────────
    tags: dict[int, dict[str, Any]] = {}
    try:
        tags = _tag_figures_with_vision(batch, project_title)
    except Exception:
        logger.warning("Vision tagging failed; falling back to metadata", exc_info=True)

    # ── 2. Metadata-only LLM tagging for figures vision missed ─────
    missing = [i for i in range(len(batch)) if i not in tags]
    if missing:
        from app.services.llm_service import chat_completion

        lines = []
        for i in missing:
            fig = batch[i]
            w = fig.get("width", 0)
            h = fig.get("height", 0)
            aspect = w / max(h, 1) if h else 0.0
            lines.append(
                f"[{i}] page={fig.get('page', '?')} aspect={aspect:.2f} "
                f"source={fig.get('source', 'embedded')} "
                f"paper={str(fig.get('paper_title', ''))[:60]}"
            )
        system_prompt = (
            "你是一位学术论文配图分类专家。请根据论文标题、图片在文中的页码和宽高比，"
            "判断每张图的语义类型：\n"
            "architecture=方法/系统架构图；result_table=结果表格或宽幅对比图；"
            "experiment_curve=实验曲线/性能图表；framework_overview=整体框架概览图；"
            "decorative=装饰性图片（出版社logo、品牌横幅、二维码、页眉页脚装饰，无学术信息量）；"
            "other=其他（示意图、照片等）。\n"
            "判定 decorative 的典型特征：出现在前2页顶部、超宽且极矮（宽高比>3.5、高度很小）、"
            "或元数据明显是刊头/机构标识。\n"
            "并给出一句话中文描述（30字内），说明图的内容主题。"
        )
        user_prompt = (
            f"论文主题：{project_title}\n\n"
            f"图片元数据：\n" + "\n".join(lines) + "\n\n"
            '输出 JSON 数组：[{"index": 0, "category": "architecture", "description": "..."}]\n'
            "只输出 JSON 数组。"
        )

        try:
            result = chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1024,
                timeout=45.0,
            )
            for entry in _parse_tag_entries(result.get("content", "")):
                idx = entry.get("index")
                if isinstance(idx, int) and 0 <= idx < len(batch) and idx not in tags:
                    tag = _valid_tag(entry) or {
                        "category": (
                            str(entry.get("category") or "").strip().lower()
                            if str(entry.get("category") or "").strip().lower() in FIGURE_CATEGORIES
                            else "other"
                        ),
                        "description": "",
                    }
                    tags[idx] = tag
        except Exception:
            logger.warning("Figure category tagging failed; using heuristics", exc_info=True)

    for i, fig in enumerate(batch):
        if i in tags:
            fig["category"] = tags[i]["category"]
            if tags[i].get("description"):
                fig["description"] = tags[i]["description"]
        else:
            fig["category"] = _heuristic_category(fig)
            fig.setdefault("description", "")
    return figures


def select_best_figures(
    all_figures: list[dict[str, Any]],
    *,
    max_count: int = 8,
) -> list[dict[str, Any]]:
    """Pick the most valuable figures from a pool for article illustration.

    Strategy (inspired by high-quality WeChat academic articles):
    - **Embedded figures** (real paper charts/diagrams) are preferred over
      page renders (full-page screenshots).
    - **Paper diversity**: max 2 figures per paper to cover multiple sources.
    - **Type variety**: wide figures → result tables, early pages → architecture
      diagrams, late pages → experimental benchmarks.
    - **Page renders** are supplementary (max 2) — used only when embedded
      figures are insufficient or for text-heavy tables.

    Returns up to *max_count* figures sorted by score (best first).
    """
    if not all_figures:
        return []

    # Logos / branding banners / ornaments are never article illustrations,
    # no matter how well they score on size and position.
    all_figures = [f for f in all_figures if f.get("category") != "decorative"]
    if not all_figures:
        return []

    import math

    # ── Separate embedded figures from page renders ──
    embedded: list[dict[str, Any]] = []
    page_renders: list[dict[str, Any]] = []
    for fig in all_figures:
        if fig.get("source") == "page_render":
            page_renders.append(fig)
        else:
            embedded.append(fig)

    # ── Score embedded figures ──
    def _score_figure(fig: dict[str, Any]) -> float:
        w = fig.get("width", 0)
        h = fig.get("height", 0)
        area = w * h
        page = fig.get("page", 99)

        # Area score (log-scaled to avoid extreme dominance)
        score = math.log2(max(area, 1))

        # Page position bonus
        if page <= 3:
            score += 3.0       # architecture / framework diagrams
        elif page <= 6:
            score += 1.5       # method illustrations
        # Late pages (results/benchmarks) get no page bonus — area
        # naturally captures large result tables.

        # Aspect ratio: wide figures = result tables/comparisons (valuable)
        aspect = w / max(h, 1)
        if 1.3 < aspect < 4.0:
            score += 2.5
        elif 0.8 < aspect < 1.3:
            score += 0.5       # roughly square — typical charts
        if aspect < 0.4:
            score -= 3.0       # very tall/narrow — likely not useful

        # F5: semantic category weight (tagged by LLM in tag_figures_with_categories).
        score += _CATEGORY_BONUS.get(fig.get("category", ""), 0.0)

        return score

    scored_embedded = [(_score_figure(fig), fig) for fig in embedded]
    scored_embedded.sort(key=lambda x: x[0], reverse=True)

    scored_renders = [(_score_figure(fig), fig) for fig in page_renders]
    scored_renders.sort(key=lambda x: x[0], reverse=True)

    # ── Select embedded figures with paper diversity ──
    selected: list[dict[str, Any]] = []
    paper_counts: dict[str, int] = {}
    seen_keys: set[tuple[int, int, int]] = set()

    for _score, fig in scored_embedded:
        if len(selected) >= max_count:
            break

        # Per-paper limit: max 2 figures from the same paper
        paper_id = str(fig.get("paper_id", "unknown"))
        if paper_counts.get(paper_id, 0) >= 2:
            continue

        # Dedup by (page, size bucket)
        page = fig.get("page", 0)
        wb = fig.get("width", 0) // 200
        hb = fig.get("height", 0) // 200
        key = (page, wb, hb)
        if key in seen_keys:
            continue

        seen_keys.add(key)
        selected.append(fig)
        paper_counts[paper_id] = paper_counts.get(paper_id, 0) + 1

    # ── Add page renders as supplements (max 2, only if space remains) ──
    max_renders = min(2, max_count - len(selected))
    render_count = 0
    for _score, fig in scored_renders:
        if render_count >= max_renders:
            break
        # Also enforce per-paper limit for page renders
        paper_id = str(fig.get("paper_id", "unknown"))
        if paper_counts.get(paper_id, 0) >= 2:
            continue
        page = fig.get("page", 0)
        wb = fig.get("width", 0) // 200
        hb = fig.get("height", 0) // 200
        key = (page, wb, hb)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(fig)
        paper_counts[paper_id] = paper_counts.get(paper_id, 0) + 1
        render_count += 1

    return selected
