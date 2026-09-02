"""Tests for F5b: vision-based figure tagging.

Metadata-only tagging (page / aspect / paper title) cannot tell a company
logo from an architecture diagram — both are wide images on early pages, and
the scoring heuristics actively reward them. Sending the figure pixels to a
multimodal LLM is the only reliable signal, so ``tag_figures_with_categories``
now tries vision first, falls back to metadata tagging, then heuristics.
Figures tagged ``decorative`` are excluded from selection entirely.

Also covers the caption-honesty fix in the image node: a plan caption
describes the figure we *want*, so it must never be attached to an extracted
paper figure (the figure we *have*).
"""
from __future__ import annotations

import pytest

import app.services.figure_extraction_service as fx


def _fig(page: int, w: int, h: int, title: str = "Paper X") -> dict:
    return {"path": f"/nonexistent/fig_{page}_{w}_{h}.png", "page": page, "width": w, "height": h, "paper_title": title, "source": "embedded"}


def _make_png(path, size=(64, 48), color=(200, 30, 30)):
    from PIL import Image

    Image.new("RGB", size, color).save(path)
    return str(path)


# ── Vision tagging ────────────────────────────────────────────────


def test_vision_tags_applied_from_real_images(monkeypatch, tmp_path):
    p1 = _make_png(tmp_path / "a.png")
    p2 = _make_png(tmp_path / "b.png", size=(200, 80))
    figs = [
        {**_fig(1, 200, 80), "path": p1},
        {**_fig(5, 900, 500), "path": p2},
    ]
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, images=None):
        captured["images"] = images
        return {"content": '[{"index": 0, "category": "decorative", "description": "某公司品牌logo横幅"}, {"index": 1, "category": "experiment_curve", "description": "训练损失曲线"}]'}

    monkeypatch.setattr("app.services.llm_service.active_model_supports_vision", lambda: True)
    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    out = fx.tag_figures_with_categories(figs, "多智能体系统")
    assert out[0]["category"] == "decorative"
    assert out[0]["description"] == "某公司品牌logo横幅"
    assert out[1]["category"] == "experiment_curve"
    # The actual pixels must reach the LLM, one base64 image per figure.
    assert captured["images"] is not None
    assert len(captured["images"]) == 2
    assert all(img["media_type"] == "image/jpeg" and img["data"] for img in captured["images"])


def test_vision_decorative_excluded_from_selection(monkeypatch, tmp_path):
    p1 = _make_png(tmp_path / "logo.png", size=(400, 120))
    p2 = _make_png(tmp_path / "curve.png", size=(300, 300))
    figs = [
        {**_fig(1, 2362, 685), "path": p1},
        {**_fig(6, 900, 900), "path": p2},
    ]

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, images=None):
        return {"content": '[{"index": 0, "category": "decorative", "description": "品牌logo"}, {"index": 1, "category": "experiment_curve", "description": "性能曲线"}]'}

    monkeypatch.setattr("app.services.llm_service.active_model_supports_vision", lambda: True)
    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    tagged = fx.tag_figures_with_categories(figs, "主题")
    picked = fx.select_best_figures(tagged, max_count=8)
    # The logo scores highest on geometry (page 1, wide, large) yet must be gone.
    assert len(picked) == 1
    assert picked[0]["category"] == "experiment_curve"


def test_vision_unavailable_falls_back_to_metadata_llm(monkeypatch):
    # Non-vision model configured (e.g. DeepSeek) -> metadata tagging path.
    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, images=None):
        assert images is None, "metadata path must not send images"
        return {"content": '[{"index": 0, "category": "result_table", "description": "宽幅结果对比表"}]'}

    monkeypatch.setattr("app.services.llm_service.active_model_supports_vision", lambda: False)
    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    figs = [_fig(5, 2000, 400)]
    out = fx.tag_figures_with_categories(figs, "主题")
    assert out[0]["category"] == "result_table"
    assert out[0]["description"] == "宽幅结果对比表"


def test_vision_error_falls_back_to_metadata_within_same_run(monkeypatch, tmp_path):
    p1 = _make_png(tmp_path / "a.png")
    figs = [{**_fig(2, 900, 700), "path": p1}]
    calls: list[dict] = []

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None, images=None):
        calls.append({"images": images})
        if images:
            return {"content": "", "error": "provider does not support images"}
        return {"content": '[{"index": 0, "category": "architecture", "description": "系统架构图"}]'}

    monkeypatch.setattr("app.services.llm_service.active_model_supports_vision", lambda: True)
    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)

    out = fx.tag_figures_with_categories(figs, "主题")
    assert out[0]["category"] == "architecture"
    assert len(calls) == 2  # vision attempt, then metadata fallback


def test_vision_missing_file_falls_back_to_heuristics(monkeypatch):
    # Figure path does not resolve to a file and no LLM is available.
    monkeypatch.setattr("app.services.llm_service.active_model_supports_vision", lambda: True)

    def fake_chat(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    figs = [_fig(5, 2000, 400)]
    out = fx.tag_figures_with_categories(figs, "主题")
    assert out[0]["category"] == "result_table"  # wide -> heuristic


# ── Path / image preparation helpers ──────────────────────────────


def test_fs_path_resolves_api_path(monkeypatch, tmp_path):
    target = tmp_path / "data" / "storage" / "p1" / "images" / "figures" / "fig_p1_1.png"
    target.parent.mkdir(parents=True)
    _make_png(target)
    monkeypatch.setattr("app.database.backend_dir", tmp_path)

    resolved = fx._fs_path_for_figure({"path": "/api/projects/p1/images/figures/fig_p1_1.png"})
    assert resolved == target


def test_fs_path_missing_returns_none(tmp_path):
    assert fx._fs_path_for_figure({"path": "/api/projects/p1/images/figures/nope.png"}) is None
    assert fx._fs_path_for_figure({"path": ""}) is None


def test_load_image_downscales_large_figures(tmp_path):
    big = _make_png(tmp_path / "big.png", size=(2400, 1200))
    img = fx._load_image_for_vision(big)
    assert img and img["media_type"] == "image/jpeg"
    import base64
    import io

    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(img["data"]))) as im:
        assert max(im.size) <= fx._VISION_MAX_DIM


# ── Caption honesty in the image node ─────────────────────────────


def test_plan_caption_not_attached_to_extracted_figures(monkeypatch):
    pytest.importorskip("langgraph")
    from app.services.workflow import graph as graph_mod

    import app.services.chart_service as chart_svc
    import app.services.figure_extraction_service as fx_mod
    import app.services.image_service as img_svc
    import app.services.social_proof_service as social_svc

    monkeypatch.setattr(graph_mod, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(graph_mod, "set_artifact", lambda *a, **k: None)
    monkeypatch.setattr(fx_mod, "tag_figures_with_categories", lambda figs, title: figs)
    monkeypatch.setattr(img_svc, "generate_article_images", lambda **k: [])
    monkeypatch.setattr(social_svc, "generate_social_proof_cards", lambda **k: [])
    monkeypatch.setattr(img_svc, "inject_images_into_markdown", lambda content, images: content)
    monkeypatch.setattr(img_svc, "finalize_figures", lambda content, images: content)
    monkeypatch.setattr(
        chart_svc,
        "generate_charts_from_evidence",
        lambda **k: [{"path": "/api/chart1.png", "section": "Framework", "source": "chart", "alt": "chart"}],
    )

    draft = type("D", (), {"content_md": "# t\n\n## 方法\n正文。", "id": "d-1", "version": 1})()
    state = {
        "task_id": "t1",
        "project_id": "p1",
        "project": type("P", (), {"title": "主题", "article_type": "wechat_article", "research_question": "深度学习方法研究"})(),
        "draft": draft,
        "draft_sections": ["一、方法"],
        "cards": [],
        "selected_papers": [],
        "extracted_figures": [
            {
                "path": "/api/f.png",
                "page": 2,
                "width": 900,
                "height": 700,
                "source": "embedded",
                "paper_title": "深度学习方法应用研究",
                "description": "深度学习方法的模型训练流程示意",
            }
        ],
        "db": type("DB", (), {"flush": lambda self: None, "commit": lambda self: None})(),
        "figure_plans": [{"fig_index": 1, "section": "Framework", "kind": "", "evidence_id": "", "caption": "期望中的规划图注"}],
        "conflict_groups": [],
    }
    result = graph_mod.image_generation_node(state)
    images = {img["path"]: img for img in result["generated_images"]}

    # Extracted figure keeps its own description — the plan caption describes
    # the figure we *want*, not this image.
    assert images["/api/f.png"].get("caption") in (None, "")
    # Generated charts still receive the evidence-grounded plan caption.
    assert images["/api/chart1.png"]["caption"] == "期望中的规划图注"
