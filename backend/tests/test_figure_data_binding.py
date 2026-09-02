"""Tests for F2: figure data must be bound to evidence cards, never inferred.

The SVG extraction prompt previously told the LLM to fill missing data with
"合理的推断" (reasonable inference) -- i.e. it was instructed to fabricate
numbers. Now extraction is bound to the evidence cards and every value must
be verbatim findable in them; the figure plan routes quantitative claims to
data-chart templates.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.services.chart_service as chart
import app.services.image_service as img


def test_svg_extraction_bans_inference_and_binds_evidence(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return {"content": '{"title": "T", "subtitle": "S", "metrics": []}'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    monkeypatch.setattr("app.services.svg_templates.render_metrics_dashboard", lambda **k: "<svg/>")

    result = img.generate_svg_illustration(
        section="实验结果",
        project_title="主题",
        prompt="性能对比",
        index=0,
        section_content="模型 A 准确率 92.3%。",
        kind="chart",
        evidence_text="[0] claim: 模型 A 准确率 92.3%。",
    )
    assert result == "<svg/>"
    # The old instruction to fabricate ("用合理的推断填充") is gone; fabrication is banned.
    assert "用合理的推断填充" not in captured["system_prompt"]
    assert "禁止推断或编造" in captured["system_prompt"]
    assert "逐字" in captured["system_prompt"]
    # Evidence text is fed to extraction.
    assert "模型 A 准确率 92.3%" in captured["user_prompt"]


def test_svg_template_routed_by_plan_kind(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        return {"content": '{"title": "T", "subtitle": "S", "steps": []}'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    monkeypatch.setattr("app.services.svg_templates.render_process_flow", lambda **k: captured.setdefault("called", "process_flow"))
    monkeypatch.setattr(
        "app.services.svg_templates.get_template_for_section",
        lambda s: ("process_flow", "ocean"),
    )
    img.generate_svg_illustration("背景", "主题", "p", 0)
    assert captured.get("called") == "process_flow"


def test_generate_article_images_forwards_evidence_and_kind(monkeypatch):
    calls: list[dict] = []

    def fake_prompts(*a, **k):
        return [{"section": "实验结果", "prompt": "性能对比图", "style": "infographic"}]

    def fake_svg(**kwargs):
        calls.append(kwargs)
        return "<svg/>"

    monkeypatch.setattr(img, "generate_image_prompts", fake_prompts)
    monkeypatch.setattr(img, "fetch_image", lambda *a, **k: None)
    monkeypatch.setattr(img, "generate_svg_illustration", fake_svg)
    monkeypatch.setattr(img, "save_image", lambda *a, **k: "/api/projects/p1/images/fake.svg")

    img.generate_article_images(
        project_id="p1",
        project_title="主题",
        research_question="问题？",
        sections=["实验结果"],
        article_type="wechat_article",
        draft_content="正文。",
        evidence_cards=[{"id": "card-1", "claim": "准确率 92.3%", "supporting_text": "详细数据"}],
        kind_by_section={"实验结果": "chart"},
    )
    assert len(calls) == 1
    assert "92.3%" in calls[0]["evidence_text"]
    assert calls[0]["kind"] == "chart"


def test_format_evidence_for_svg_compacts_cards():
    text = img._format_evidence_for_svg(
        [{"id": "a", "claim": "结论一", "supporting_text": "数据一"}, {"id": "b", "claim": "结论二"}]
    )
    assert "结论一" in text and "数据一" in text and "结论二" in text


def test_chart_extraction_requires_verbatim_values(monkeypatch):
    captured: dict = {}

    def fake_chat(system_prompt="", user_prompt="", max_tokens=None, timeout=None):
        captured["system_prompt"] = system_prompt
        return {"content": '{"benchmarks": [], "paper_titles": [], "metrics_summary": {}}'}

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake_chat)
    chart._extract_structured_data(
        [SimpleNamespace(claim="模型 X 在基准上达到 88.1%", supporting_text="实验数据")],
        "主题",
    )
    assert "VERBATIM" in captured["system_prompt"]
    assert "estimate" in captured["system_prompt"].lower()
    assert "omit it" in captured["system_prompt"]
