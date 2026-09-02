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
import re
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


_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"
_font_registered = False


def _register_chinese_font() -> None:
    """Register a CJK-capable font so Chinese labels render as glyphs, not boxes.

    matplotlib has no built-in CJK font; without this, Chinese model/benchmark
    names on every axis render as tofu squares. Reuses the same bundled
    simhei.ttf as the PDF exporter and falls back gracefully when absent.
    """
    global _font_registered
    if _font_registered:
        return
    _font_registered = True
    try:
        import matplotlib.font_manager as fm
        import matplotlib.pyplot as plt

        families: list[str] = []
        for name in ("simhei.ttf", "msyh.ttc", "simsun.ttc"):
            font_path = _FONT_DIR / name
            if font_path.exists():
                fm.fontManager.addfont(str(font_path))
                families.append(fm.FontProperties(fname=str(font_path)).get_name())
        if not families:
            # No bundled font: try common system CJK families by name.
            families = ["SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei"]
        plt.rcParams["font.sans-serif"] = families + [
            f for f in plt.rcParams.get("font.sans-serif", []) if isinstance(f, str)
        ]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as exc:
        logger.warning("CJK font registration failed (Chinese labels may render as boxes): %s", exc)


def _ensure_matplotlib():
    """Import matplotlib with the non-interactive backend; raise if missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: F401

        _register_chinese_font()
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
            # Missing (model, benchmark) combos stay NaN: a NaN bar is simply
            # not drawn. Zero-filling would falsely show "this model scored 0".
            scores.append(float(match[0]["score"]) if match else float("nan"))
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


# F4: shape-detection helpers for chart-type selection.
def _looks_like_time_series(benchmarks: list[dict[str, Any]]) -> bool:
    """>=3 benchmarks whose names are years/periods (e.g. '2021' or '2021 版')."""
    names = [str(b.get("benchmark", "")) for b in benchmarks]
    time_points = [n for n in names if re.match(r"^\d{4}", n) or "年" in n]
    return len(time_points) >= 3


def _scatter_pair(benchmarks: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Exactly two models sharing >=4 benchmarks -> correlation scatter."""
    models = sorted({b.get("model", "") for b in benchmarks if b.get("model")})
    if len(models) != 2:
        return None
    shared = {
        b.get("benchmark")
        for b in benchmarks
        if b.get("benchmark") and b.get("model") in models
    }
    return (models[0], models[1]) if len(shared) >= 4 else None


def _radar_model(benchmarks: list[dict[str, Any]]) -> str | None:
    """A model with >=4 distinct benchmarks -> multi-dim radar shape."""
    by_model: dict[str, set[str]] = {}
    for b in benchmarks:
        model = b.get("model", "")
        bm = b.get("benchmark", "")
        if model and bm:
            by_model.setdefault(model, set()).add(bm)
    for model, dims in by_model.items():
        if len(dims) >= 4:
            return model
    return None


def render_time_series(
    data: list[dict[str, Any]],
    output_path: Path,
    title: str = "Performance Trend",
    *,
    project_id: str = "",
) -> str | None:
    """Line chart: scores across time points (F4: 时序 -> 折线)."""
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt

    def _sort_key(name: str) -> tuple[int, str]:
        m = re.match(r"^(\d{4})", name)
        return (int(m.group(1)) if m else len(name), name)

    time_points = sorted({d.get("benchmark", "") for d in data}, key=_sort_key)
    models = sorted({d.get("model", "") for d in data if d.get("model")})
    if not time_points or not models:
        return None

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for m_idx, model in enumerate(models):
        ys: list[float | None] = []
        for tp in time_points:
            match = [d for d in data if d.get("model") == model and d.get("benchmark") == tp]
            ys.append(float(match[0]["score"]) if match else None)
        ax.plot(
            range(len(time_points)),
            ys,
            marker="o",
            label=model,
            color=_COLORS[m_idx % len(_COLORS)],
            linewidth=2,
        )
    ax.set_xticks(range(len(time_points)))
    ax.set_xticklabels(time_points, fontsize=9, rotation=20, ha="right")
    ax.set_ylabel("Score", fontsize=10)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=12)
    ax.legend(fontsize=8, loc="best")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _to_api_path(output_path, project_id) if project_id else str(output_path.resolve())


def render_scatter_chart(
    data: list[dict[str, Any]],
    output_path: Path,
    title: str = "Model Correlation",
    *,
    project_id: str = "",
) -> str | None:
    """Scatter: one model's scores vs another's per benchmark (F4: 相关性 -> 散点)."""
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt

    pair = _scatter_pair(data)
    if not pair:
        return None
    model_a, model_b = pair
    common: dict[str, tuple[float, float]] = {}
    by_bm: dict[str, dict[str, float]] = {}
    for d in data:
        by_bm.setdefault(d.get("benchmark", ""), {})[d.get("model", "")] = float(d["score"])
    for bm, scores in by_bm.items():
        if model_a in scores and model_b in scores:
            common[bm] = (scores[model_a], scores[model_b])
    if len(common) < 4:
        return None
    xs = [v[0] for v in common.values()]
    ys = [v[1] for v in common.values()]

    fig, ax = plt.subplots(figsize=(6.5, 6))
    ax.scatter(xs, ys, s=60, color=_COLORS[0], alpha=0.85)
    for bm, (x, y) in common.items():
        ax.annotate(str(bm), (x, y), fontsize=8, xytext=(4, 4), textcoords="offset points")
    lims = [min(min(xs), min(ys)) * 0.9, max(max(xs), max(ys)) * 1.1]
    ax.plot(lims, lims, ls="--", color="gray", lw=1)
    ax.set_xlabel(model_a, fontsize=10)
    ax.set_ylabel(model_b, fontsize=10)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=12)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return _to_api_path(output_path, project_id) if project_id else str(output_path.resolve())


def render_radar_chart(
    data: list[dict[str, Any]],
    output_path: Path,
    title: str = "Multi-dim Comparison",
    *,
    project_id: str = "",
) -> str | None:
    """Radar: models as polygons across benchmark dimensions (F4: 多维 -> 雷达)."""
    if not _ensure_matplotlib():
        return None

    import matplotlib.pyplot as plt

    models = sorted({d.get("model", "") for d in data if d.get("model")})
    dims = sorted({d.get("benchmark", "") for d in data if d.get("benchmark")})
    if len(dims) < 3:
        return None
    by_model: dict[str, dict[str, float]] = {}
    for d in data:
        by_model.setdefault(d.get("model", ""), {})[d.get("benchmark", "")] = float(d["score"])
    # Only models with all dims get plotted.
    plotted = [m for m in models if all(bm in by_model[m] for bm in dims)]
    if not plotted:
        return None

    angles = [i / len(dims) * 2 * 3.14159 for i in range(len(dims))] + [0.0]
    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw={"projection": "polar"})
    for m_idx, model in enumerate(plotted):
        values = [by_model[model][bm] for bm in dims] + [by_model[model][dims[0]]]
        ax.plot(angles, values, marker="o", label=model, color=_COLORS[m_idx % len(_COLORS)], linewidth=2)
        ax.fill(angles, values, color=_COLORS[m_idx % len(_COLORS)], alpha=0.12)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dims, fontsize=9)
    ax.set_title(title, fontsize=13, fontweight="bold", pad=20)
    ax.legend(fontsize=8, loc="upper right", bbox_to_anchor=(1.15, 1.1))
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
        "valid JSON, no markdown fences or commentary.\n"
        "F2 RULE: every number you output must appear VERBATIM in the evidence "
        "cards. Do NOT estimate, interpolate, round, or fill in missing values -- "
        "if a value is not stated, omit it. When the same metric is reported "
        "differently in different cards, keep each reported value."
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


def matches_evidence_number(value: Any, evidence_text: str) -> bool:
    """Deterministic F2 backstop: a charted number must appear in the evidence.

    The extraction prompts demand verbatim numbers, but nothing enforced it --
    an LLM-slipped hallucinated value went straight onto a chart. This checks
    the value (and common formatting variants: trailing zeros, integer form)
    occurs as a substring of the evidence corpus.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    text = evidence_text or ""
    if not text:
        return False
    forms = {f"{number:g}"}
    if number == int(number):
        forms.add(str(int(number)))
    else:
        decimals = len(f"{number:g}".split(".", 1)[1])
        for extra in range(decimals + 1, 4):
            forms.add(f"{number:.{extra}f}")
    return any(form in text for form in forms)


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
    # Deterministic F2 backstop: drop any entry whose score does not appear
    # verbatim in the evidence corpus (the prompt rule alone let hallucinated
    # numbers onto charts). Skipped when there is no evidence text at all -
    # nothing to validate against.
    evidence_corpus = " ".join(
        f"{getattr(card, 'claim', '') or ''} {getattr(card, 'supporting_text', '') or ''}"
        for card in cards
    )
    if evidence_corpus.strip():
        verbatim_kept = [b for b in benchmarks if matches_evidence_number(b.get("score"), evidence_corpus)]
        if len(verbatim_kept) < len(benchmarks):
            logger.warning(
                "chart data validation: dropped %d/%d benchmark entries whose score "
                "does not appear verbatim in the evidence corpus",
                len(benchmarks) - len(verbatim_kept), len(benchmarks),
            )
        benchmarks = verbatim_kept
    # Cap at 12 entries for readable charts.
    benchmarks = benchmarks[:12]

    paper_titles = structured.get("paper_titles", [])[:3]
    metrics_summary = structured.get("metrics_summary", {})

    # ── Chart 1: shape-driven selection (F4) ────────────────────
    # 时序 -> 折线；两模型共同基准 >=4 -> 相关性散点；单模型 >=4 维 -> 雷达；
    # 其余多模型对比 -> 分组柱状。
    time_series = _looks_like_time_series(benchmarks)
    scatter_pair = _scatter_pair(benchmarks)
    radar_model = _radar_model(benchmarks)

    if time_series:
        path = render_time_series(
            benchmarks,
            output_dir / "chart_trend.png",
            title="Performance Trend",
            project_id=project_id,
        )
        if path:
            charts.append({
                "path": path,
                "alt": "Performance trend over time",
                "section": "实验结果",
                "source": "chart",
            })
    elif scatter_pair:
        path = render_scatter_chart(
            benchmarks,
            output_dir / "chart_correlation.png",
            title=f"{scatter_pair[0]} vs {scatter_pair[1]}",
            project_id=project_id,
        )
        if path:
            charts.append({
                "path": path,
                "alt": f"Correlation between {scatter_pair[0]} and {scatter_pair[1]}",
                "section": "实验结果",
                "source": "chart",
            })
    elif radar_model:
        path = render_radar_chart(
            benchmarks,
            output_dir / "chart_radar.png",
            title="Multi-dim Comparison",
            project_id=project_id,
        )
        if path:
            charts.append({
                "path": path,
                "alt": "Multi-dimension model comparison (radar)",
                "section": "Results",
                "source": "chart",
            })
    elif len(benchmarks) >= 3:
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
