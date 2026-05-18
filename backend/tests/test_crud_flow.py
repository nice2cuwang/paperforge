def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.text == "ok"


def test_stage2_crud_flow(client):
    project_payload = {
        "title": "Test Project",
        "research_question": "Will AI increase productivity?",
        "article_type": "policy_report",
        "target_audience": "policy analysts",
        "language": "en",
        "target_words": 4000,
        "citation_style": "APA",
        "settings": {"tone": "formal"},
    }
    project_res = client.post("/api/projects", json=project_payload)
    assert project_res.status_code == 201
    project = project_res.json()
    project_id = project["id"]
    assert project["target_audience"] == "policy analysts"
    assert project["settings"] == {"tone": "formal"}

    paper_payload = {
        "title": "AI and Productivity",
        "authors": ["A. Smith", "B. Lee"],
        "year": 2024,
        "doi": "10.1234/example",
        "metadata_json": {"source": "unit-test"},
    }
    paper_res = client.post(f"/api/projects/{project_id}/papers", json=paper_payload)
    assert paper_res.status_code == 201
    paper = paper_res.json()
    paper_id = paper["id"]
    assert paper["project_id"] == project_id
    assert paper["metadata_json"] == {"source": "unit-test"}

    chunk_payload = {
        "section": "Introduction",
        "text": "AI can improve productivity in multiple sectors.",
        "page_start": 1,
        "page_end": 1,
        "metadata_json": {"lang": "en"},
    }
    chunk_res = client.post(f"/api/papers/{paper_id}/chunks", json=chunk_payload)
    assert chunk_res.status_code == 201
    chunk = chunk_res.json()
    chunk_id = chunk["id"]

    evidence_payload = {
        "paper_id": paper_id,
        "chunk_ids": [chunk_id],
        "claim": "AI raises productivity.",
        "supporting_text": "Empirical evidence shows gains in throughput.",
        "evidence_type": "empirical_result",
        "strength": "medium",
    }
    evidence_res = client.post(f"/api/projects/{project_id}/evidence", json=evidence_payload)
    assert evidence_res.status_code == 201
    evidence = evidence_res.json()
    assert evidence["project_id"] == project_id
    assert evidence["paper_id"] == paper_id

    draft_payload = {
        "version": 1,
        "title": "Draft v1",
        "content_md": "# Draft\n\nProductivity evidence.",
        "status": "draft",
    }
    draft_res = client.post(f"/api/projects/{project_id}/drafts", json=draft_payload)
    assert draft_res.status_code == 201
    draft = draft_res.json()
    draft_id = draft["id"]
    assert draft["version"] == 1

    review_payload = {
        "draft_id": draft_id,
        "severity": "high",
        "issue_type": "citation",
        "description": "The claim needs stronger citation support.",
        "evidence_ids": [evidence["id"]],
    }
    review_res = client.post(f"/api/projects/{project_id}/review-issues", json=review_payload)
    assert review_res.status_code == 201
    review = review_res.json()
    assert review["draft_id"] == draft_id
    assert review["evidence_ids"] == [evidence["id"]]

    duplicate_draft_res = client.post(f"/api/projects/{project_id}/drafts", json=draft_payload)
    assert duplicate_draft_res.status_code == 409

    another_project_res = client.post(
        "/api/projects",
        json={
            "title": "Other Project",
            "research_question": "Other?",
            "article_type": "policy_report",
            "language": "en",
            "target_words": 2000,
            "citation_style": "APA",
        },
    )
    assert another_project_res.status_code == 201
    another_project_id = another_project_res.json()["id"]

    cross_project_evidence = client.post(
        f"/api/projects/{another_project_id}/evidence",
        json={
            "paper_id": paper_id,
            "chunk_ids": [],
            "claim": "cross project claim",
            "supporting_text": "cross project support",
        },
    )
    assert cross_project_evidence.status_code == 404
