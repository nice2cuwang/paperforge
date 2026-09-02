"""Component seams (C 方案): 五处轻替换点的单点间接层测试。

替换点机制：节点调用处走 ``_seam(state, key, fallback)``，替换实现随
initial_state 的 ``component_overrides`` 注入——编译图单例可复用、并发安全
（无模块级可变全局）。不传时行为与直接调用默认服务函数完全一致（由全量
回归兜底）；本文件验证 ① _seam 语义、② 三个代表性节点的接线真实生效、
③ 五个缝键的源码契约不被删改。

公开契约见 docs/architecture/component-seams.md（含每缝签名与替换示例）。
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.workflow import graph as wg


def _blank_state(**extra) -> dict:
    state = {
        "task_id": "t-seam",
        "project_id": "p-seam",
        "current_content": "原文内容。",
        "current_metrics": {},
        "revision_round": 0,
        "stagnant_rounds": 0,
        "previous_overall": 0.0,
        "best_score": 0.0,
        "best_content": "",
        "best_issues": [],
        "best_metrics": {},
        "review_rounds": [],
        "current_issues": [],
        "cards": [],
    }
    state.update(extra)
    return state


def test_seam_falls_back_to_default_without_overrides():
    default = Mock(return_value="default-result")
    result = wg._seam({}, "plan_sections", default)
    assert result is default  # 不传 overrides → 逐字节原路径


def test_seam_resolves_override_from_state_and_build_initial_state_carries_it():
    override = Mock()
    state = _blank_state(component_overrides={"plan_sections": override})
    assert wg._seam(state, "plan_sections", Mock()) is override
    assert wg._seam(state, "review", Mock()) is not override  # 其他缝不受影响


def test_build_initial_state_snapshots_overrides(monkeypatch):
    payload = SimpleNamespace(draft_title=None)
    db = object()
    monkeypatch.setattr(wg, "set_progress", lambda *a, **k: None)
    overrides = {"review": lambda *a, **k: ([], {})}
    state = wg._build_initial_state(
        project_id="p1", payload=payload, db=db, task_id="t1",
        component_overrides=overrides,
    )
    assert state["component_overrides"] == overrides
    assert wg._build_initial_state(
        project_id="p1", payload=payload, db=db, task_id="t1"
    )["component_overrides"] is None


def test_plan_sections_override_drives_thesis_thread_sections(monkeypatch):
    """缝 2：plan_sections —— 大纲替换后 draft_sections 变成自定义输出。"""
    monkeypatch.setattr(wg, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(wg, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(wg, "build_thesis_statement", lambda **k: "FAKE THESIS")

    captured: dict = {}
    fake_sections = ["自定义引言", "自定义主体"]

    def fake_plan_sections(article_type, project_title, research_question, evidence_cards):
        captured["article_type"] = article_type
        captured["project_title"] = project_title
        captured["research_question"] = research_question
        captured["evidence_cards"] = evidence_cards
        return fake_sections

    state = _blank_state(
        payload=SimpleNamespace(draft_title=None),
        project=SimpleNamespace(
            title="P", research_question="Q", article_type="policy_report"
        ),
        component_overrides={"plan_sections": fake_plan_sections},
    )
    result = wg.thesis_thread_node(state)

    assert captured == {
        "article_type": "policy_report",
        "project_title": "P",
        "research_question": "Q",
        "evidence_cards": [],
    }
    assert result["draft_sections"] == fake_sections
    assert result["thesis_statement"] == "FAKE THESIS"


def test_build_draft_override_replaces_persisted_draft_content(
    test_session_factory, monkeypatch
):
    """缝 3：build_draft —— 替换正文产出，草稿落库内容随之变化。"""
    monkeypatch.setattr(wg, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(wg, "set_progress", lambda *a, **k: None)
    monkeypatch.setattr(wg, "set_artifact", lambda *a, **k: None)

    session = test_session_factory()
    try:
        from app.models import Project

        session.add(
            Project(
                id="p-seam-draft",
                title="P",
                research_question="Q",
                article_type="policy_report",
                language="zh",
                citation_style="APA",
            )
        )
        session.flush()
        fake_content = "完全由替换实现产出的正文。"
        state = _blank_state(
            project_id="p-seam-draft",
            db=session,
            payload=SimpleNamespace(draft_title="替换标题"),
            project=SimpleNamespace(title="P", research_question="Q", article_type="policy_report", citation_style="APA"),
            component_overrides={
                "build_draft": lambda **kwargs: (fake_content, ["S1"])
            },
        )
        result = wg.draft_node(state)

        assert result["current_content"] == fake_content
        assert result["draft_sections"] == ["S1"]
        from app.models import Draft
        from sqlalchemy import select

        row = session.scalars(
            select(Draft).where(Draft.project_id == "p-seam-draft")
        ).first()
        assert row is not None and row.content_md == fake_content
        assert row.title == "替换标题"
    finally:
        session.close()


def test_review_override_drives_revision_round_metrics(monkeypatch):
    """缝 4：review —— 替换审稿后评分/停滞/最佳稿追踪都按新指标走。"""
    monkeypatch.setattr(wg, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(wg, "set_progress", lambda *a, **k: None)

    captured: dict = {}

    def fake_review(content_md, evidence_cards, article_type, task_id):
        captured["content"] = content_md
        captured["cards"] = evidence_cards
        captured["article_type"] = article_type
        return ([], {"overall_score": 0.51, "evidence_coverage": 0.4})

    state = _blank_state(
        project=SimpleNamespace(article_type="literature_review"),
        previous_overall=0.5,  # 涨幅 0.01 < MIN_IMPROVEMENT → stagnant 累积
        component_overrides={"review": fake_review},
    )
    result = wg.review_node(state)

    assert captured["content"] == "原文内容。"
    assert captured["article_type"] == "literature_review"
    assert result["current_metrics"]["overall_score"] == 0.51
    assert result["previous_overall"] == 0.51
    assert result["stagnant_rounds"] == 1
    assert result["best_score"] == 0.51
    assert result["best_content"] == "原文内容。"


def test_five_seam_keys_are_wired_in_graph_source():
    """契约锁：五处缝键出现在调用点（防改名/删缝不告警）。"""
    import inspect

    source = inspect.getsource(wg)
    for key in ("recall_chunks", "plan_sections", "build_draft", "review", "generate_image"):
        assert f'"{key}"' in source, f"seam key {key} not wired in graph.py"
    # runner 透传签名存在（二次开发入口）
    import app.services.workflow.runner as runner_mod

    assert "component_overrides" in inspect.getsource(runner_mod._execute_auto_workflow_inner)
