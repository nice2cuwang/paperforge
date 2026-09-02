"""Auto-workflow entry point.

Uses LangGraph StateGraph when available; falls back to monolithic implementation.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import backend_dir
from app.models import Draft, EvidenceCard, Paper, PaperChunk, ReviewIssue
from app.schemas import RunAutoWorkflowRequest
from app.services.task_registry import add_log, set_artifact, set_progress
from app.services.evidence_service import build_evidence_from_chunks
from app.services.writing_service import build_draft_markdown
from app.services.review_service import review_draft_with_metrics, revise_draft, score_quality
from app.services.export_service import (
    ensure_export_dir, export_bibtex, export_docx, export_json,
    export_markdown, export_pdf, export_quality_report,
)
from app.services.workflow.helpers import (
    _evidence_to_dict, _get_project_or_404, _next_draft_version, _now, _timestamp,
)
from app.services.workflow.ingest import (
    _download_pdf_for_paper, _is_fallback_source, _parse_paper_to_chunks,
    _provider_diagnostics, _resolve_local_pdf_path, _resolve_pdf_url,
    _resolve_pdf_url_with_fallback,
)
from app.services.workflow.search_select import (
    _paper_facet_coverage, _paper_query_score, _query_tokens,
    _text_query_score, run_search_and_select,
)

logger = logging.getLogger(__name__)


def _execute_auto_workflow(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session, task_id: str
) -> dict:
    """Execute the full auto-workflow.

    When langgraph is installed, uses the StateGraph-based implementation
    (graph.py). Falls back to the inline implementation otherwise.
    """
    # 任务上下文：让本次运行中所有 LLM 调用的审计日志归属到 task/project，
    # 供 token 消耗统计聚合。
    from app.services.llm_service import task_context

    with task_context(task_id, project_id):
        return _execute_auto_workflow_inner(project_id, payload, db, task_id)


def _execute_auto_workflow_inner(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session, task_id: str
) -> dict:
    try:
        from app.services.workflow.graph import _build_initial_state, _workflow_graph
        add_log(task_id, "enter _execute_auto_workflow (langgraph)")
        initial_state = _build_initial_state(
            project_id=project_id, payload=payload, db=db, task_id=task_id,
        )
        final_state = _workflow_graph.invoke(initial_state)
        return final_state["result"]
    except ImportError:
        pass  # fall through to inline implementation

    # ── Inline fallback ──────────────────────────────────────────
    add_log(task_id, "enter _execute_auto_workflow (inline)")
    _step_timings: dict[str, float] = {}
    project = _get_project_or_404(project_id, db)
    add_log(task_id, f"project loaded: {project.title}")
    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    add_log(task_id, f"starting search_and_select: query={query[:80]}")

    # ── Clean up papers from previous runs to prevent accumulation ──
    old_paper_count = db.scalar(
        select(func.count(Paper.id)).where(Paper.project_id == project_id)
    ) or 0
    if old_paper_count > 0:
        db.execute(
            delete(PaperChunk).where(
                PaperChunk.paper_id.in_(
                    select(Paper.id).where(Paper.project_id == project_id)
                )
            )
        )
        db.execute(delete(Paper).where(Paper.project_id == project_id))
        db.flush()
        add_log(task_id, f"cleaned up {old_paper_count} papers from previous run")

    _t0 = time.perf_counter()
    selected_papers, inserted, reselection_triggered, _rewritten_queries, _required_terms = run_search_and_select(
        project_id=project_id, query=query, auto_select_limit=payload.auto_select_limit,
        keep_manual_selection=payload.keep_manual_selection, max_results=payload.max_results,
        db=db, task_id=task_id,
    )
    _step_timings["search"] = round(time.perf_counter() - _t0, 3)
    add_log(task_id, f"search_and_select done: selected={len(selected_papers)}, inserted={inserted}")
    del reselection_triggered, _rewritten_queries, _required_terms

    if not selected_papers:
        provider_diag = _provider_diagnostics()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "code": "SEARCH_NO_CANDIDATES", "title": "No credible papers found",
            "message": "Current query returned no usable real candidates.",
            "summary": {"query": query, "provider_candidate_count": 0, "inserted_count": inserted},
            "provider_diagnostics": provider_diag,
            "next_actions": ["Check network connectivity.", "Run with keep_manual_selection=true if trusted papers exist."],
        })

    _t_ingest = time.perf_counter()
    set_progress(task_id, 30, "downloading and parsing selected papers")
    downloaded_count = parsed_count = reused_local_pdf_count = resolved_via_fallback_count = 0
    skipped_no_pdf_count = failed_count = 0
    paper_diagnostics: list[dict[str, Any]] = []

    for index, paper in enumerate(selected_papers, start=1):
        set_progress(task_id, min(62, 30 + int(index * 32 / len(selected_papers))),
                     f"processing paper {index}/{len(selected_papers)}")
        diag: dict[str, Any] = {"paper_id": paper.id, "title": paper.title, "status": "pending"}
        try:
            resolved_local = _resolve_local_pdf_path(paper.local_pdf_path)
            if (paper.source or "").lower() == "fallback":
                resolved_local = None
            if resolved_local:
                if paper.local_pdf_path != str(resolved_local):
                    paper.local_pdf_path = str(resolved_local); paper.updated_at = _now()
                reused_local_pdf_count += 1; diag["status"] = "reused_local_pdf"
                add_log(task_id, f"reuse local pdf: {paper.title}")
            else:
                direct_url = _resolve_pdf_url(paper)
                if direct_url:
                    resolved_url, trace = direct_url, ["direct pdf_url/arxiv available"]
                else:
                    resolved_url, trace = _resolve_pdf_url_with_fallback(paper, task_id=task_id)
                if not resolved_url:
                    skipped_no_pdf_count += 1; diag["status"] = "skipped_no_pdf"; diag["resolution_trace"] = trace
                    continue
                if not direct_url:
                    resolved_via_fallback_count += 1
                _download_pdf_for_paper(paper, task_id=task_id, resolved_pdf_url=resolved_url, resolution_trace=trace)
                downloaded_count += 1; diag["status"] = "downloaded"; diag["resolution_trace"] = trace
            chunk_count = _parse_paper_to_chunks(paper, db, chunk_size=payload.chunk_size)
            parsed_count += 1; diag["chunk_count"] = chunk_count
            if diag["status"] == "pending": diag["status"] = "parsed"
        except Exception as exc:
            failed_count += 1; diag["status"] = "failed"; diag["error"] = str(exc)
        finally:
            paper_diagnostics.append(diag)

    db.flush()
    _step_timings["ingest"] = round(time.perf_counter() - _t_ingest, 3)
    _t_evidence = time.perf_counter()
    set_progress(task_id, 66, "building evidence cards")
    db.execute(delete(EvidenceCard).where(EvidenceCard.project_id == project_id))
    evidence_count = metadata_fallback_evidence_count = low_relevance_filtered_count = 0
    query_tokens = _query_tokens(query)

    for paper in selected_papers:
        chunks = list(db.scalars(
            select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.created_at)
        ).all())
        if not chunks: continue
        chunk_payloads = [{"id": c.id, "text": c.text, "page_start": c.page_start, "page_end": c.page_end} for c in chunks]
        scored = [(_text_query_score(c["text"], query), c) for c in chunk_payloads]
        max_score = max((s for s, _ in scored), default=0.0)
        if query_tokens:
            if max_score < 0.08: chunk_payloads = []; low_relevance_filtered_count += 1
            else:
                threshold = max(0.12, max_score * 0.45)
                filtered = [c for s, c in scored if s >= threshold]
                if filtered: chunk_payloads = filtered
                else: chunk_payloads = []; low_relevance_filtered_count += 1
        elif max_score > 0:
            threshold = max(0.06, max_score * 0.4)
            filtered = [c for s, c in scored if s >= threshold]
            chunk_payloads = filtered if filtered else [c for _, c in sorted(scored, key=lambda r: r[0], reverse=True)[:8]]

        for item in build_evidence_from_chunks(paper.id, chunk_payloads, limit=payload.max_cards):
            if evidence_count >= payload.max_cards: break
            db.add(EvidenceCard(
                id=str(uuid4()), project_id=project_id, paper_id=paper.id,
                chunk_ids=item["chunk_ids"], claim=item["claim"], supporting_text=item["supporting_text"],
                evidence_type=item["evidence_type"], strength=item["strength"],
                limitations=item["limitations"], page_start=item["page_start"], page_end=item["page_end"],
                citation_key=item["citation_key"], used_in_draft=False,
                created_at=_now(), updated_at=_now(),
            ))
            evidence_count += 1
        if evidence_count >= payload.max_cards: break

    if evidence_count == 0:
        for paper in selected_papers:
            if evidence_count >= payload.max_cards: break
            if _is_fallback_source(paper): continue
            score = _paper_query_score(paper, query); facet = _paper_facet_coverage(paper, query)
            if score < 0.18 or facet < 0.5: low_relevance_filtered_count += 1; continue
            abstract = (paper.abstract or "").strip()
            if len(abstract) < 40: continue
            pseudo = {"id": str(uuid4()), "text": f"{paper.title}\n{abstract}"[:2400], "page_start": None, "page_end": None}
            for item in build_evidence_from_chunks(paper.id, [pseudo], limit=1):
                if evidence_count >= payload.max_cards: break
                db.add(EvidenceCard(
                    id=str(uuid4()), project_id=project_id, paper_id=paper.id, chunk_ids=[],
                    claim=item["claim"], supporting_text=item["supporting_text"],
                    evidence_type=item["evidence_type"], source_type="academic", strength="low",
                    limitations="Metadata-only evidence (title/abstract). Full PDF unavailable.",
                    page_start=None, page_end=None, citation_key=item["citation_key"],
                    used_in_draft=False, created_at=_now(), updated_at=_now(),
                ))
                evidence_count += 1; metadata_fallback_evidence_count += 1
        if metadata_fallback_evidence_count:
            add_log(task_id, f"metadata fallback evidence: {metadata_fallback_evidence_count}")

    db.flush()
    if evidence_count == 0:
        skipped_names = [d["title"] for d in paper_diagnostics if d.get("status") == "skipped_no_pdf"][:6]
        failed_items = [{"title": d.get("title"), "error": d.get("error")} for d in paper_diagnostics if d.get("status") == "failed"][:6]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "code": "NO_EVIDENCE_CARDS", "title": "No evidence cards were generated",
            "message": "All selected papers were skipped or failed before parsing.",
            "summary": {"selected_count": len(selected_papers), "reused_local_pdf_count": reused_local_pdf_count,
                        "downloaded_count": downloaded_count, "parsed_count": parsed_count,
                        "skipped_no_pdf_count": skipped_no_pdf_count, "failed_count": failed_count,
                        "evidence_count": 0, "metadata_fallback_evidence_count": 0,
                        "low_relevance_filtered_count": low_relevance_filtered_count},
            "skipped_titles": skipped_names, "failed_items": failed_items,
            "paper_diagnostics": paper_diagnostics[:20],
            "next_actions": ["Upload a local PDF or refine the search query."],
        })

    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == project_id)).all())
    add_log(task_id, f"evidence cards built: {len(cards)}")
    _step_timings["evidence"] = round(time.perf_counter() - _t_evidence, 3)
    _t_draft = time.perf_counter()
    set_progress(task_id, 78, "generating draft")
    content_md, _draft_sections = build_draft_markdown(
        project_title=payload.draft_title or project.title,
        research_question=project.research_question,
        article_type=project.article_type, citation_style=project.citation_style,
        evidence_cards=[_evidence_to_dict(c) for c in cards],
    )
    draft = Draft(
        id=str(uuid4()), project_id=project_id,
        version=_next_draft_version(project_id, db),
        title=payload.draft_title or f"{project.title} Draft",
        content_md=content_md,
        status="draft", quality_score={"overall_score": 0.75}, created_at=_now(),
    )
    db.add(draft); db.flush()
    # 增量提交：草稿一旦生成立即落盘，进程被杀也能从 drafts 表取回。
    draft_id, draft_version = draft.id, draft.version
    db.commit()
    set_artifact(task_id, "draft_id", draft_id)
    set_artifact(task_id, "draft_version", draft_version)
    add_log(task_id, "draft generated and flushed")
    _step_timings["draft"] = round(time.perf_counter() - _t_draft, 3)
    _t_review = time.perf_counter()
    set_progress(task_id, 86, "reviewing draft")
    review_payloads, review_metrics = review_draft_with_metrics(
        draft.content_md, evidence_cards=[_evidence_to_dict(c) for c in cards],
        article_type=project.article_type,
    )
    db.execute(delete(ReviewIssue).where(ReviewIssue.project_id == project_id))
    created_issues: list[ReviewIssue] = []
    for p in review_payloads:
        issue = ReviewIssue(
            id=str(uuid4()), project_id=project_id, draft_id=draft.id,
            severity=p["severity"], issue_type=p["issue_type"], location=p["location"],
            claim=p["claim"], description=p["description"], suggestion=p["suggestion"],
            evidence_ids=p.get("evidence_ids", []), resolved=False, created_at=_now(),
        )
        db.add(issue); created_issues.append(issue)
    critical_count = len([i for i in created_issues if i.severity == "high"])
    draft.status = "reviewed"
    draft.quality_score = score_quality(len(created_issues), critical_count, metrics=review_metrics)

    max_rounds, min_imp, stagnant_limit = 3, 0.02, 2
    current_content, current_issues, current_metrics = draft.content_md, review_payloads, review_metrics
    previous_overall = float(current_metrics.get("overall_score") or 0.0)
    stagnant_rounds = rounds_executed = 0
    best_score = previous_overall
    best_content, best_issues, best_metrics = current_content, current_issues, current_metrics
    review_rounds: list[dict[str, Any]] = [{"round": 0, "stage": "initial_review", "metrics": dict(review_metrics)}]

    for round_index in range(1, max_rounds + 1):
        if bool(current_metrics.get("publication_prepared")): break
        set_progress(task_id, min(96, 90 + round_index * 2), f"revising round {round_index}/{max_rounds}")
        revised = revise_draft(current_content, issues=[
            {"issue_type": str(i.get("issue_type") or ""), "severity": str(i.get("severity") or ""),
             "location": str(i.get("location") or "")} for i in current_issues
        ])
        revised_issues, revised_metrics = review_draft_with_metrics(
            revised, evidence_cards=[_evidence_to_dict(c) for c in cards],
            article_type=project.article_type,
        )
        rounds_executed += 1
        overall = float(revised_metrics.get("overall_score") or 0.0)
        improvement = overall - previous_overall
        add_log(task_id, f"round {round_index}: overall={overall:.3f}, delta={improvement:.3f}")
        review_rounds.append({"round": round_index, "stage": "revision",
                              "metrics": dict(revised_metrics), "improvement": round(improvement, 6)})
        if overall > best_score:
            best_score, best_content, best_issues, best_metrics = overall, revised, revised_issues, revised_metrics
        current_content, current_issues, current_metrics = revised, revised_issues, revised_metrics
        stagnant_rounds = stagnant_rounds + 1 if improvement < min_imp else 0
        previous_overall = overall
        if bool(revised_metrics.get("publication_prepared")): break
        if stagnant_rounds >= stagnant_limit: break

    revised_status = "publication_prepared" if best_metrics.get("publication_prepared") else "revised_needs_human_review"
    revised_critical = len([i for i in best_issues if i.get("severity") == "high"])
    revised_draft = Draft(
        id=str(uuid4()), project_id=project_id, version=_next_draft_version(project_id, db),
        title=(draft.title or "Draft") + " (Revised)", content_md=best_content,
        status=revised_status,
        quality_score=score_quality(len(best_issues), revised_critical, metrics=best_metrics),
        created_at=_now(),
    )
    db.add(revised_draft); db.flush()
    # 增量提交：修订稿 + 审稿 issue 落盘，崩溃后可取回最新修订版本。
    rd_id, rd_version = revised_draft.id, revised_draft.version
    db.commit()
    set_artifact(task_id, "draft_id", rd_id)
    set_artifact(task_id, "draft_version", rd_version)
    _step_timings["review"] = round(time.perf_counter() - _t_review, 3)
    _t_export = time.perf_counter()

    export_files: dict[str, str] = {}
    if payload.auto_export:
        set_progress(task_id, 97, "exporting package")
        target_dir = ensure_export_dir(backend_dir / "data", project_id); stamp = _timestamp()
        md_path = export_markdown(target_dir, f"draft_{revised_draft.version}_{stamp}.md", revised_draft.content_md)
        docx_path = export_docx(target_dir, f"draft_{revised_draft.version}_{stamp}.docx", revised_draft.content_md)
        pdf_path = export_pdf(target_dir, f"draft_{revised_draft.version}_{stamp}.pdf", revised_draft.content_md)
        bib_path = export_bibtex(target_dir, f"refs_{stamp}.bib",
                                 [{"title": p.title, "authors": p.authors, "year": p.year,
                                   "venue": p.venue, "doi": p.doi, "arxiv_id": p.arxiv_id}
                                  for p in selected_papers])
        evidence_path = export_json(target_dir, f"evidence_map_{stamp}.json",
                                    [{"id": c.id, "paper_id": c.paper_id, "chunk_ids": c.chunk_ids,
                                      "claim": c.claim, "supporting_text": c.supporting_text,
                                      "evidence_type": c.evidence_type, "strength": c.strength,
                                      "page_start": c.page_start, "page_end": c.page_end} for c in cards])
        review_lines = ["# Review Report", ""]
        for idx, issue in enumerate(created_issues, start=1):
            review_lines.extend([f"## Issue {idx}", f"- severity: {issue.severity}",
                                 f"- issue_type: {issue.issue_type}", f"- location: {issue.location or 'n/a'}",
                                 f"- description: {issue.description}", f"- suggestion: {issue.suggestion or 'n/a'}", ""])
        review_path = export_markdown(target_dir, f"review_report_{stamp}.md", "\n".join(review_lines))
        quality_path = export_quality_report(
            target_dir, f"quality_report_{stamp}.json", draft_version=revised_draft.version,
            review_rounds=review_rounds, final_metrics=dict(best_metrics),
            publication_prepared=bool(best_metrics.get("publication_prepared")),
        )
        export_files = {"markdown": str(md_path), "docx": str(docx_path), "pdf": str(pdf_path),
                        "bibtex": str(bib_path), "evidence_map": str(evidence_path),
                        "review_report": str(review_path), "quality_report": str(quality_path)}

    db.commit()
    _step_timings["export"] = round(time.perf_counter() - _t_export, 3)
    try:
        from app.middleware.metrics import metrics_record_step
        for step_name, duration in _step_timings.items():
            metrics_record_step(step_name, duration)
    except Exception:
        pass
    add_log(task_id, f"[timing] step_timings={_step_timings}")
    return {
        "query": query, "inserted_count": inserted, "total_papers": len(selected_papers),
        "selected_count": len(selected_papers), "auto_selected_count": len(selected_papers),
        "reused_local_pdf_count": reused_local_pdf_count, "resolved_via_fallback_count": resolved_via_fallback_count,
        "downloaded_count": downloaded_count, "parsed_count": parsed_count,
        "skipped_no_pdf_count": skipped_no_pdf_count, "failed_count": failed_count,
        "evidence_count": evidence_count, "metadata_fallback_evidence_count": metadata_fallback_evidence_count,
        "low_relevance_filtered_count": low_relevance_filtered_count,
        "draft_id": draft.id, "revised_draft_id": revised_draft.id,
        "review_issue_count": len(created_issues), "critical_issue_count": revised_critical,
        "revision_rounds_executed": rounds_executed,
        "publication_prepared": bool(best_metrics.get("publication_prepared")),
        "quality_gate": best_metrics, "export_files": export_files,
        "step_timings": _step_timings,
    }
