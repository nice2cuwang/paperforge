"""Tests for F4 (chart type chosen by data shape).

Previously every benchmark comparison was rendered as a grouped bar chart no
matter what the data looked like. Now the shape-detection helpers route
time-series data to line charts, two-model correlations to scatter charts and
multi-dimension single-model results to radar charts before falling back to
the bar chart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.services.chart_service as chart

TIMESERIES_BENCHMARKS = [
    {"model": "M1", "benchmark": "2020", "score": 70.0},
    {"model": "M1", "benchmark": "2021", "score": 78.0},
    {"model": "M1", "benchmark": "2022", "score": 85.0},
]

CORRELATION_BENCHMARKS = [
    {"model": "A", "benchmark": bm, "score": s}
    for bm, s in [("GLUE", 80.0), ("SQuAD", 82.0), ("MMLU", 78.0), ("BoolQ", 85.0)]
] + [
    {"model": "B", "benchmark": bm, "score": s}
    for bm, s in [("GLUE", 72.0), ("SQuAD", 75.0), ("MMLU", 70.0), ("BoolQ", 79.0)]
]

RADAR_BENCHMARKS = [
    {"model": "M1", "benchmark": bm, "score": s}
    for bm, s in [
        ("GLUE", 80.0), ("SQuAD", 82.0), ("MMLU", 78.0),
        ("BoolQ", 85.0), ("WinoGrande", 81.0),
    ]
]


# ── shape detection ──────────────────────────────────────────────


def test_looks_like_time_series_three_or_more_time_points():
    assert chart._looks_like_time_series(TIMESERIES_BENCHMARKS) is True
    # "2021 版" still counts as a time point.
    assert chart._looks_like_time_series(
        [{"model": "M", "benchmark": "2020 版", "score": 1},
         {"model": "M", "benchmark": "2021 版", "score": 2},
         {"model": "M", "benchmark": "2022 版", "score": 3}]
    ) is True
    # Benchmark names are not years -> not a time series.
    assert chart._looks_like_time_series(RADAR_BENCHMARKS) is False


def test_scatter_pair_two_models_sharing_benchmarks():
    pair = chart._scatter_pair(CORRELATION_BENCHMARKS)
    assert pair is not None
    assert set(pair) == {"A", "B"}
    # Three models -> no correlation pair.
    three = CORRELATION_BENCHMARKS + [{"model": "C", "benchmark": "GLUE", "score": 90.0}]
    assert chart._scatter_pair(three) is None
    # Two models but too few shared benchmarks -> no scatter.
    sparse = [
        {"model": "A", "benchmark": "GLUE", "score": 1.0},
        {"model": "B", "benchmark": "GLUE", "score": 2.0},
    ]
    assert chart._scatter_pair(sparse) is None


def test_radar_model_single_model_many_dimensions():
    assert chart._radar_model(RADAR_BENCHMARKS) == "M1"
    # Fewer than 4 dimensions per model -> no radar.
    two_dims = [
        {"model": "A", "benchmark": "GLUE", "score": 1.0},
        {"model": "A", "benchmark": "SQuAD", "score": 2.0},
        {"model": "B", "benchmark": "GLUE", "score": 3.0},
        {"model": "B", "benchmark": "SQuAD", "score": 4.0},
    ]
    assert chart._radar_model(two_dims) is None


# ── routing inside generate_charts_from_evidence ─────────────────


def test_time_series_wins_over_other_shapes(monkeypatch, tmp_path):
    # Data that satisfies time-series AND scatter AND radar conditions must
    # route to the line chart (priority: time > scatter > radar).
    data = [
        {"model": m, "benchmark": b, "score": 50.0 + i}
        for i, b in enumerate(["2020", "2021", "2022", "2023"])
        for m in ("A", "B")
    ]
    monkeypatch.setattr(chart, "_extract_structured_data", lambda *a, **k: {"benchmarks": data})
    rendered: list[str] = []
    monkeypatch.setattr(
        chart, "render_time_series",
        lambda d, p, title="", project_id="": rendered.append("line") or str(p),
    )
    monkeypatch.setattr(chart, "render_scatter_chart", lambda *a, **k: rendered.append("scatter") or "/x.png")
    monkeypatch.setattr(chart, "render_radar_chart", lambda *a, **k: rendered.append("radar") or "/x.png")
    monkeypatch.setattr(chart, "render_results_table", lambda *a, **k: None)

    out = chart.generate_charts_from_evidence([], "p", "t", tmp_path)
    assert rendered == ["line"]
    assert len(out) == 1
    assert out[0]["source"] == "chart"


def test_correlation_data_routes_to_scatter(monkeypatch, tmp_path):
    monkeypatch.setattr(chart, "_extract_structured_data", lambda *a, **k: {"benchmarks": CORRELATION_BENCHMARKS})
    rendered: list[str] = []
    monkeypatch.setattr(chart, "render_scatter_chart", lambda *a, **k: rendered.append("scatter") or str(tmp_path / "s.png"))
    monkeypatch.setattr(chart, "render_radar_chart", lambda *a, **k: rendered.append("radar") or "/x.png")
    monkeypatch.setattr(chart, "render_results_table", lambda *a, **k: None)

    out = chart.generate_charts_from_evidence([], "p", "t", tmp_path)
    assert rendered == ["scatter"]
    assert "Correlation between A and B" in out[0]["alt"]


def test_multi_dimension_data_routes_to_radar(monkeypatch, tmp_path):
    monkeypatch.setattr(chart, "_extract_structured_data", lambda *a, **k: {"benchmarks": RADAR_BENCHMARKS})
    rendered: list[str] = []
    monkeypatch.setattr(chart, "render_radar_chart", lambda *a, **k: rendered.append("radar") or str(tmp_path / "r.png"))
    monkeypatch.setattr(chart, "render_results_table", lambda *a, **k: None)

    out = chart.generate_charts_from_evidence([], "p", "t", tmp_path)
    assert rendered == ["radar"]
    assert "radar" in out[0]["alt"]


def test_plain_comparison_falls_back_to_bar(monkeypatch, tmp_path):
    data = [
        {"model": m, "benchmark": "GLUE", "score": s}
        for m, s in [("A", 80.0), ("B", 75.0), ("C", 70.0)]
    ]
    monkeypatch.setattr(chart, "_extract_structured_data", lambda *a, **k: {"benchmarks": data})
    monkeypatch.setattr(chart, "render_benchmark_comparison", lambda *a, **k: str(tmp_path / "bar.png"))
    monkeypatch.setattr(chart, "render_results_table", lambda *a, **k: None)

    out = chart.generate_charts_from_evidence([], "p", "t", tmp_path)
    assert len(out) == 1
    assert "Benchmark comparison chart" in out[0]["alt"]


# ── F2 verbatim rule lives in the extraction prompt ──────────────


def test_extract_prompt_forbids_invented_numbers(monkeypatch):
    from types import SimpleNamespace

    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {"content": '{"benchmarks": [], "paper_titles": [], "metrics_summary": {}}'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    card = SimpleNamespace(
        id="c1",
        claim="实验表明性能提升 30%。",
        supporting_text="MMLU 达到 85.3 分。",
    )
    chart._extract_structured_data([card], "某主题")
    assert "F2 RULE" in captured["system_prompt"]
    assert "Do NOT estimate" in captured["system_prompt"]
    assert "VERBATIM" in captured["system_prompt"]
    assert "85.3" in captured["user_prompt"] and "30%" in captured["user_prompt"]


def test_generate_charts_requires_matplotlib_guard(monkeypatch, tmp_path):
    # The renderer contract: returns None when matplotlib is unavailable,
    # and generate_charts_from_evidence skips it without crashing.
    monkeypatch.setattr(chart, "_ensure_matplotlib", lambda: False)
    monkeypatch.setattr(chart, "_extract_structured_data", lambda *a, **k: {"benchmarks": TIMESERIES_BENCHMARKS})
    out = chart.generate_charts_from_evidence([], "p", "t", tmp_path)
    assert out == []


def test_benchmark_missing_value_not_drawn_as_zero(tmp_path, monkeypatch):
    """B5 regression: a missing (model, benchmark) combo must not become a 0 bar."""
    import matplotlib.pyplot as plt

    captured: dict = {}

    real_barh = plt.Axes.barh

    def spy_barh(self, y, width, *args, **kwargs):
        captured["widths"] = list(width)
        return real_barh(self, y, width, *args, **kwargs)

    monkeypatch.setattr(plt.Axes, "barh", spy_barh)

    data = [
        {"model": "A", "benchmark": "MMLU", "score": 86.4},
        # Model B has no MMLU score - must stay NaN, not 0.
        {"model": "B", "benchmark": "GSM8K", "score": 70.0},
    ]
    chart.render_benchmark_comparison(data, tmp_path / "bar.png", "T")
    widths = [w for w in captured["widths"]]
    assert 0 not in widths
    import math
    assert any(isinstance(w, float) and math.isnan(w) for w in widths)


def test_cjk_font_registered_after_render(tmp_path):
    """B4 regression: chart rendering registers a CJK-capable matplotlib font."""
    import matplotlib.pyplot as plt

    chart.render_benchmark_comparison(
        [{"model": "模型甲", "benchmark": "MMLU", "score": 86.4}],
        tmp_path / "cjk.png",
        "基准对比",
    )
    families = [f for f in plt.rcParams["font.sans-serif"] if isinstance(f, str)]
    cjk = {"SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Micro Hei"}
    assert any(f in cjk for f in families[:3])
    assert plt.rcParams["axes.unicode_minus"] is False
