"""Hermetic 离线环境（workflow 管线测试共享底座）。

把 workflow 测试的离线化逻辑集中在这里，供 test_workflow_pipeline 与
test_quality_baseline 等需要跑全管线的模块复用：

- LLM 入口换成“未配置 LLM”式的空响应（消费方各自走既有降级路径）；
- graph 中绑定的 web/社区/提供方诊断/PDF 解析兜底全部不出网；
- 向量链路（embedding 模型推理 + Qdrant）直接抛错，消费方走词法兜底。

注意：这些 patch 会改变 llm_service 等模块的行为，只应在跑全管线的
hermetic 测试里安装（模块级 autouse fixture），不能进 conftest 全目录
autouse——llm 层单测（如 test_llm_test_error）依赖真实“无配置即报错”
语义。
"""

_EMPTY_LLM_RESPONSE = {
    "content": "",
    "usage": None,
    "latency_ms": 0,
    "error": "No active LLM config (hermetic test mock)",
    "_reasoning": None,
}


def _empty_chat_completion(*args, **kwargs):
    return _EMPTY_LLM_RESPONSE


def _raise_fn(message: str):
    def _raise(*args, **kwargs):
        raise RuntimeError(message)

    return _raise


def install_hermetic(monkeypatch) -> None:
    """一次性安装全部离线 patch（幂等性由 monkeypatch 的 fixture 生命周期保证）。"""
    from app.services import debate_service, llm_service, review_service, writing_service
    from app.services.workflow import graph as workflow_graph

    # ── LLM：模块级绑定（A 类调用方）+ llm_service 属性（late-import B 类）──
    monkeypatch.setattr(llm_service, "chat_completion", _empty_chat_completion)
    monkeypatch.setattr(llm_service, "chat_completion_text", lambda *a, **k: "")
    monkeypatch.setattr(llm_service, "chat_completion_json", lambda *a, **k: {"_error": "hermetic mock"})
    monkeypatch.setattr(writing_service, "chat_completion_text", lambda *a, **k: "")
    monkeypatch.setattr(review_service, "chat_completion_text", lambda *a, **k: "")
    monkeypatch.setattr(review_service, "chat_completion_json", lambda *a, **k: {"_error": "hermetic mock"})
    monkeypatch.setattr(debate_service, "chat_completion", _empty_chat_completion)

    # ── 网络来源：web / 社区 / LLM 知识检索全空 ──
    monkeypatch.setattr(workflow_graph, "search_web", lambda *a, **k: [])
    monkeypatch.setattr(workflow_graph, "fetch_page_details", lambda *a, **k: {})
    monkeypatch.setattr(workflow_graph, "build_web_evidence", lambda *a, **k: [])
    monkeypatch.setattr(workflow_graph, "search_reddit", lambda *a, **k: [])
    monkeypatch.setattr(workflow_graph, "search_zhihu", lambda *a, **k: [])
    monkeypatch.setattr(workflow_graph, "generate_llm_knowledge", lambda *a, **k: [])
    monkeypatch.setattr(workflow_graph, "build_community_evidence", lambda *a, **k: [])

    # ── 向量链路全断（embedding 模型推理 + Qdrant）──
    # CI 无模型缓存、无 Qdrant 部署：解析/召回处的向量写入与查询都应直接
    # 抛错，让消费方走词法兜底，避免本机默认端点的探测告警或模型下载。
    # retrieval_service 顶层绑定的别名也要盖住（A 类调用方）。
    from app.services import embedding_service, qdrant_service, retrieval_service

    monkeypatch.setattr(embedding_service, "encode_single", _raise_fn("hermetic: embedding disabled"))
    monkeypatch.setattr(embedding_service, "encode_texts", _raise_fn("hermetic: embedding disabled"))
    monkeypatch.setattr(qdrant_service, "search_chunks", _raise_fn("hermetic: qdrant disabled"))
    monkeypatch.setattr(qdrant_service, "upsert_chunks", _raise_fn("hermetic: qdrant disabled"))
    monkeypatch.setattr(retrieval_service, "encode_single", _raise_fn("hermetic: embedding disabled"))
    monkeypatch.setattr(retrieval_service, "search_chunks", _raise_fn("hermetic: qdrant disabled"))
    monkeypatch.setattr(retrieval_service, "recall_chunks", _raise_fn("hermetic: qdrant unavailable"))

    # ── 出网兜底：provider 诊断、PDF 解析兜底、PDF 下载 ──
    monkeypatch.setattr(
        workflow_graph,
        "_provider_diagnostics",
        lambda: {"openalex": "error:hermetic", "crossref": "error:hermetic", "arxiv": "error:hermetic"},
    )
    monkeypatch.setattr(
        workflow_graph,
        "_resolve_pdf_url_with_fallback",
        lambda paper, *a, **k: (None, ["hermetic: pdf url resolution disabled"]),
    )
    monkeypatch.setattr(
        workflow_graph,
        "_download_pdf_for_paper",
        lambda *a, **k: (_ for _ in ()).throw(OSError("hermetic: pdf download disabled")),
    )

    # 路由层检索入口（/search-papers）也换成空结果，测试需要时各自覆盖
    from app.api.routes import workflow as workflow_routes

    monkeypatch.setattr(workflow_routes, "search_papers", lambda *a, **k: [])

    # workflow 内部检索节点走 search_select 模块属性（late-import），路由层
    # patch 盖不住它；旧测试各自手动 patch 此处，统一进底座避免漏网真网调用
    from app.services.workflow import search_select

    monkeypatch.setattr(search_select, "search_papers", lambda *a, **k: [])
