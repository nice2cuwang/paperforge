"""Generate data-driven charts from evidence card data using matplotlib.

Instead of decorative AI-generated illustrations, this service creates
real, data-backed visualisations: benchmark comparison bars, experimental
result tables, and performance metrics — the kind of charts seen in
high-quality WeChat academic articles.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _to_api_path(filepath: Path, project_id: str) -> str:
    """Convert an absolute filesystem path to a frontend-compatible API path."""
    marker = f"images{os.sep}"
    full = str(filepath)
    idx = full.find(marker)
    if idx != -1:
        relative = full[idx:]
        return f"/api/projects/{project_id}/{relative.replace(os.sep, '/')}"
    return f"/api/projects/{project_id}/images/{filepath.name}"

# Consistent professional colour palette.
_COLORS = [
    "#4C72B0", "#DD8452", "#55A868",
    "#C44E52", "#8172B3", "#937860",
    "#DA8BC3", "#8C8C8C", "#CCB974", "#64B5CD",
]


def _ensure_matplotlib():
    """Import matplotlib with the non-interactive backend; raise if missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: F401

        return True
    except ImportError:
        logger.warning(
            "matplotlib not installed — chart rendering skipped. "
            "Install with: pip install matplotlib"
        )
        return False


# ── Chart renderers ─────────────────────────────────────────────────────


def render_benchmark_comparison(
    data: list[dict[str, Any]],
    output_path: Path,
    title: str = "Benchmark Comparison",
    *,
    project_id: str = "",
) -> str | None:
    """Horizontal bar chart comparing models across benchmarks.

    *data* format::

        [{"model": "GPT-4", "benchmark": "MMLU", "score": 86.4}, ...]
    """
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt

    if not data:
        return None

    # Deduplicate: keep highest score per (model, benchmark) pair.
    seen: dict[tuple[str, str], float] = {}
    for item in data:
        key = (item["model"], item["benchmark"])
        score = float(item["score"])
        if key not in seen or score > seen[key]:
            seen[key] = score
    data = [
        {"model": k[0], "benchmark": k[1], "score": v}
        for k, v in seen.items()
    ]

    benchmarks = sorted(set(d["benchmark"] for d in data))
    models = sorted(set(d["model"] for d in data))

    fig, ax = plt.subplots(figsize=(10, max(3.5, len(models) * 0.6 + 1.2)))

    bar_height = 0.8 / max(len(benchmarks), 1)
    for i, bm in enumerate(benchmarks):
        scores = []
        for model in models:
            match = [d for d in data if d["model"] == model and d["benchmark"] == bm]
            scores.append(match[0]["score"] if match else 0)
        offsets = [
            (i - len(benchmarks) / 2 + 0.5) * bar_height for _ in models
        ]
        ax.barh(
            [m + offsets[m_idx] for m_idx, m in enumerate(range(len(models)))],
            scores,
            bar_height * 0.9,
            label=bm,
            color=_COLORS[i % len(_COLORS)],
        )

    ax.set_yticks(range(len(models)))
    ax.set_yticklabels(models, fontsize=10)
    ax.set_xlabel("Score", fontsize=10)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=12)
    if len(benchmarks) > 1:
        ax.legend(fontsize=8, loc="lower right")
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _to_api_path(output_path, project_id) if project_id else str(output_path.resolve())


def render_results_table(
    data: list[dict[str, Any]],
    output_path: Path,
    title: str = "Experimental Results",
    *,
    project_id: str = "",
) -> str | None:
    """Render a colour-coded results table image.

    *data* format::

        [{"model": "GPT-4", "benchmark": "MMLU", "score": 86.4}, ...]
    """
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt
    import numpy as np

    if not data:
        return None

    benchmarks = sorted(set(d["benchmark"] for d in data))
    models = sorted(set(d["model"] for d in data))

    matrix: list[list[float]] = []
    for model in models:
        row: list[float] = []
        for bm in benchmarks:
            match = [d for d in data if d["model"] == model and d["benchmark"] == bm]
            row.append(match[0]["score"] if match else float("nan"))
        matrix.append(row)

    arr = np.array(matrix)

    fig, ax = plt.subplots(
        figsize=(max(6, len(benchmarks) * 2.0 + 2), max(3, len(models) * 0.55 + 1.5))
    )
    ax.axis("off")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=14)

    cell_text = [[f"{v:.1f}" if not np.isnan(v) else "—" for v in row] for row in arr]
    table = ax.table(
        cellText=cell_text,
        rowLabels=models,
        colLabels=benchmarks,
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)

    # Iterate over all cells and style them by position.
    for (row_idx, col_idx), cell in table.get_celld().items():
        if row_idx == 0 and col_idx >= 1:
            # Column header.
            cell.set_facecolor("#4C72B0")
            cell.set_text_props(color="white", fontweight="bold")
        elif col_idx == 0 and row_idx >= 1:
            # Row label.
            cell.set_facecolor("#E8ECF1")
            cell.set_text_props(fontweight="bold")
        elif row_idx >= 1 and col_idx >= 1:
            # Data cell — colour by relative rank within the column.
            data_row = row_idx - 1
            data_col = col_idx - 1
            if data_row < len(models) and data_col < len(benchmarks):
                val = arr[data_row, data_col]
                if np.isnan(val):
                    cell.set_facecolor("#F5F5F5")
                else:
                    col_vals = arr[:, data_col]
                    col_max = np.nanmax(col_vals)
                    col_min = np.nanmin(col_vals)
                    if col_max > col_min:
                        ratio = (val - col_min) / (col_max - col_min)
                    else:
                        ratio = 0.5
                    r = int(255 - ratio * 70)
                    g = int(255 - ratio * 40)
                    b_val = int(255 - ratio * 10)
                    cell.set_facecolor(f"#{r:02x}{g:02x}{b_val:02x}")

    plt.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _to_api_path(output_path, project_id) if project_id else str(output_path.resolve())


def render_metrics_bar(
    metrics: dict[str, float],
    output_path: Path,
    title: str = "Performance Overview",
    *,
    project_id: str = "",
) -> str | None:
    """Vertical bar chart for a dictionary of metric-name → score."""
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt

    if not metrics:
        return None

    names = list(metrics.keys())
    values = list(metrics.values())

    fig, ax = plt.subplots(figsize=(max(6, len(names) * 1.2), 4.5))
    bars = ax.bar(
        names, values,
        color=[_COLORS[i % len(_COLORS)] for i in range(len(names))],
        width=0.65,
    )

    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.5,
            f"{val:.1f}",
            ha="center", va="bottom", fontsize=9,
        )

    ax.set_title(title, fontsize=13, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", rotation=30)
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _to_api_path(output_path, project_id) if project_id else str(output_path.resolve())


# ── SVG fallback (when matplotlib is unavailable) ──────────────────────


def render_metrics_svg(
    metrics: dict[str, float],
    output_path: Path,
    title: str = "Performance Overview",
    *,
    project_id: str = "",
) -> str | None:
    """Simple SVG bar chart fallback when matplotlib is not installed."""
    if not metrics:
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    width = 600
    bar_area = width - 180
    bar_h = 28
    gap = 8
    height = max(120, len(metrics) * (bar_h + gap) + 60)
    max_val = max(metrics.values()) or 1.0

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#FAFAFA"/>',
        f'<text x="20" y="30" font-size="16" font-weight="bold" fill="#333">{title}</text>',
    ]

    y = 50
    for name, val in metrics.items():
        bar_w = int((val / max_val) * (bar_area - 80))
        svg_parts.append(
            f'<text x="140" y="{y + bar_h // 2 + 5}" font-size="11" '
            f'text-anchor="end" fill="#555">{name}</text>'
        )
        svg_parts.append(
            f'<rect x="150" y="{y}" width="{bar_w}" height="{bar_h}" '
            f'fill="#4C72B0" rx="3"/>'
        )
        svg_parts.append(
            f'<text x="{150 + bar_w + 6}" y="{y + bar_h // 2 + 5}" '
            f'font-size="11" fill="#333">{val:.1f}</text>'
        )
        y += bar_h + gap

    svg_parts.append("</svg>")

    svg_path = output_path.with_suffix(".svg")
    svg_path.write_text("\n".join(svg_parts), encoding="utf-8")
    return _to_api_path(svg_path, project_id) if project_id else str(svg_path.resolve())


# ── Orchestration ───────────────────────────────────────────────────────


def _extract_structured_data(
    cards: list[Any],
    project_title: str,
) -> dict[str, Any]:
    """Use LLM to extract structured benchmark data from evidence cards.

    Returns::

        {
            "benchmarks": [
                {"model": "...", "benchmark": "...", "score": 86.4}, ...
            ],
            "paper_titles": ["..."],
            "metrics_summary": {"Accuracy": 92.3, ...},
        }
    """
    from app.services.llm_service import chat_completion

    card_texts: list[str] = []
    for i, card in enumerate(cards):
        claim = getattr(card, "claim", "") or ""
        support = getattr(card, "supporting_text", "") or ""
        if claim or support:
            card_texts.append(f"[{i}] claim: {claim} | data: {support}")

    if not card_texts:
        return {"benchmarks": [], "paper_titles": [], "metrics_summary": {}}

    system_prompt = (
        "You are a data extraction assistant. Extract structured quantitative "
        "benchmark/experimental data from the given evidence cards. Return ONLY "
        "valid JSON, no markdown fences or commentary."
    )
    user_prompt = (
        f"Research topic: {project_title}\n\n"
        f"Evidence cards:\n" + "\n".join(card_texts[:20]) + "\n\n"
        "Extract all benchmark/experimental results. For each result provide:\n"
        '{"benchmarks": [{"model": "model name", "benchmark": "benchmark name", '
        '"score": numeric_value}, ...], '
        '"paper_titles": ["title1", ...], '
        '"metrics_summary": {"metric_name": numeric_value, ...}}\n'
        "If no quantitative data found, return: "
        '{"benchmarks": [], "paper_titles": [], "metrics_summary": {}}'
    )

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=2048,
            timeout=30.0,
        )
        text = result.get("content", "").strip()
        if not text:
            return {"benchmarks": [], "paper_titles": [], "metrics_summary": {}}

        import json as _json
        import re

        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)

        parsed = _json.loads(text)
        return {
            "benchmarks": parsed.get("benchmarks", []),
            "paper_titles": parsed.get("paper_titles", []),
            "metrics_summary": parsed.get("metrics_summary", {}),
        }
    except Exception:
        logger.warning("LLM data extraction failed", exc_info=True)
        return {"benchmarks": [], "paper_titles": [], "metrics_summary": {}}


def generate_charts_from_evidence(
    cards: list[Any],
    project_id: str,
    project_title: str,
    output_dir: Path,
) -> list[dict[str, str]]:
    """Main entry: generate data-driven chart images from evidence cards.

    Returns a list of image metadata dicts compatible with
    ``inject_images_into_markdown``::

        [{"path": "...", "alt": "...", "section": "...", "source": "chart"}]
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    charts: list[dict[str, str]] = []

    structured = _extract_structured_data(cards, project_title)

    benchmarks = structured.get("benchmarks", [])
    # Validate entries.
    benchmarks = [
        b for b in benchmarks
        if isinstance(b, dict)
        and b.get("model")
        and b.get("benchmark")
        and isinstance(b.get("score"), (int, float))
    ]
    # Cap at 12 entries for readable charts.
    benchmarks = benchmarks[:12]

    paper_titles = structured.get("paper_titles", [])[:3]
    metrics_summary = structured.get("metrics_summary", {})

    # ── Chart 1: benchmark comparison bar chart ──────────────────
    if len(benchmarks) >= 3:
        unique_bm = set(b["benchmark"] for b in benchmarks)
        if len(unique_bm) > 1:
            groups: dict[str, list[dict]] = {}
            for b in benchmarks:
                groups.setdefault(b["benchmark"], []).append(b)
            for bm_name, bm_data in list(groups.items())[:2]:
                path = render_benchmark_comparison(
                    bm_data,
                    output_dir / f"chart_{bm_name[:30].replace(' ', '_')}.png",
                    title=f"{bm_name} Comparison",
                    project_id=project_id,
                )
                if path:
                    charts.append({
                        "path": path,
                        "alt": f"{bm_name} benchmark comparison chart",
                        "section": "实验结果" if any(
                            kw in project_title for kw in ("实验", "评测", "基准", "benchmark")
                        ) else "Results",
                        "source": "chart",
                    })
        else:
            path = render_benchmark_comparison(
                benchmarks,
                output_dir / "chart_benchmark_comparison.png",
                title="Benchmark Comparison",
                project_id=project_id,
            )
            if path:
                charts.append({
                    "path": path,
                    "alt": "Benchmark comparison chart",
                    "section": "实验结果",
                    "source": "chart",
                })

    # ── Chart 2: results table image ────────────────────────────
    if len(benchmarks) >= 2:
        path = render_results_table(
            benchmarks,
            output_dir / "chart_results_table.png",
            title="Experimental Results Summary",
            project_id=project_id,
        )
        if path:
            charts.append({
                "path": path,
                "alt": "Experimental results table",
                "section": "实验结果",
                "source": "chart",
            })

    # ── Chart 3: metrics overview bar chart ─────────────────────
    valid_metrics = {
        str(k): float(v)
        for k, v in metrics_summary.items()
        if isinstance(v, (int, float))
    }
    if len(valid_metrics) >= 2:
        path = render_metrics_bar(
            valid_metrics,
            output_dir / "chart_metrics_overview.png",
            title="Performance Metrics",
            project_id=project_id,
        )
        if path:
            charts.append({
                "path": path,
                "alt": "Performance metrics overview",
                "section": "Results",
                "source": "chart",
            })
        else:
            # matplotlib missing → SVG fallback
            svg_path = render_metrics_svg(
                valid_metrics,
                output_dir / "chart_metrics_overview.png",
                title="Performance Metrics",
                project_id=project_id,
            )
            if svg_path:
                charts.append({
                    "path": svg_path,
                    "alt": "Performance metrics overview (SVG)",
                    "section": "Results",
                    "source": "chart",
                })

    logger.info("Generated %d charts for project %s", len(charts), project_id)
    return charts
