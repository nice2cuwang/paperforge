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
