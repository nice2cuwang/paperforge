import time

from app.services.search_service import PaperCandidate

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


def test_workflow_pipeline(client):
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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])

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

    drafts_res = client.get(f"/api/projects/{project_id}/drafts")
    assert drafts_res.status_code == 200
    drafts = drafts_res.json()
    revised = next(item for item in drafts if item["id"] == payload["revised_draft_id"])
    assert "REPLACE_ME" not in revised["content_md"]
    assert "<!-- evidence:" in revised["content_md"]


def test_run_auto_workflow_recovers_from_empty_search_with_trusted_manual_selection(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])

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


def test_run_auto_workflow_async_returns_task_and_completes(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])

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
    for _ in range(60):
        task_res = client.get(f"/api/tasks/{task_id}")
        assert task_res.status_code == 200
        final_payload = task_res.json()
        if final_payload["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert final_payload is not None
    assert final_payload["status"] == "completed"
    assert final_payload["result"]["evidence_count"] >= 1


def test_run_auto_workflow_returns_detailed_no_pdf_error(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])

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


def test_run_auto_workflow_returns_search_no_candidates_when_provider_empty(client, monkeypatch):
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])
    monkeypatch.setattr(
        "app.api.routes.workflow._provider_diagnostics",
        lambda: {"openalex": "error:mock", "crossref": "error:mock", "arxiv": "error:mock"},
    )

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
    assert detail["code"] == "SEARCH_NO_CANDIDATES"
    assert detail["summary"]["provider_candidate_count"] == 0
    assert "provider_diagnostics" in detail


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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: candidates)

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
    assert payload["metadata_fallback_evidence_count"] >= 1


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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: candidates)
    monkeypatch.setattr(
        "app.api.routes.workflow._download_pdf_for_paper",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("SSL download failed")),
    )

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
    assert payload["evidence_count"] >= 1
    assert payload["metadata_fallback_evidence_count"] >= 1


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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: candidates)

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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: [])

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
    monkeypatch.setattr("app.api.routes.workflow.search_papers", lambda query, limit: candidates)
    monkeypatch.setattr(
        "app.api.routes.workflow._download_pdf_for_paper",
        lambda *args, **kwargs: (_ for _ in ()).throw(Exception("network failed")),
    )

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
    assert payload["metadata_fallback_evidence_count"] >= 1

    papers_res = client.get(f"/api/projects/{project_id}/papers")
    assert papers_res.status_code == 200
    papers = papers_res.json()

    stale_row = next(item for item in papers if item["id"] == stale_paper["id"])
    assert stale_row["selected"] is False

    scoped_row = next(item for item in papers if item.get("doi") == "10.1234/current.scope.1")
    assert scoped_row["selected"] is True
