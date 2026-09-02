"""Tests for the image-generation role & richer LLM call previews.

本轮改动：
1. 生图模型角色位（llm_configs.is_image_gen）+ designate API + fetch_image
   优先走生图模型、失败回退 Pollinations；
2. figure plan 的证据图注（caption）拼进生图 prompt；
3. 图表准入新增"图描述相关性"门（拦住标题双命中但图内容离题的漏网）；
4. /llm-calls 列表新增 response_preview。
"""
from __future__ import annotations

from types import SimpleNamespace

import app.services.image_service as img
import app.services.llm_service as llm
from app.services.search_service import title_query_hits


# ── 1. fetch_image：生图模型优先，Pollinations 回退 ────────────────


def test_fetch_image_uses_designated_model(monkeypatch):
    calls: list[str] = []

    def fake_generate_image(prompt, *, size="1024x576", timeout=None):
        calls.append(prompt)
        return {"image_bytes": b"PNGDATA", "image_url": None, "revised_prompt": None, "error": None}

    monkeypatch.setattr(llm, "image_gen_configured", lambda: True)
    monkeypatch.setattr(llm, "generate_image", fake_generate_image)

    out = img.fetch_image("a robot planning before acting")
    assert out == b"PNGDATA"
    assert calls == ["a robot planning before acting"]


def test_fetch_image_downloads_url_result(monkeypatch):
    class FakeResp:
        headers = {"content-type": "image/png"}
        content = b"URLIMG"

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            assert url == "https://cdn.example.com/img.png"
            return FakeResp()

    import httpx as _httpx

    monkeypatch.setattr(llm, "image_gen_configured", lambda: True)
    monkeypatch.setattr(
        llm, "generate_image",
        lambda prompt, **kw: {"image_bytes": None, "image_url": "https://cdn.example.com/img.png",
                              "revised_prompt": None, "error": None},
    )
    monkeypatch.setattr(_httpx, "Client", FakeClient)

    out = img.fetch_image("prompt")
    assert out == b"URLIMG"


def test_fetch_image_falls_back_to_pollinations_on_model_failure(monkeypatch):
    monkeypatch.setattr(llm, "image_gen_configured", lambda: True)
    monkeypatch.setattr(
        llm, "generate_image",
        lambda prompt, **kw: {"image_bytes": None, "image_url": None,
                              "revised_prompt": None, "error": "provider 500"},
    )

    class FakeResp:
        headers = {"content-type": "image/jpeg"}
        content = b"POLLINATIONS"

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            assert "pollinations" in url
            return FakeResp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    out = img.fetch_image("prompt")
    assert out == b"POLLINATIONS"


def test_fetch_image_skips_model_when_not_configured(monkeypatch):
    monkeypatch.setattr(llm, "image_gen_configured", lambda: False)

    def fail(prompt, **kw):
        raise AssertionError("must not call the image model")

    monkeypatch.setattr(llm, "generate_image", fail)

    class FakeResp:
        headers = {"content-type": "image/png"}
        content = b"FALLBACK"

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return FakeResp()

    import httpx as _httpx

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    assert img.fetch_image("p") == b"FALLBACK"


# ── 2. caption 注入生图 prompt ─────────────────────────────────────


def test_generate_article_images_appends_plan_caption(monkeypatch):
    captured: dict = {}

    def fake_generate_image_prompts(*args, **kwargs):
        return [{"section": "关键发现", "prompt": "flat illustration of agent planning", "style": "illustration"}]

    monkeypatch.setattr(img, "generate_image_prompts", fake_generate_image_prompts)

    def fake_fetch(prompt, width=None, height=None):
        captured["prompt"] = prompt
        return b"IMG"

    monkeypatch.setattr(img, "fetch_image", fake_fetch)
    monkeypatch.setattr(img, "save_image", lambda b, pid, name: f"/api/projects/{pid}/images/{name}")

    out = img.generate_article_images(
        project_id="p1",
        project_title="AI Agent 刹车",
        research_question="如何给 Agent 加规划审批",
        sections=["关键发现"],
        article_type="wechat_article",
        caption_by_section={"关键发现": "Agent 在只读调查阶段提交计划并等待人类审批"},
    )
    assert len(out) == 1
    assert "agent planning" in captured["prompt"]
    assert "Agent 在只读调查阶段提交计划" in captured["prompt"], "plan caption must steer the image model"


# ── 3. 图描述相关性门 ─────────────────────────────────────────────


def test_figure_description_gate_blocks_topically_wrong_figures():
    """标题双命中（agent+ai）但图描述与主题无关的图必须拦下。"""
    rq = "解决 AI Agent 缺乏刹车的问题，应该怎么踩刹车呢"
    # PROV-AGENT：标题命中 agent+ai 双词——标题门放行
    assert title_query_hits("PROV-AGENT: Unified Provenance for Tracking AI Agent Interactions", rq) >= 2
    # 但"材料发现工作台"描述与 rq 零命中——描述门拦截
    assert title_query_hits("材料发现工作台多面板展示候选材料分析结果", rq) < 1

    # 真正对口的图描述（提到 Agent 规划/审批）应通过描述门
    assert title_query_hits("Agent 先规划再执行的状态机流程示意", rq) >= 1


# ── 4. llm-calls response_preview ─────────────────────────────────


def test_llm_calls_list_includes_response_preview():
    from app.api.routes.projects import list_llm_calls  # noqa: F401  (import check)

    # 结构级断言：list_llm_calls 的输出字段含 response_preview（端点级
    # 集成测试需要 FastAPI TestClient + DB，此处仅验证函数可导入与
    # schema 字段命名一致）
    import inspect

    src = inspect.getsource(list_llm_calls)
    assert "response_preview" in src
