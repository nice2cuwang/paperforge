from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import backend_dir
from app.models import Draft, EvidenceCard, PaperChunk, ReviewIssue
from app.schemas import RunAutoWorkflowRequest
from app.services.task_registry import add_log, fail_task, set_progress, _fail_task_for_exception
from app.services.evidence_service import build_evidence_from_chunks
from app.services.writing_service import build_draft_markdown, build_outline
from app.services.review_service import review_draft_with_metrics, revise_draft, score_quality
from app.services.export_service import (
    ensure_export_dir,
    export_bibtex,
    export_docx,
    export_json,
    export_markdown,
    export_pdf,
    export_quality_report,
)
from app.services.workflow.helpers import (
    _get_project_or_404,
    _next_draft_version,
    _now,
    _timestamp,
    _evidence_to_dict,
)
from app.services.workflow.ingest import (
    _download_pdf_for_paper,
    _is_fallback_source,
    _parse_paper_to_chunks,
    _provider_diagnostics,
    _resolve_local_pdf_path,
    _resolve_pdf_url,
    _resolve_pdf_url_with_fallback,
)
from app.services.workflow.search_select import (
    run_search_and_select,
    _query_tokens,
    _text_query_score,
    _paper_query_score,
    _paper_facet_coverage,
)

logger = logging.getLogger(__name__)


def _execute_auto_workflow(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session, task_id: str
) -> dict:
    add_log(task_id, "enter _execute_auto_workflow")
    project = _get_project_or_404(project_id, db)
    add_log(task_id, f"project loaded: {project.title}")
    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    add_log(task_id, f"starting search_and_select: query={query[:80]}")
    selected_papers, inserted, reselection_triggered = run_search_and_select(
        project_id=project_id,
        query=query,
        auto_select_limit=payload.auto_select_limit,
        keep_manual_selection=payload.keep_manual_selection,
        max_results=payload.max_results,
        db=db,
        task_id=task_id,
    )
    add_log(task_id, f"search_and_select done: selected={len(selected_papers)}, inserted={inserted}")
    auto_selected_count = len(selected_papers)
    candidates: list[Any] = []  # populated below if needed for diagnostics
    if not selected_papers:
        provider_diag = _provider_diagnostics()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "SEARCH_NO_CANDIDATES",
                "title": "No credible papers found from current search",
                "message": (
                    "Current query returned no usable real candidates from external providers. "
                    "Auto workflow stopped to avoid writing from stale or synthetic papers."
                ),
                "summary": {
                    "query": query,
                    "provider_candidate_count": 0,
                    "inserted_count": inserted,
                },
                "provider_diagnostics": provider_diag,
                "next_actions": [
                    "Check backend network connectivity to OpenAlex/Crossref/arXiv.",
                    "If you use a local proxy, set PAPERFORGE_PROXY_URL=http://host.docker.internal:<port> in .env.",
                    "Or run backend outside Docker and retry search.",
                    "If you already selected trusted local papers, run with keep_manual_selection=true.",
                ],
            },
        )

    set_progress(task_id, 30, "downloading and parsing selected papers")
    downloaded_count = 0
    parsed_count = 0
    reused_local_pdf_count = 0
    resolved_via_fallback_count = 0
    skipped_no_pdf_count = 0
    failed_count = 0
    paper_diagnostics: list[dict[str, Any]] = []

    for index, paper in enumerate(selected_papers, start=1):
        set_progress(
            task_id,
            min(62, 30 + int(index * 32 / len(selected_papers))),
            f"processing selected paper {index}/{len(selected_papers)}",
        )
        paper_diag: dict[str, Any] = {"paper_id": paper.id, "title": paper.title, "status": "pending"}
        try:
            resolved_local_pdf = _resolve_local_pdf_path(paper.local_pdf_path)
            if (paper.source or "").lower() == "fallback":
                resolved_local_pdf = None
            if resolved_local_pdf:
                if paper.local_pdf_path != str(resolved_local_pdf):
                    paper.local_pdf_path = str(resolved_local_pdf)
                    paper.updated_at = _now()
                reused_local_pdf_count += 1
                paper_diag["status"] = "reused_local_pdf"
                add_log(task_id, f"reuse local pdf: {paper.title}")
            else:
                direct_pdf_url = _resolve_pdf_url(paper)
                if direct_pdf_url:
                    resolved_pdf_url = direct_pdf_url
                    resolve_trace = ["direct pdf_url/arxiv available"]
                else:
                    resolved_pdf_url, resolve_trace = _resolve_pdf_url_with_fallback(paper, task_id=task_id)
                if not resolved_pdf_url:
                    skipped_no_pdf_count += 1
                    paper_diag["status"] = "skipped_no_pdf"
                    paper_diag["resolution_trace"] = resolve_trace
                    add_log(task_id, f"skip(no downloadable or uploaded pdf): {paper.title}")
                    continue

                used_fallback = not bool(direct_pdf_url)
                if used_fallback:
                    resolved_via_fallback_count += 1
                    add_log(task_id, f"resolved via fallback: {paper.title}")
                _download_pdf_for_paper(
                    paper,
                    task_id=task_id,
                    resolved_pdf_url=resolved_pdf_url,
                    resolution_trace=resolve_trace,
                )
                downloaded_count += 1
                paper_diag["status"] = "downloaded"
                paper_diag["resolution_trace"] = resolve_trace
                add_log(task_id, f"downloaded: {paper.title}")

            chunk_count = _parse_paper_to_chunks(paper, db, chunk_size=payload.chunk_size)
            parsed_count += 1
            paper_diag["chunk_count"] = chunk_count
            if paper_diag["status"] == "pending":
                paper_diag["status"] = "parsed"
            add_log(task_id, f"parsed: {paper.title} ({chunk_count} chunks)")
        except Exception as exc:  # noqa: BLE001
            failed_count += 1
            paper_diag["status"] = "failed"
            paper_diag["error"] = str(exc)
            add_log(task_id, f"failed processing {paper.title}: {exc}")
        finally:
            paper_diagnostics.append(paper_diag)

    db.flush()
    add_log(task_id, "db flushed after paper processing")
    set_progress(task_id, 66, "building evidence cards")
    db.execute(delete(EvidenceCard).where(EvidenceCard.project_id == project_id))
    evidence_count = 0
    metadata_fallback_evidence_count = 0
    low_relevance_filtered_count = 0
    query_tokens = _query_tokens(query)
    for paper in selected_papers:
        chunks = list(
            db.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.created_at)).all()
        )
        if not chunks:
            continue
        chunk_payloads = [
            {
                "id": chunk.id,
                "text": chunk.text,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
            }
            for chunk in chunks
        ]
        scored_chunks = [(_text_query_score(item["text"], query), item) for item in chunk_payloads]
        max_chunk_score = max((score for score, _ in scored_chunks), default=0.0)
        if query_tokens:
            if max_chunk_score < 0.08:
                chunk_payloads = []
                low_relevance_filtered_count += 1
            else:
                threshold = max(0.12, max_chunk_score * 0.45)
                filtered = [item for score, item in scored_chunks if score >= threshold]
                if filtered:
                    chunk_payloads = filtered
                else:
                    chunk_payloads = []
                    low_relevance_filtered_count += 1
        elif max_chunk_score > 0:
            threshold = max(0.06, max_chunk_score * 0.4)
            filtered = [item for score, item in scored_chunks if score >= threshold]
            if filtered:
                chunk_payloads = filtered
            else:
                chunk_payloads = [item for _, item in sorted(scored_chunks, key=lambda row: row[0], reverse=True)[:8]]

        for item in build_evidence_from_chunks(paper.id, chunk_payloads, limit=payload.max_cards):
            if evidence_count >= payload.max_cards:
                break
            db.add(
                EvidenceCard(
                    id=str(uuid4()),
                    project_id=project_id,
                    paper_id=paper.id,
                    chunk_ids=item["chunk_ids"],
                    claim=item["claim"],
                    supporting_text=item["supporting_text"],
                    evidence_type=item["evidence_type"],
                    strength=item["strength"],
                    limitations=item["limitations"],
                    page_start=item["page_start"],
                    page_end=item["page_end"],
                    citation_key=item["citation_key"],
                    used_in_draft=False,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            evidence_count += 1
        if evidence_count >= payload.max_cards:
            break

    if evidence_count == 0:
        for paper in selected_papers:
            if evidence_count >= payload.max_cards:
                break
            if _is_fallback_source(paper):
                continue
            paper_score = _paper_query_score(paper, query)
            facet_coverage = _paper_facet_coverage(paper, query)
            if paper_score < 0.18 or facet_coverage < 0.5:
                low_relevance_filtered_count += 1
                continue
            abstract = (paper.abstract or "").strip()
            if len(abstract) < 40:
                continue
            pseudo_chunk = {
                "id": str(uuid4()),
                "text": f"{paper.title}\n{abstract}"[:2400],
                "page_start": None,
                "page_end": None,
            }
            for item in build_evidence_from_chunks(paper.id, [pseudo_chunk], limit=1):
                if evidence_count >= payload.max_cards:
                    break
                db.add(
                    EvidenceCard(
                        id=str(uuid4()),
                        project_id=project_id,
                        paper_id=paper.id,
                        chunk_ids=[],
                        claim=item["claim"],
                        supporting_text=item["supporting_text"],
                        evidence_type=item["evidence_type"],
                        strength="low",
                        limitations=(
                            "Metadata-only evidence (title/abstract). "
                            "Full PDF unavailable or parsing failed; manual verification required."
                        ),
                        page_start=None,
                        page_end=None,
                        citation_key=item["citation_key"],
                        used_in_draft=False,
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
                evidence_count += 1
                metadata_fallback_evidence_count += 1
        if metadata_fallback_evidence_count > 0:
            add_log(
                task_id,
                f"metadata fallback evidence generated: {metadata_fallback_evidence_count}",
            )

    db.flush()
    if evidence_count == 0:
        skipped_titles = [item["title"] for item in paper_diagnostics if item.get("status") == "skipped_no_pdf"][:6]
        failed_items = [
            {"title": item.get("title"), "error": item.get("error")}
            for item in paper_diagnostics
            if item.get("status") == "failed"
        ][:6]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_EVIDENCE_CARDS",
                "title": "No evidence cards were generated",
                "message": (
                    "All selected papers were skipped or failed before parsing. "
                    "At least one selected paper must provide a downloadable or uploaded PDF."
                ),
                "summary": {
                    "selected_count": len(selected_papers),
                    "reused_local_pdf_count": reused_local_pdf_count,
                    "resolved_via_fallback_count": resolved_via_fallback_count,
                    "downloaded_count": downloaded_count,
                    "parsed_count": parsed_count,
                    "skipped_no_pdf_count": skipped_no_pdf_count,
                    "failed_count": failed_count,
                    "evidence_count": evidence_count,
                    "metadata_fallback_evidence_count": metadata_fallback_evidence_count,
                    "low_relevance_filtered_count": low_relevance_filtered_count,
                },
                "skipped_titles": skipped_titles,
                "failed_items": failed_items,
                "next_actions": [
                    "In Paper Library, keep at least one selected paper with a valid downloadable PDF.",
                    "If auto download fails, upload a local PDF manually and parse it once.",
                    "Refine the query with domain and audience constraints (for example: beginner, learning path, higher education).",
                    "If only metadata is available, manually verify generated claims before publication.",
                    "Re-run One-click Auto Workflow after at least one paper reaches parsed status.",
                ],
                "paper_diagnostics": paper_diagnostics[:20],
            },
        )

    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
    add_log(task_id, f"evidence cards built: {len(cards)}")
    set_progress(task_id, 78, "generating draft")
    draft = Draft(
        id=str(uuid4()),
        project_id=project_id,
        version=_next_draft_version(project_id, db),
        title=payload.draft_title or f"{project.title} Draft",
        content_md=build_draft_markdown(
            project_title=payload.draft_title or project.title,
            research_question=project.research_question,
            article_type=project.article_type,
            citation_style=project.citation_style,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
        ),
        status="draft",
        quality_score={"overall_score": 0.75},
        created_at=_now(),
    )
    db.add(draft)
    db.flush()

    db.flush()
    add_log(task_id, "draft generated and flushed")
    set_progress(task_id, 86, "reviewing draft")
    review_payloads, review_metrics = review_draft_with_metrics(
        draft.content_md,
        evidence_cards=[_evidence_to_dict(item) for item in cards],
        article_type=project.article_type,
    )
    db.execute(delete(ReviewIssue).where(ReviewIssue.project_id == project_id))
    created_issues: list[ReviewIssue] = []
    for payload_item in review_payloads:
        issue = ReviewIssue(
            id=str(uuid4()),
            project_id=project_id,
            draft_id=draft.id,
            severity=payload_item["severity"],
            issue_type=payload_item["issue_type"],
            location=payload_item["location"],
            claim=payload_item["claim"],
            description=payload_item["description"],
            suggestion=payload_item["suggestion"],
            evidence_ids=payload_item["evidence_ids"],
            resolved=False,
            created_at=_now(),
        )
        db.add(issue)
        created_issues.append(issue)
    critical_count = len([item for item in created_issues if item.severity == "high"])
    draft.status = "reviewed"
    draft.quality_score = score_quality(len(created_issues), critical_count, metrics=review_metrics)

    # Multi-round revision loop (max 3) with early stop conditions.
    # Stop when publication gate passes, or two consecutive rounds improve < 0.02.
    max_revision_rounds = 3
    min_improvement = 0.02
    current_content = draft.content_md
    current_issues = review_payloads
    current_metrics = review_metrics
    previous_overall = float(current_metrics.get("overall_score") or 0.0)
    stagnant_rounds = 0
    rounds_executed = 0

    best_content = current_content
    best_issues = current_issues
    best_metrics = current_metrics
    best_score = previous_overall

    # Quality gate snapshot history
    review_rounds: list[dict[str, Any]] = [
        {
            "round": 0,
            "stage": "initial_review",
            "metrics": dict(review_metrics),
        }
    ]

    for round_index in range(1, max_revision_rounds + 1):
        if bool(current_metrics.get("publication_prepared")):
            add_log(task_id, f"revision stop: publication gate passed before round {round_index}")
            break

        set_progress(task_id, min(96, 90 + round_index * 2), f"revising draft round {round_index}/{max_revision_rounds}")
        revised_candidate = revise_draft(
            current_content,
            issues=[
                {
                    "issue_type": str(item.get("issue_type") or ""),
                    "severity": str(item.get("severity") or ""),
                    "location": str(item.get("location") or ""),
                }
                for item in current_issues
            ],
        )
        revised_issues, revised_metrics = review_draft_with_metrics(
            revised_candidate,
            evidence_cards=[_evidence_to_dict(item) for item in cards],
            article_type=project.article_type,
        )

        rounds_executed += 1
        overall = float(revised_metrics.get("overall_score") or 0.0)
        improvement = overall - previous_overall
        add_log(
            task_id,
            f"revision round {round_index}: overall={overall:.3f}, delta={improvement:.3f}, "
            f"critical={revised_metrics.get('critical_issues')}, "
            f"unsupported={revised_metrics.get('unsupported_claims')}",
        )

        review_rounds.append(
            {
                "round": round_index,
                "stage": "revision",
                "metrics": dict(revised_metrics),
                "improvement": round(improvement, 6),
            }
        )

        if overall > best_score:
            best_score = overall
            best_content = revised_candidate
            best_issues = revised_issues
            best_metrics = revised_metrics

        current_content = revised_candidate
        current_issues = revised_issues
        current_metrics = revised_metrics

        if improvement < min_improvement:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        previous_overall = overall

        if bool(revised_metrics.get("publication_prepared")):
            add_log(task_id, f"revision stop: publication gate passed at round {round_index}")
            break
        if stagnant_rounds >= 2:
            add_log(task_id, "revision stop: two consecutive rounds improved < 0.02")
            break

    revised_content = best_content
    revised_issue_payloads = best_issues
    revised_metrics = best_metrics
    revised_critical_count = len([item for item in revised_issue_payloads if item.get("severity") == "high"])
    revised_status = "publication_prepared" if revised_metrics.get("publication_prepared") else "revised_needs_human_review"
    revised_draft = Draft(
        id=str(uuid4()),
        project_id=project_id,
        version=_next_draft_version(project_id, db),
        title=(draft.title or "Draft") + " (Revised)",
        content_md=revised_content,
        status=revised_status,
        quality_score=score_quality(
            len(revised_issue_payloads),
            revised_critical_count,
            metrics=revised_metrics,
        ),
        created_at=_now(),
    )
    db.add(revised_draft)
    db.flush()

    export_files: dict[str, str] = {}
    add_log(task_id, f"revision done: rounds={rounds_executed}, best_score={best_score:.3f}")
    if payload.auto_export:
        set_progress(task_id, 97, "exporting package")
        target_dir = ensure_export_dir(backend_dir / "data", project_id)
        stamp = _timestamp()

        md_path = export_markdown(target_dir, f"draft_{revised_draft.version}_{stamp}.md", revised_draft.content_md)
        docx_path = export_docx(target_dir, f"draft_{revised_draft.version}_{stamp}.docx", revised_draft.content_md)
        pdf_path = export_pdf(target_dir, f"draft_{revised_draft.version}_{stamp}.pdf", revised_draft.content_md)

        bib_papers = selected_papers
        bib_path = export_bibtex(
            target_dir,
            f"refs_{stamp}.bib",
            [
                {
                    "title": item.title,
                    "authors": item.authors,
                    "year": item.year,
                    "venue": item.venue,
                    "doi": item.doi,
                    "arxiv_id": item.arxiv_id,
                }
                for item in bib_papers
            ],
        )

        evidence_map_path = export_json(
            target_dir,
            f"evidence_map_{stamp}.json",
            [
                {
                    "id": item.id,
                    "paper_id": item.paper_id,
                    "chunk_ids": item.chunk_ids,
                    "claim": item.claim,
                    "supporting_text": item.supporting_text,
                    "evidence_type": item.evidence_type,
                    "strength": item.strength,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                }
                for item in cards
            ],
        )
        review_lines = ["# Review Report", ""]
        if not created_issues:
            review_lines.append("No review issues found.")
        for idx, item in enumerate(created_issues, start=1):
            review_lines.extend(
                [
                    f"## Issue {idx}",
                    f"- severity: {item.severity}",
                    f"- issue_type: {item.issue_type}",
                    f"- location: {item.location or 'n/a'}",
                    f"- description: {item.description}",
                    f"- suggestion: {item.suggestion or 'n/a'}",
                    "",
                ]
            )
        review_path = export_markdown(target_dir, f"review_report_{stamp}.md", "\n".join(review_lines))

        quality_path = export_quality_report(
            target_dir,
            f"quality_report_{stamp}.json",
            draft_version=revised_draft.version,
            review_rounds=review_rounds,
            final_metrics=dict(revised_metrics),
            publication_prepared=bool(revised_metrics.get("publication_prepared")),
        )

        export_files = {
            "markdown": str(md_path),
            "docx": str(docx_path),
            "pdf": str(pdf_path),
            "bibtex": str(bib_path),
            "evidence_map": str(evidence_map_path),
            "review_report": str(review_path),
            "quality_report": str(quality_path),
        }

    db.commit()
    result = {
        "query": query,
        "inserted_count": inserted,
        "total_papers": len(selected_papers),
        "selected_count": len(selected_papers),
        "auto_selected_count": auto_selected_count,
        "reused_local_pdf_count": reused_local_pdf_count,
        "resolved_via_fallback_count": resolved_via_fallback_count,
        "downloaded_count": downloaded_count,
        "parsed_count": parsed_count,
        "skipped_no_pdf_count": skipped_no_pdf_count,
        "failed_count": failed_count,
        "evidence_count": evidence_count,
        "metadata_fallback_evidence_count": metadata_fallback_evidence_count,
        "low_relevance_filtered_count": low_relevance_filtered_count,
        "draft_id": draft.id,
        "revised_draft_id": revised_draft.id,
        "review_issue_count": len(created_issues),
        "critical_issue_count": revised_critical_count,
        "revision_rounds_executed": rounds_executed,
        "publication_prepared": bool(revised_metrics.get("publication_prepared")),
        "quality_gate": revised_metrics,
        "export_files": export_files,
    }
    return result
