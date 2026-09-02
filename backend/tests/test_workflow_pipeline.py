import time

import pytest

import hermetic_env
from app.services.search_service import PaperCandidate


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """Hermetic environment: no real LLM calls, no outbound network.

    实现已抽到 tests/hermetic_env.py（与 test_quality_baseline 共用），
    此处只保留 autouse 挂载点，保证本模块全部用例离线运行。
    """
    hermetic_env.install_hermetic(monkeypatch)


def _create_project(client):
    response = client.post(
        "/api/projects",
        json={
            "title": "Workflow Project",
            "research_question": "How does AI affect productivity?",
            "article_type": "policy_report",
            "language": "en",
            "target_words": 3000,
            "citation_style": "APA",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_workflow_pipeline(client, monkeypatch):
    # 路由级检索入口换成语义化候选（PDF 由后续 upload 提供，全程不出网）
    candidates = [
        PaperCandidate(
            title="AI productivity: survey evidence from modern workplaces",
            authors=["Author A"],
            year=2025,
            doi=None,
            arxiv_id=None,
            venue="Mock Venue",
            abstract="Empirical evidence on AI and productivity from workplace surveys.",
            source="mock",
            source_url="https://example.org/ai-productivity",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.9,
        )
    ]
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: candidates)

    project = _create_project(client)
    project_id = project["id"]

    search_res = client.post(
        f"/api/projects/{project_id}/search-papers",
        json={"query": "AI productivity empirical evidence", "max_results": 8},
    )
    assert search_res.status_code == 200
    search_payload = search_res.json()
    assert search_payload["total"] >= 1
    assert search_payload["task_id"]

    papers_res = client.get(f"/api/projects/{project_id}/papers")
    assert papers_res.status_code == 200
    papers = papers_res.json()
    assert len(papers) >= 1
    paper_id = papers[0]["id"]

    select_res = client.post(f"/api/papers/{paper_id}/select", json={"selected": True})
    assert select_res.status_code == 200
    assert select_res.json()["paper"]["selected"] is True

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": paper_id},
        files={
            "file": (
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nAI productivity evidence from surveys.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200
    assert upload_res.json()["paper"]["local_pdf_path"]

    parse_res = client.post(f"/api/papers/{paper_id}/parse", json={"chunk_size": 300})
    assert parse_res.status_code == 200
    assert parse_res.json()["chunk_count"] >= 1

    retrieve_res = client.post(
        f"/api/projects/{project_id}/retrieve-chunks",
        json={"query": "productivity evidence", "top_k": 5},
    )
    assert retrieve_res.status_code == 200
    assert retrieve_res.json()["count"] >= 1

    evidence_res = client.post(
        f"/api/projects/{project_id}/build-evidence", json={"max_cards": 20, "only_selected": True}
    )
    assert evidence_res.status_code == 200
    assert evidence_res.json()["evidence_count"] >= 1

    draft_res = client.post(f"/api/projects/{project_id}/generate-draft", json={"title": "Draft v1"})
    assert draft_res.status_code == 200
    draft_id = draft_res.json()["draft_id"]

    review_res = client.post(f"/api/projects/{project_id}/review-draft", json={"draft_id": draft_id})
    assert review_res.status_code == 200

    revise_res = client.post(f"/api/projects/{project_id}/revise-draft", json={"draft_id": draft_id})
    assert revise_res.status_code == 200
    assert revise_res.json()["version"] >= 2

    export_res = client.post(f"/api/projects/{project_id}/export/markdown")
    assert export_res.status_code == 200
    assert "text/markdown" in export_res.headers["content-type"]


def test_task_api(client):
    project = _create_project(client)
    project_id = project["id"]

    search_res = client.post(
        f"/api/projects/{project_id}/search-papers",
        json={"query": "test query", "max_results": 3},
    )
    assert search_res.status_code == 200
    task_id = search_res.json()["task_id"]

    task_res = client.get(f"/api/tasks/{task_id}")
    assert task_res.status_code == 200
    payload = task_res.json()
    assert payload["task_id"] == task_id
    assert payload["status"] in {"running", "completed", "failed"}


def test_bulk_auto_download_endpoint_handles_missing_pdf_url(client):
    project = _create_project(client)
    project_id = project["id"]

    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "No PDF paper",
            "authors": ["Test Author"],
            "selected": True
        },
    )
    assert paper_res.status_code == 201

    auto_res = client.post(
        f"/api/projects/{project_id}/download-selected-papers?auto_parse=true&chunk_size=900",
        json={},
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] == 1
    assert payload["downloaded_count"] == 0
    assert payload["parsed_count"] == 0
    assert payload["skipped_no_pdf_count"] == 1


def test_run_auto_workflow_uses_local_pdf_and_finishes(client, monkeypatch):
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])

    project = _create_project(client)
    project_id = project["id"]

    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "Uploaded PDF Paper",
            "authors": ["Test Author"],
            "selected": True
        },
    )
    assert paper_res.status_code == 201
    paper_id = paper_res.json()["id"]

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": paper_id},
        files={
            "file": (
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nEmpirical evidence supports the claim.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "empirical evidence",
            "max_results": 5,
            "auto_select_limit": 5,
            "keep_manual_selection": True,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] >= 1
    assert payload["reused_local_pdf_count"] >= 1
    assert payload["parsed_count"] >= 1
    assert payload["evidence_count"] >= 1
    assert payload["draft_id"]
    assert payload["revised_draft_id"]
    assert payload["revision_rounds_executed"] >= 0
    assert "publication_prepared" in payload
    assert "quality_gate" in payload
    # evidence_gap 节点总是返回新键（hermetic 下词法回退，不抛错）
    assert isinstance(payload["gap_added_count"], int)
    assert isinstance(payload["low_evidence_sections"], list)

    drafts_res = client.get(f"/api/projects/{project_id}/drafts")
    assert drafts_res.status_code == 200
    drafts = drafts_res.json()
    revised = next(item for item in drafts if item["id"] == payload["revised_draft_id"])
    # export_node 产出的是面向读者的终稿：证据标注已渲染为 [N] 引用并剥离注释
    assert "REPLACE_ME" not in revised["content_md"]
    assert "<!-- evidence:" not in revised["content_md"]
    assert len(revised["content_md"].strip()) > 100


def test_run_auto_workflow_recovers_from_empty_search_with_trusted_manual_selection(client, monkeypatch):
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])

    project = _create_project(client)
    project_id = project["id"]

    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "为行业小白提供AI模型学习路线研究",
            "authors": ["Test Author"],
            "source": "upload",
            "selected": True,
        },
    )
    assert paper_res.status_code == 201
    paper_id = paper_res.json()["id"]

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": paper_id},
        files={
            "file": (
                "sample.pdf",
                (
                    b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\n"
                    b"\xe4\xb8\xba\xe8\xa1\x8c\xe4\xb8\x9a\xe5\xb0\x8f\xe7\x99\xbd\xe6\x8f\x90\xe4\xbe\x9b"
                    b"AI\xe6\xa8\xa1\xe5\x9e\x8b\xe5\xad\xa6\xe4\xb9\xa0\xe8\xb7\xaf\xe7\xba\xbf\xe7\x9a\x84"
                    b"\xe8\xaf\x81\xe6\x8d\xae\xe5\x88\x86\xe6\x9e\x90\xe4\xb8\x8e\xe8\xaf\xbe\xe7\xa8\x8b\xe5\xbb\xba\xe8\xae\xae"
                    b"\nendstream\n"
                ),
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "为行业小白提供ai模型的学习路线",
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] >= 1
    assert payload["reused_local_pdf_count"] >= 1
    assert payload["evidence_count"] >= 1


@pytest.mark.skip(reason="SQLite + Thread concurrency limitation; passes with Redis/RQ task backend")
def test_run_auto_workflow_async_returns_task_and_completes(client, monkeypatch):
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])
    monkeypatch.setattr("app.services.grobid_client.is_available", lambda: False)

    project = _create_project(client)
    project_id = project["id"]

    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "Async Uploaded PDF Paper",
            "authors": ["Test Author"],
            "selected": True
        },
    )
    assert paper_res.status_code == 201
    paper_id = paper_res.json()["id"]

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": paper_id},
        files={
            "file": (
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nEvidence chunk text for async workflow.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    start_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow-async",
        json={
            "query": "async workflow",
            "max_results": 5,
            "auto_select_limit": 5,
            "keep_manual_selection": True,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert start_res.status_code == 200
    task_id = start_res.json()["task_id"]

    final_payload = None
    for _ in range(150):
        task_res = client.get(f"/api/tasks/{task_id}")
        assert task_res.status_code == 200
        final_payload = task_res.json()
        if final_payload["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert final_payload is not None, f"Task still running after 15s, logs: {final_payload.get('logs', [])[-5:] if final_payload else 'N/A'}"
    assert final_payload["status"] == "completed"
    assert final_payload["result"]["evidence_count"] >= 1


def test_run_auto_workflow_returns_detailed_no_pdf_error(client, monkeypatch):
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])

    project = _create_project(client)
    project_id = project["id"]

    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "Missing PDF Paper",
            "authors": ["Test Author"],
            "selected": True,
        },
    )
    assert paper_res.status_code == 201

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "missing pdf case",
            "max_results": 5,
            "auto_select_limit": 5,
            "keep_manual_selection": True,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 400
    detail = auto_res.json()["detail"]
    assert detail["code"] == "NO_EVIDENCE_CARDS"
    assert detail["summary"]["selected_count"] >= 1
    assert detail["summary"]["skipped_no_pdf_count"] >= 1


def test_run_auto_workflow_returns_no_evidence_when_all_sources_empty(client, monkeypatch):
    # 检索为空 + web/社区为空 → 全来源证据为 0，按 NO_EVIDENCE_CARDS 失败
    # （不恢复搜索级 SEARCH_NO_CANDIDATES：web-only 成文是受支持的产品形态）
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])

    project = _create_project(client)
    project_id = project["id"]

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "为行业小白提供ai模型的学习路线",
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 400
    detail = auto_res.json()["detail"]
    assert detail["code"] == "NO_EVIDENCE_CARDS"
    assert detail["summary"]["selected_count"] == 0
    assert detail["summary"]["evidence_count"] == 0
    assert detail["summary"]["web_evidence_count"] == 0


def test_run_auto_workflow_autoselects_newly_inserted_candidates(client, monkeypatch):
    candidates = [
        PaperCandidate(
            title="AI Learning Path Candidate A",
            authors=["Author A"],
            year=2025,
            doi=None,
            arxiv_id=None,
            venue="Mock Venue",
            abstract="AI learning path planning in higher education.",
            source="mock",
            source_url="https://example.org/a",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.91,
        ),
        PaperCandidate(
            title="AI Learning Path Candidate B",
            authors=["Author B"],
            year=2024,
            doi=None,
            arxiv_id=None,
            venue="Mock Venue",
            abstract="Personalized learning pathway evidence.",
            source="mock",
            source_url="https://example.org/b",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.87,
        ),
    ]
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: candidates)

    project = _create_project(client)
    project_id = project["id"]

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "ai learning path",
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] == 2
    # 两篇都没有 PDF：由摘要块或元数据兜底路径产出证据
    assert payload["evidence_count"] >= 1 or payload["metadata_fallback_evidence_count"] >= 1


def test_run_auto_workflow_uses_metadata_fallback_when_pdf_unavailable(client, monkeypatch):
    candidates = [
        PaperCandidate(
            title="AI Learning Path with downloadable link",
            authors=["Author A"],
            year=2025,
            doi=None,
            arxiv_id="2301.00001",
            venue="Mock Venue",
            abstract="This paper provides a detailed abstract about AI learning paths for beginners in industry.",
            source="mock",
            source_url="https://arxiv.org/abs/2301.00001",
            pdf_url="https://arxiv.org/pdf/2301.00001.pdf",
            oa_status="open",
            license="arxiv",
            relevance_score=0.91,
        ),
        PaperCandidate(
            title="AI Learning Path without pdf",
            authors=["Author B"],
            year=2024,
            doi=None,
            arxiv_id=None,
            venue="Mock Venue",
            abstract="This abstract still contains useful high-level evidence for learning path design.",
            source="mock",
            source_url="https://example.org/no-pdf",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.87,
        ),
    ]
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: candidates)
    # 下载由 hermetic fixture 兜底为抛错；这里验证下载失败后仍能从摘要/元数据成文

    project = _create_project(client)
    project_id = project["id"]

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "ai learning path",
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["failed_count"] >= 1
    # graph 语义：失败论文的摘要会先走摘要块/元数据兜底路径生成证据
    assert payload["evidence_count"] >= 1 or payload["metadata_fallback_evidence_count"] >= 1


def test_run_auto_workflow_reselects_when_manual_selection_exceeds_limit(client, monkeypatch):
    candidates = [
        PaperCandidate(
            title="Evidence Chunk Candidate A",
            authors=["A"],
            year=2025,
            doi="10.1234/reselect.1",
            arxiv_id=None,
            venue="Mock Venue",
            abstract="Evidence chunk focused on empirical evidence for selection behavior in auto workflow.",
            source="mock",
            source_url="https://example.org/reselect-a",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.93,
        ),
        PaperCandidate(
            title="Evidence Chunk Candidate B",
            authors=["B"],
            year=2024,
            doi="10.1234/reselect.2",
            arxiv_id=None,
            venue="Mock Venue",
            abstract="Second candidate discussing evidence chunk validation and ranking thresholds.",
            source="mock",
            source_url="https://example.org/reselect-b",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.89,
        ),
    ]
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: candidates)

    project = _create_project(client)
    project_id = project["id"]

    created_ids: list[str] = []
    for idx in range(4):
        paper_res = client.post(
            f"/api/projects/{project_id}/papers",
            json={
                "title": f"Paper {idx}",
                "authors": ["Tester"],
                "selected": True,
            },
        )
        assert paper_res.status_code == 201
        created_ids.append(paper_res.json()["id"])

    # Give one paper local PDF so reselection has viable candidate.
    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": created_ids[0]},
        files={
            "file": (
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nEvidence chunk text.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "evidence chunk",
            "max_results": 1,
            "auto_select_limit": 2,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] <= 2


def test_run_auto_workflow_does_not_reuse_fallback_local_pdf(client, monkeypatch):
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: [])

    project = _create_project(client)
    project_id = project["id"]

    fallback_paper = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "Fallback Seed",
            "authors": ["Tester"],
            "source": "fallback",
            "selected": True,
        },
    ).json()

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": fallback_paper["id"]},
        files={
            "file": (
                "sample.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nFallback should not be reused.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "fallback behavior",
            "max_results": 3,
            "auto_select_limit": 3,
            "keep_manual_selection": True,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 400
    detail = auto_res.json()["detail"]
    assert detail["code"] == "NO_EVIDENCE_CARDS"
    assert detail["summary"]["reused_local_pdf_count"] == 0


def test_run_auto_workflow_prefers_current_search_scope_over_stale_history(client, monkeypatch):
    candidates = [
        PaperCandidate(
            title="AI learning path roadmap for beginners in industry",
            authors=["Author A"],
            year=2025,
            doi="10.1234/current.scope.1",
            arxiv_id=None,
            venue="Mock Venue",
            abstract=(
                "This study proposes a beginner-oriented AI model learning path with staged milestones, "
                "skills map, and curriculum guidance for industry newcomers."
            ),
            source="mock",
            source_url="https://example.org/current-scope",
            pdf_url=None,
            oa_status="unknown",
            license=None,
            relevance_score=0.93,
        )
    ]
    monkeypatch.setattr("app.services.workflow.search_select.search_papers", lambda query, limit: candidates)

    project = _create_project(client)
    project_id = project["id"]

    stale_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": "When AI meets AI: analyzing AI bills using AI",
            "authors": ["Stale Source"],
            "source": "openalex",
            "selected": True,
        },
    )
    assert stale_res.status_code == 201
    stale_paper = stale_res.json()

    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": stale_paper["id"]},
        files={
            "file": (
                "stale.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\nStale policy evidence.\nendstream\n",
                "application/pdf",
            )
        },
    )
    assert upload_res.status_code == 200

    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": "为行业小白提供ai模型的学习路线",
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200
    payload = auto_res.json()
    assert payload["selected_count"] == 1
    # 新候选没有 PDF：由摘要块或元数据兜底路径产出证据
    assert payload["evidence_count"] >= 1 or payload["metadata_fallback_evidence_count"] >= 1

    papers_res = client.get(f"/api/projects/{project_id}/papers")
    assert papers_res.status_code == 200
    papers = papers_res.json()

    # 上一轮带本地 PDF 的论文行会被 wipe 保留（产品决策），再由本次遴选降级为未选中
    stale_row = next(item for item in papers if item["id"] == stale_paper["id"])
    assert stale_row["selected"] is False

    scoped_row = next(item for item in papers if item.get("doi") == "10.1234/current.scope.1")
    assert scoped_row["selected"] is True


def test_evidence_gap_node_backfills_covered_section_and_flags_absent_section(
    test_session_factory, monkeypatch
):
    """单节点验证（hermetic，无 LLM/向量）：有语料节补卡、无语料节进低证据清单。

    LLM 改写检索式在 hermetic 下返回空 → 回落章节标题词法检索；向量召回被
    hermetic 断言打断 → 纯词法。断言补卡与 flag 两个出口各走一路。
    """
    from uuid import uuid4

    from sqlalchemy import select

    from app.models import EvidenceCard, Paper, PaperChunk, Project
    from app.services.workflow import graph as wg

    monkeypatch.setattr(wg, "add_log", lambda *a, **k: None)
    monkeypatch.setattr(wg, "set_progress", lambda *a, **k: None)

    db = test_session_factory()
    try:
        project = Project(
            id=str(uuid4()),
            title="RAG 评测项目",
            research_question="检索增强生成是否提升问答准确率",
            article_type="policy_report",
            language="zh",
        )
        db.add(project)
        db.flush()
        paper = Paper(
            id=str(uuid4()),
            project_id=project.id,
            title="RAG 评测论文",
            source="upload",
            selected=True,
            relevance_score=0.9,
            metadata_json={},
        )
        db.add(paper)
        db.flush()
        chunk_hit = PaperChunk(
            id=str(uuid4()),
            paper_id=paper.id,
            text="检索增强生成 RAG 评测显示在问答准确率与事实一致性上显著优于朴素 LLM 基线",
            page_start=1,
            page_end=1,
        )
        chunk_unrelated = PaperChunk(
            id=str(uuid4()),
            paper_id=paper.id,
            text="量子引力与弦理论至今缺乏可检验的实验证据",
            page_start=2,
            page_end=2,
        )
        db.add_all([chunk_hit, chunk_unrelated])
        db.commit()

        state = {
            "task_id": "unit-evidence-gap",
            "db": db,
            "project_id": project.id,
            "query": "RAG 评测",
            "selected_papers": [paper],
            "cards": [],
            "draft_sections": ["RAG 问答评测结果", "多模态医学影像诊断"],
        }
        out = wg.evidence_gap_node(state)

        assert out["gap_added_count"] == 1
        # 有本地语料的一节补上 chunk_hit 的证据卡
        assert len(out["cards"]) == 1
        assert out["cards"][0].chunk_ids == [chunk_hit.id]
        # 语料完全缺失的一节进入低证据清单，且不被误补无关 chunk
        assert out["low_evidence_sections"] == ["多模态医学影像诊断"]
        # 卡片确实落库（供 plan_figures/draft 直接消费）
        rows = db.scalars(
            select(EvidenceCard).where(EvidenceCard.project_id == project.id)
        ).all()
        assert [card.chunk_ids for card in rows] == [[chunk_hit.id]]
    finally:
        db.close()
