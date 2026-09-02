"""LangGraph StateGraph for auto-workflow orchestration.

Replaces the monolithic _execute_auto_workflow with 9 composable nodes.
Each node reads/updates WorkflowState and handles its own errors.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import backend_dir
from app.models import Draft, EvidenceCard, Paper, PaperChunk, Project, ReviewIssue
from app.schemas import RunAutoWorkflowRequest
from app.services.evidence_service import build_evidence_from_chunks
from app.services.export_service import (
    ensure_export_dir,
    export_bibtex,
    export_docx,
    export_json,
    export_markdown,
    export_pdf,
    export_quality_report,
)
from app.services.review_service import (
    debate_review_with_metrics,
    review_draft_with_metrics,
    revise_draft,
    score_quality,
)
from app.services.task_registry import add_log, set_artifact, set_progress
from app.services.workflow.helpers import (
    _evidence_to_dict,
    _get_project_or_404,
    _next_draft_version,
    _now,
    _timestamp,
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
    _paper_facet_coverage,
    _paper_query_score,
    _query_tokens,
    _text_query_score,
    run_search_and_select,
)
from app.services.writing_service import (
    build_draft_markdown,
    build_thesis_statement,
    plan_article_sections,
    strip_evidence_comments,
)
from app.services.web_search_service import (
    build_web_evidence,
    fetch_page_details,
    search_web,
)
from app.services.community_service import (
    search_reddit,
    search_zhihu,
    generate_llm_knowledge,
    build_community_evidence,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────

MAX_REVISION_ROUNDS = 3
STAGNATION_LIMIT = 2
MIN_IMPROVEMENT = 0.02

# ── State ────────────────────────────────────────────────────────


class WorkflowState(TypedDict, total=False):
    # Input
    project_id: str
    payload: RunAutoWorkflowRequest
    db: Session
    task_id: str

    # Search
    query: str
    project: Project
    selected_papers: list[Paper]
    inserted: int
    auto_selected_count: int
    paper_diagnostics: list[dict[str, Any]]
    rewritten_queries: list[str]
    required_terms: list[str]

    # Ingest counters
    downloaded_count: int
    parsed_count: int
    reused_local_pdf_count: int
    resolved_via_fallback_count: int
    skipped_no_pdf_count: int
    failed_count: int

    # Evidence
    evidence_count: int
    metadata_fallback_evidence_count: int
    low_relevance_filtered_count: int
    web_evidence_count: int
    community_evidence_count: int
    llm_knowledge_count: int
    cards: list[EvidenceCard]

    # Conflict detection (S4)
    conflict_groups: list[dict[str, Any]]

    # Search quality signal: True when no selected paper is topically
    # relevant (authority-only scores let same-keyword noise through).
    papers_off_topic: bool

    # Draft
    draft: Draft | None
    current_content: str
    draft_sections: list[str]
    thesis_statement: str
    figure_plans: list[dict[str, Any]]
    # L2: per-figure evidence dependency (path/section/evidence_ids) so the
    # revise loop can re-sync captions when a section's evidence changes.
    figure_deps: list[dict[str, Any]]

    # Review / revision loop
    current_issues: list[dict[str, Any]]
    current_metrics: dict[str, Any]
    review_rounds: list[dict[str, Any]]
    revision_round: int
    stagnant_rounds: int
    previous_overall: float
    best_score: float
    best_content: str
    best_issues: list[dict[str, Any]]
    best_metrics: dict[str, Any]
    created_issues: list[ReviewIssue]

    # Output
    revised_draft: Draft | None
    revised_status: str
    revised_critical_count: int
    export_files: dict[str, str]
    result: dict[str, Any]
    topic_assessment: dict[str, Any]
    generated_images: list[dict[str, str]]
    extracted_figures: list[dict[str, Any]]

    # Observability
    node_timings: dict[str, float]


# ── Initial state ────────────────────────────────────────────────


def _build_initial_state(
    project_id: str, payload: RunAutoWorkflowRequest, db: Session, task_id: str
) -> WorkflowState:
    return WorkflowState(
        project_id=project_id,
        payload=payload,
        db=db,
        task_id=task_id,
        query="",
        selected_papers=[],
        inserted=0,
        auto_selected_count=0,
        paper_diagnostics=[],
        rewritten_queries=[],
        required_terms=[],
        downloaded_count=0,
        parsed_count=0,
        reused_local_pdf_count=0,
        resolved_via_fallback_count=0,
        skipped_no_pdf_count=0,
        failed_count=0,
        evidence_count=0,
        metadata_fallback_evidence_count=0,
        low_relevance_filtered_count=0,
        web_evidence_count=0,
        community_evidence_count=0,
        llm_knowledge_count=0,
        cards=[],
        draft=None,
        current_content="",
        draft_sections=[],
        current_issues=[],
        current_metrics={},
        review_rounds=[],
        revision_round=0,
        stagnant_rounds=0,
        previous_overall=0.0,
        best_score=0.0,
        best_content="",
        best_issues=[],
        best_metrics={},
        created_issues=[],
        revised_draft=None,
        revised_status="",
        revised_critical_count=0,
        export_files={},
        result={},
        node_timings={},
        topic_assessment={},
        generated_images=[],
        extracted_figures=[],
    )


# ── Node 1: search_and_select ────────────────────────────────────


def search_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    payload = state["payload"]

    add_log(task_id, "langgraph: search_and_select")
    project = _get_project_or_404(state["project_id"], db)
    add_log(task_id, f"project loaded: {project.title}")

    query = (payload.query or project.research_question or project.title).strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Search query is empty")

    add_log(task_id, f"searching: query={query[:80]}")

    # ── Clean up stale auto-search rows from previous runs ──────
    # 用户资产会被保留：手工录入 / 本地上传（含本地 PDF）的论文行跨 run 留存，
    # ingest 直接复用本地 PDF，旧行由 run_search_and_select 负责重新遴选
    # （重新打分、deselect）。只有从未产生 PDF 的自动检索行才是上一次运行的
    # 临时产物，需要清理以免无限累积。
    def _is_user_asset(paper: Paper) -> bool:
        source = (paper.source or "").strip().lower()
        if not source or source in ("upload", "manual", "fallback"):
            return True
        return bool((paper.local_pdf_path or "").strip())

    old_papers = list(db.scalars(select(Paper).where(Paper.project_id == state["project_id"])).all())
    stale_papers = [p for p in old_papers if not _is_user_asset(p)]
    if stale_papers:
        # Cascade: delete chunks, then stale paper rows (evidence/issues are cleaned by their own nodes)
        db.execute(delete(PaperChunk).where(PaperChunk.paper_id.in_([p.id for p in stale_papers])))
        for p in stale_papers:
            db.delete(p)
        db.flush()
        add_log(
            task_id,
            f"cleaned {len(stale_papers)}/{len(old_papers)} stale auto-search papers "
            f"(kept {len(old_papers) - len(stale_papers)} user/local-PDF rows)",
        )

    selected_papers, inserted, reselection_triggered, rewritten_queries, required_terms = run_search_and_select(
        project_id=state["project_id"],
        query=query,
        auto_select_limit=payload.auto_select_limit,
        keep_manual_selection=payload.keep_manual_selection,
        max_results=payload.max_results,
        db=db,
        task_id=task_id,
        project_title=project.title or "",
    )
    add_log(task_id, f"search done: selected={len(selected_papers)}, inserted={inserted}")
    del reselection_triggered

    if not selected_papers:
        provider_diag = _provider_diagnostics()
        add_log(
            task_id,
            f"WARNING: no academic papers selected from {inserted} candidates. "
            f"Workflow will continue with web/community sources only. "
            f"Provider diagnostics: {provider_diag}"
        )

    # ── Off-topic pool detection ─────────────────────────────────
    # relevance_score encodes authority (citations + recency), not topical
    # fit: a query with no real academic hits ("DeepSeek Harness") still
    # fills the pool with same-keyword journal noise. Flag the pool so the
    # figure pipeline stops broadcasting off-topic papers' figures/titles.
    #
    # 判定标准：多数派（≥60% 命中）才算论文池对口。此前只要 1 篇标题
    # 命中 ≥2 个查询词就整池放行，擦边池（1/12 命中）照样把离题图表
    # 灌进正文。
    papers_off_topic = False
    if selected_papers:
        from app.services.search_service import title_query_hits

        topical = [p for p in selected_papers if title_query_hits(p.title or "", query) >= 2]
        topical_ratio = len(topical) / len(selected_papers)
        if not topical or topical_ratio < 0.6:
            papers_off_topic = True
            add_log(
                task_id,
                f"WARNING: only {len(topical)}/{len(selected_papers)} selected papers share >=2 "
                f"content terms with the query (ratio {topical_ratio:.0%} < 60%) - the academic "
                f"pool is mostly off-topic noise. Paper figures and social proof cards will be "
                f"skipped; the article will lean on web/community evidence instead."
            )

    return {
        "query": query,
        "project": project,
        "selected_papers": selected_papers,
        "inserted": inserted,
        "auto_selected_count": len(selected_papers),
        "paper_diagnostics": [],
        "rewritten_queries": rewritten_queries,
        "required_terms": required_terms,
        "papers_off_topic": papers_off_topic,
    }


# ── Node 2: ingest_papers ───────────────────────────────────────


def ingest_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    payload = state["payload"]
    papers = state["selected_papers"]

    set_progress(task_id, 30, "downloading and parsing selected papers")
    downloaded = 0
    parsed = 0
    reused = 0
    fallback = 0
    skipped = 0
    failed = 0
    diags: list[dict[str, Any]] = []

    for index, paper in enumerate(papers, start=1):
        set_progress(task_id, min(62, 30 + int(index * 32 / len(papers))), f"processing paper {index}/{len(papers)}")
        diag: dict[str, Any] = {"paper_id": paper.id, "title": paper.title, "status": "pending"}
        try:
            resolved_local = _resolve_local_pdf_path(paper.local_pdf_path)
            if (paper.source or "").lower() == "fallback":
                resolved_local = None
            if resolved_local:
                if paper.local_pdf_path != str(resolved_local):
                    paper.local_pdf_path = str(resolved_local)
                    paper.updated_at = _now()
                reused += 1
                diag["status"] = "reused_local_pdf"
                add_log(task_id, f"reuse local pdf: {paper.title}")
            else:
                direct_url = _resolve_pdf_url(paper)
                if direct_url:
                    resolved_url = direct_url
                    trace = ["direct pdf_url/arxiv available"]
                else:
                    resolved_url, trace = _resolve_pdf_url_with_fallback(paper, task_id=task_id)
                if not resolved_url:
                    skipped += 1
                    diag["status"] = "skipped_no_pdf"
                    diag["resolution_trace"] = trace

                    # Fallback: create a chunk from abstract if available
                    abstract = (paper.abstract or "").strip()
                    if len(abstract) >= 40:
                        try:
                            from app.models import PaperChunk
                            fallback_chunk = PaperChunk(
                                id=str(uuid4()),
                                paper_id=paper.id,
                                text=f"{paper.title}\n{abstract}"[:2400],
                                page_start=None,
                                page_end=None,
                                created_at=_now(),
                            )
                            db.add(fallback_chunk)
                            diag["status"] = "fallback_abstract"
                            add_log(task_id, f"no PDF but abstract available: {paper.title}")
                        except Exception:
                            logger.debug("Abstract fallback failed for %s", paper.title, exc_info=True)
                    else:
                        add_log(task_id, f"skip(no downloadable pdf): {paper.title}")
                    continue

                used_fb = not bool(direct_url)
                if used_fb:
                    fallback += 1
                _download_pdf_for_paper(paper, task_id=task_id, resolved_pdf_url=resolved_url, resolution_trace=trace)
                downloaded += 1
                diag["status"] = "downloaded"
                diag["resolution_trace"] = trace

            chunk_count = _parse_paper_to_chunks(paper, db, chunk_size=payload.chunk_size)
            parsed += 1
            diag["chunk_count"] = chunk_count
            if diag["status"] == "pending":
                diag["status"] = "parsed"
        except Exception as exc:
            failed += 1
            diag["status"] = "failed"
            diag["error"] = str(exc)
            add_log(task_id, f"failed processing {paper.title}: {exc}")

            # PDF fallback: if PDF parsing failed, create a chunk from the abstract
            abstract = (paper.abstract or "").strip()
            if len(abstract) >= 40:
                try:
                    from app.models import PaperChunk
                    fallback_chunk = PaperChunk(
                        id=str(uuid4()),
                        paper_id=paper.id,
                        text=f"{paper.title}\n{abstract}"[:2400],
                        page_start=None,
                        page_end=None,
                        created_at=_now(),
                    )
                    db.add(fallback_chunk)
                    fallback += 1
                    diag["status"] = "fallback_abstract"
                    add_log(task_id, f"fallback to abstract for: {paper.title}")
                except Exception:
                    logger.debug("Abstract fallback failed for %s", paper.title, exc_info=True)
        finally:
            diags.append(diag)

    db.flush()
    add_log(task_id, "db flushed after paper processing")

    # ── Collect extracted figures from all papers ──────────────
    all_figures: list[dict[str, Any]] = []
    for paper in papers:
        meta = paper.metadata_json or {}
        for fig in meta.get("extracted_figures", []):
            all_figures.append({**fig, "paper_id": paper.id, "paper_title": paper.title})
    if all_figures:
        add_log(task_id, f"collected {len(all_figures)} extracted figures from papers")

    return {
        "downloaded_count": downloaded,
        "parsed_count": parsed,
        "reused_local_pdf_count": reused,
        "resolved_via_fallback_count": fallback,
        "skipped_no_pdf_count": skipped,
        "failed_count": failed,
        "paper_diagnostics": diags,
        "extracted_figures": all_figures,
    }


# ── Node 3: build_evidence ───────────────────────────────────────


def evidence_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    payload = state["payload"]
    papers = state["selected_papers"]
    query = state["query"]

    set_progress(task_id, 66, "building evidence cards")
    db.execute(delete(EvidenceCard).where(EvidenceCard.project_id == state["project_id"]))

    ev_count = 0
    meta_count = 0
    low_rel_count = 0
    query_tokens = _query_tokens(query)

    # ── Hybrid recall (批次7): one project-wide vector pass over Qdrant ──
    # Chunks ingested earlier are already embedded in Qdrant, but the per-paper
    # selection below was purely lexical (token overlap): semantically close
    # chunks that share no query vocabulary could never pass the lexical gate.
    # The vector ranks both rescue those chunks and order the survivors.
    vector_ranks: dict[str, int] = {}
    vector_by_paper: dict[str, list[str]] = defaultdict(list)
    try:
        from app.services.retrieval_service import recall_chunks

        for rank, hit in enumerate(recall_chunks(query, project_id=state["project_id"], top_k=120)):
            cid = str(hit.get("id") or "")
            if not cid:
                continue
            vector_ranks[cid] = rank
            pid = str(hit.get("paper_id") or "")
            if pid:
                vector_by_paper[pid].append(cid)
        add_log(task_id, f"vector recall: {len(vector_ranks)} chunks ranked across {len(vector_by_paper)} papers")
    except Exception as exc:
        add_log(task_id, f"vector recall unavailable ({type(exc).__name__}); lexical scoring only")

    for paper in papers:
        chunks = list(
            db.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.created_at)).all()
        )
        if not chunks:
            continue

        chunk_payloads = [
            {"id": c.id, "text": c.text, "page_start": c.page_start, "page_end": c.page_end}
            for c in chunks
        ]
        payload_by_id = {str(c["id"]): c for c in chunk_payloads}
        scored = [(_text_query_score(c["text"], query), c) for c in chunk_payloads]
        max_score = max((s for s, _ in scored), default=0.0)
        if query_tokens:
            if max_score < 0.08:
                chunk_payloads = []
                low_rel_count += 1
            else:
                threshold = max(0.12, max_score * 0.45)
                filtered = [c for s, c in scored if s >= threshold]
                chunk_payloads = filtered if filtered else []
                if not filtered:
                    low_rel_count += 1
        elif max_score > 0:
            threshold = max(0.06, max_score * 0.4)
            filtered = [c for s, c in scored if s >= threshold]
            chunk_payloads = filtered if filtered else [c for _, c in sorted(scored, key=lambda r: r[0], reverse=True)[:8]]

        # Merge in vector-recalled chunks of this paper that the lexical gate
        # dropped, then order everything by semantic closeness (vector rank
        # first, lexical score as tiebreaker for unranked chunks).
        recall_ids = [cid for cid in vector_by_paper.get(paper.id, []) if cid in payload_by_id]
        if vector_ranks and (recall_ids or chunk_payloads):
            merged: dict[str, dict[str, Any]] = {str(c["id"]): c for c in chunk_payloads}
            for cid in recall_ids[:6]:
                merged.setdefault(cid, payload_by_id[cid])
            lexical_scores = {str(c["id"]): s for s, c in scored}

            def _order(chunk: dict[str, Any]) -> tuple[int, Any]:
                cid = str(chunk["id"])
                if cid in vector_ranks:
                    return (0, vector_ranks[cid])
                return (1, -lexical_scores.get(cid, 0.0))

            chunk_payloads = sorted(merged.values(), key=_order)

        for item in build_evidence_from_chunks(paper.id, chunk_payloads, limit=payload.max_cards):
            if ev_count >= payload.max_cards:
                break
            db.add(EvidenceCard(
                id=str(uuid4()), project_id=state["project_id"], paper_id=paper.id,
                chunk_ids=item["chunk_ids"], claim=item["claim"], supporting_text=item["supporting_text"],
                evidence_type=item["evidence_type"], strength=item["strength"],
                limitations=item["limitations"], page_start=item["page_start"], page_end=item["page_end"],
                citation_key=item["citation_key"], used_in_draft=False,
                created_at=_now(), updated_at=_now(),
            ))
            ev_count += 1
        if ev_count >= payload.max_cards:
            break

    # Metadata fallback
    if ev_count == 0:
        for paper in papers:
            if ev_count >= payload.max_cards:
                break
            if _is_fallback_source(paper):
                continue
            score = _paper_query_score(paper, query)
            facet = _paper_facet_coverage(paper, query)
            if score < 0.18 or facet < 0.5:
                low_rel_count += 1
                continue
            abstract = (paper.abstract or "").strip()
            if len(abstract) < 40:
                continue
            pseudo = {"id": str(uuid4()), "text": f"{paper.title}\n{abstract}"[:2400], "page_start": None, "page_end": None}
            for item in build_evidence_from_chunks(paper.id, [pseudo], limit=1):
                if ev_count >= payload.max_cards:
                    break
                db.add(EvidenceCard(
                    id=str(uuid4()), project_id=state["project_id"], paper_id=paper.id,
                    chunk_ids=[], claim=item["claim"], supporting_text=item["supporting_text"],
                    evidence_type=item["evidence_type"], source_type="academic", strength="low",
                    limitations="Metadata-only evidence (title/abstract). Full PDF unavailable.",
                    page_start=None, page_end=None, citation_key=item["citation_key"],
                    used_in_draft=False, created_at=_now(), updated_at=_now(),
                ))
                ev_count += 1
                meta_count += 1

    db.flush()
    if meta_count:
        add_log(task_id, f"metadata fallback evidence generated: {meta_count}")

    if ev_count == 0:
        skipped_titles = [d["title"] for d in state["paper_diagnostics"] if d.get("status") == "skipped_no_pdf"][:6]
        failed_items = [
            {"title": d.get("title"), "error": d.get("error")}
            for d in state["paper_diagnostics"] if d.get("status") == "failed"
        ][:6]
        add_log(
            task_id,
            f"WARNING: no academic evidence cards generated. "
            f"selected={len(papers)}, reused={state['reused_local_pdf_count']}, "
            f"downloaded={state['downloaded_count']}, parsed={state['parsed_count']}, "
            f"skipped={state['skipped_no_pdf_count']}, failed={state['failed_count']}. "
            f"Workflow will continue with web/community sources. "
            f"skipped_titles={skipped_titles}, failed_items={failed_items}"
        )

    cards = list(db.scalars(select(EvidenceCard).where(EvidenceCard.project_id == state["project_id"])).all())
    add_log(task_id, f"evidence cards built: {len(cards)}")

    return {
        "evidence_count": ev_count,
        "metadata_fallback_evidence_count": meta_count,
        "low_relevance_filtered_count": low_rel_count,
        "cards": cards,
    }


# ── Node 3b: gather_web_sources ────────────────────────────────


def web_sources_node(state: WorkflowState) -> dict[str, Any]:
    """Search the web for additional evidence beyond academic papers."""
    from datetime import datetime as _dt

    task_id = state["task_id"]
    db = state["db"]
    project = state["project"]
    query = state["query"] or project.research_question or project.title
    rewritten_queries = state.get("rewritten_queries", [])

    add_log(task_id, "langgraph: gather_web_sources")
    set_progress(task_id, 68, "searching web sources")

    # web 优先（时效话题）：扩大检索面 — 更多查询变体、更多结果、多抓页面
    web_priority = bool((state.get("topic_assessment") or {}).get("web_priority_effective"))
    max_per_query = 12 if web_priority else 8
    max_page_fetches = 18 if web_priority else 12

    web_count = 0
    try:
        # Build web search queries: use original query + rewritten queries for product-specific searches
        # 查询-时效配对：web 优先的时效话题核心查询限 1 个月、新闻变体限 1 周，
        # 重写查询不限时（保留背景资料入口）。不限时的旧路径不受影响。
        query_plan: list[tuple[str, str | None]] = [(query, "month" if web_priority else None)]
        for rq in rewritten_queries[: (5 if web_priority else 3)]:
            if rq.lower() != query.lower():
                query_plan.append((rq, None))

        # Add a year-tagged variant for news / recent-event topics
        current_year = str(_dt.now().year)
        if current_year not in query:
            query_plan.append((f"{query} {current_year}", None))

        # web 优先时追加"最新/新闻"变体，并限定最近一周
        if web_priority:
            existing = {q.lower() for q, _ in query_plan}
            for suffix in ("最新 消息", "news"):
                variant = f"{query} {suffix}"
                if variant.lower() not in existing:
                    query_plan.append((variant, "week"))

        add_log(
            task_id,
            f"web search queries (web_priority={web_priority}): "
            + "; ".join(f"{q!r}[recency={r}]" for q, r in query_plan),
        )

        all_web_results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for wq, recency in query_plan:
            results = search_web(wq, max_results=max_per_query, recency=recency)
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_web_results.append(r)
        add_log(task_id, f"web search returned {len(all_web_results)} total results (deduped)")

        # 抓取预算优先花在最新发布的源上（published 未知者排后）
        def _freshness_key(r: dict[str, Any]) -> str:
            return r.get("published") or "0000-00-00"

        by_freshness = sorted(all_web_results, key=_freshness_key, reverse=True)

        # Fetch page text for top results (limit to avoid timeout)
        for result in by_freshness[:max_page_fetches]:
            url = result.get("url", "")
            if url:
                details = fetch_page_details(url)
                text = details.get("text")
                if text and len(text) > 100:
                    result["full_text"] = text
                # 页面 meta 的发布时间比 snippet 猜测可靠，优先覆盖
                if details.get("published"):
                    result["published"] = details["published"]

        # Build evidence from web results
        web_evidence = build_web_evidence(state["project_id"], all_web_results, db)
        web_count = len(web_evidence)
        db.flush()
        add_log(task_id, f"web evidence cards created: {web_count}")

    except Exception:
        logger.exception("Web sources gathering failed (non-fatal)")
        add_log(task_id, "web sources gathering failed (non-fatal)")

    return {"web_evidence_count": web_count}


# ── Node 3c: gather_community_and_llm ──────────────────────────


def community_sources_node(state: WorkflowState) -> dict[str, Any]:
    """Gather evidence from community forums and LLM knowledge."""
    task_id = state["task_id"]
    db = state["db"]
    project = state["project"]
    query = state["query"] or project.research_question or project.title

    add_log(task_id, "langgraph: gather_community_and_llm_sources")
    set_progress(task_id, 70, "gathering community & LLM sources")

    community_count = 0
    llm_count = 0

    try:
        # Search Reddit for expert discussions
        reddit_results = search_reddit(query, max_results=6)
        add_log(task_id, f"Reddit search returned {len(reddit_results)} results")

        # Search Zhihu for Chinese expert discussions
        zhihu_results = search_zhihu(query, max_results=4)
        add_log(task_id, f"Zhihu search returned {len(zhihu_results)} results")

        # Combine and build evidence
        all_community = reddit_results + zhihu_results
        if all_community:
            community_evidence = build_community_evidence(state["project_id"], all_community, db)
            community_count = len(community_evidence)

        # Generate LLM knowledge — scale depth based on existing evidence count
        existing_count = state.get("evidence_count", 0) + community_count
        # Check how many existing cards are actually relevant (have source_type set)
        topic_assessment = state.get("topic_assessment", {})
        topic_type = topic_assessment.get("topic_type", "general")
        # web 优先（时效话题）时论文只作背景，模型知识是正文主力之一 → 加深
        web_priority_effective = bool(topic_assessment.get("web_priority_effective"))
        # For product-type topics or sparse evidence, generate more LLM knowledge
        knowledge_depth = (
            "deep"
            if (existing_count < 8 or topic_type == "product" or web_priority_effective)
            else "standard"
        )
        llm_results = generate_llm_knowledge(
            project.title, project.research_question, existing_count,
            depth=knowledge_depth,
        )
        add_log(task_id, f"LLM knowledge generated: {len(llm_results)} points")

        if llm_results:
            llm_evidence = build_community_evidence(
                state["project_id"],
                [{**r, "source_type": "llm_knowledge", "community_platform": "llm_knowledge"} for r in llm_results],
                db,
            )
            # Override source_type for LLM evidence
            for ev in llm_evidence:
                ev.source_type = "llm_knowledge"
            llm_count = len(llm_evidence)

        db.flush()
        add_log(task_id, f"community evidence: {community_count}, LLM knowledge: {llm_count}")

    except Exception:
        logger.exception("Community sources gathering failed (non-fatal)")
        add_log(task_id, "community sources gathering failed (non-fatal)")

    # Reload all cards including the new multi-source ones
    cards = list(db.scalars(
        select(EvidenceCard).where(EvidenceCard.project_id == state["project_id"])
    ).all())

    # ── 全来源证据为空 → 显式失败（恢复错误契约）─────────────────
    # 学术 + web + 社区/LLM 三路证据全部为 0 才是真正的“无据可写”；
    # 任一来源有证据都继续成文（web-only 文章是受支持的产品形态），
    # 只有全空时按 NO_EVIDENCE_CARDS 返回 400，摘要与旧 runner 对齐。
    if not cards:
        add_log(task_id, "FATAL: no evidence cards from any source (academic/web/community)")
        skipped_titles = [
            d["title"] for d in state.get("paper_diagnostics", []) if d.get("status") == "skipped_no_pdf"
        ][:6]
        failed_items = [
            {"title": d.get("title"), "error": d.get("error")}
            for d in state.get("paper_diagnostics", []) if d.get("status") == "failed"
        ][:6]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NO_EVIDENCE_CARDS",
                "title": "No evidence cards were generated",
                "message": "No evidence could be built from academic papers, web or community sources.",
                "summary": {
                    "selected_count": len(state.get("selected_papers", [])),
                    "reused_local_pdf_count": state.get("reused_local_pdf_count", 0),
                    "downloaded_count": state.get("downloaded_count", 0),
                    "parsed_count": state.get("parsed_count", 0),
                    "skipped_no_pdf_count": state.get("skipped_no_pdf_count", 0),
                    "failed_count": state.get("failed_count", 0),
                    "evidence_count": 0,
                    "metadata_fallback_evidence_count": state.get("metadata_fallback_evidence_count", 0),
                    "low_relevance_filtered_count": state.get("low_relevance_filtered_count", 0),
                    "web_evidence_count": 0,
                    "community_evidence_count": 0,
                    "llm_knowledge_count": 0,
                },
                "skipped_titles": skipped_titles,
                "failed_items": failed_items,
                "paper_diagnostics": state.get("paper_diagnostics", [])[:20],
                "next_actions": ["Upload a local PDF or refine the search query."],
            },
        )

    return {
        "community_evidence_count": community_count,
        "llm_knowledge_count": llm_count,
        "cards": cards,
    }


# ── Node 3d: relevance_filter ──────────────────────────────────


def relevance_filter_node(state: WorkflowState) -> dict[str, Any]:
    """Use LLM to filter evidence cards, removing those irrelevant to the research question.

    This is the key quality gate: only evidence directly related to the research
    question passes through to the writing stage.
    """
    task_id = state["task_id"]
    db = state["db"]
    project = state["project"]
    cards = state.get("cards", [])
    query = state["query"] or project.research_question or project.title

    add_log(task_id, "langgraph: relevance_filter")
    set_progress(task_id, 74, "filtering evidence for relevance")

    if not cards or len(cards) < 2:
        add_log(task_id, f"relevance filter skipped: only {len(cards)} cards")
        return {"cards": cards, "low_relevance_filtered_count": 0}

    from app.services.llm_service import chat_completion
    import json as _json

    # Build batch evaluation prompt (evaluate all cards at once for efficiency)
    card_summaries = []
    for i, card in enumerate(cards):
        claim = (card.claim or "")[:200]
        support = (card.supporting_text or "")[:200]
        source = card.source_type or "academic"
        card_summaries.append(f"[{i}] source={source} claim: {claim} | supporting: {support}")

    system_prompt = (
        "你是一位学术内容质量审核专家。请评估每条证据卡是否与给定的研究问题直接相关。\n"
        "判断标准：\n"
        "- 直接相关：证据内容直接讨论了研究问题的核心主题\n"
        "- 间接相关：证据涉及研究问题的某个侧面或提供了有用的背景\n"
        "- 不相关：证据内容与研究问题的核心主题没有实质联系\n\n"
        "来源可信度分级（S3）：academic=学术论文 1.0，web=网络来源 0.5，"
        "community=社区讨论 0.3，llm_knowledge=背景知识 0.2。\n"
        "- 低可信度来源（web/community/llm_knowledge）必须从严把关："
        "只有直接支撑研究问题核心主张的内容才算 relevant，边缘相关的判为 irrelevant\n"
        "- 学术来源可放宽：间接相关即可判为 relevant\n\n"
        "请对每条证据卡输出 relevant（直接或间接相关）或 irrelevant（不相关）。"
    )

    user_prompt = (
        f"研究主题：{project.title}\n"
        f"研究问题：{query}\n\n"
        f"以下是 {len(cards)} 条证据卡，请逐条判断相关性：\n\n"
        + "\n".join(card_summaries)
        + "\n\n请以 JSON 数组格式输出每条证据卡的判断，格式：\n"
        '[{"index": 0, "relevance": "relevant"}, {"index": 1, "relevance": "irrelevant"}, ...]\n'
        "只输出 JSON 数组，不要输出其他内容。"
    )

    relevant_indices: set[int] = set()
    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=2048,
            timeout=60.0,
        )
        text = result.get("content", "").strip()
        if text:
            # Extract JSON from possible code fences
            if "```" in text:
                import re
                match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
                if match:
                    text = match.group(1)

            judgments = _json.loads(text)
            for j in judgments:
                idx = j.get("index", -1)
                rel = j.get("relevance", "irrelevant")
                if 0 <= idx < len(cards) and rel == "relevant":
                    relevant_indices.add(idx)

            add_log(task_id, f"relevance filter: {len(relevant_indices)}/{len(cards)} cards passed")
    except Exception:
        logger.exception("Relevance filter LLM call failed")
        add_log(task_id, "relevance filter LLM failed, using keyword fallback")
        # ── Keyword-based fallback ───────────────────────────────────────
        # Extract key terms from the research question and filter cards
        # that contain at least one meaningful keyword match.
        import re as _re
        topic_terms = [
            t.lower() for t in _re.split(r'[\s,，。？！、""''（）]+', query)
            if len(t) >= 2 and t.lower() not in {
                "the", "and", "for", "with", "from", "that", "this",
                "what", "how", "why", "which", "关于", "的", "了", "是",
                "在", "有", "和", "与", "对", "为", "从", "等",
            }
        ]
        # Also add project title terms
        for t in _re.split(r'[\s,，。？！、""''（）]+', project.title or ""):
            if len(t) >= 2:
                topic_terms.append(t.lower())
        topic_terms = list(set(topic_terms))

        if topic_terms:
            from app.services.evidence_service import credibility_weight

            for i, card in enumerate(cards):
                text = ((card.claim or "") + " " + (card.supporting_text or "")).lower()
                hits = sum(1 for term in topic_terms if term in text)
                # S3: low-credibility sources need stronger evidence of relevance.
                cred = credibility_weight(card.source_type, bool(getattr(card, "paper", None) and card.paper.doi))
                min_hits = 1 if cred >= 0.5 else 2
                if hits >= min_hits:
                    relevant_indices.add(i)
            add_log(task_id, f"keyword fallback: {len(relevant_indices)}/{len(cards)} cards matched terms={topic_terms[:8]}")
        else:
            add_log(task_id, "keyword fallback: no meaningful terms extracted, keeping all cards")
            relevant_indices = set(range(len(cards)))

    if not relevant_indices:
        add_log(task_id, "relevance filter rejected all cards — using keyword fallback to keep best matches")
        # Fallback: keyword-based scoring to keep the most relevant cards
        import re as _re
        topic_terms = [
            t.lower() for t in _re.split(r'[\s,，。？！、""''（）]+', (query or "") + " " + (project.title or ""))
            if len(t) >= 2 and t.lower() not in {
                "the", "and", "for", "with", "from", "that", "this",
                "what", "how", "why", "which", "关于", "的", "了", "是",
                "在", "有", "和", "与", "对", "为", "从", "等",
            }
        ]
        scored: list[tuple[int, int]] = []
        for i, card in enumerate(cards):
            text = ((card.claim or "") + " " + (card.supporting_text or "")).lower()
            hits = sum(1 for term in topic_terms if term in text)
            scored.append((hits, i))
        scored.sort(reverse=True)
        # Keep top 5 scoring cards (or fewer if fewer exist)
        relevant_indices = set(idx for _, idx in scored[:min(5, len(scored))] if _ > 0)
        if not relevant_indices:
            # Last resort: keep top 3 by position
            relevant_indices = set(idx for _, idx in scored[:min(3, len(scored))])

    filtered_cards = [cards[i] for i in sorted(relevant_indices)]
    filtered_count = len(cards) - len(filtered_cards)

    # ── Cross-card dedup (批次7) ──
    # The same finding restated by several chunks/papers enters the pool as
    # multiple cards and crowds out diverse evidence under the per-section
    # bucket cap; merge near-duplicates before writing.
    from app.services.evidence_service import dedupe_evidence_cards

    filtered_cards, deduped_count = dedupe_evidence_cards(filtered_cards)
    if deduped_count:
        add_log(task_id, f"dedup: dropped {deduped_count} near-duplicate evidence cards")
        filtered_count += deduped_count

    if filtered_count > 0:
        add_log(task_id, f"relevance filter removed {filtered_count} irrelevant cards")

    return {
        "cards": filtered_cards,
        "low_relevance_filtered_count": state.get("low_relevance_filtered_count", 0) + filtered_count,
    }


# ── Node 3d: conflict_detection (S4) ────────────────────────────


def conflict_detection_node(state: WorkflowState) -> dict[str, Any]:
    """Group evidence claims that contradict each other (S4).

    Runs right after relevance filtering. Conflicting cards are grouped by
    ``group_id``; the writing prompt then forces critical comparison instead
    of picking one side, and the reviewer flags drafts that cite both sides
    without discussing the conflict.
    """
    task_id = state["task_id"]
    project = state["project"]
    cards = state.get("cards", [])

    set_progress(task_id, 75, "detecting conflicting evidence")
    from app.services.evidence_service import detect_conflict_groups

    groups = detect_conflict_groups(
        cards=[_evidence_to_dict(c) for c in cards],
        research_question=project.research_question or project.title,
    )
    if groups:
        add_log(
            task_id,
            "conflict groups: "
            + "; ".join(f"{g['group_id']}({', '.join(g['card_ids'])})" for g in groups),
        )
    else:
        add_log(task_id, "conflict detection: no conflicting evidence found")
    return {"conflict_groups": groups}


# ── Node 3e: topic_feasibility_assessment ───────────────────────


def topic_assessment_node(state: WorkflowState) -> dict[str, Any]:
    """Quick assessment of whether the topic has enough searchable material.

    Runs before the heavy pipeline to fail fast if the topic is too niche.
    """
    task_id = state["task_id"]
    db = state["db"]

    # Load project directly since this runs before search_node
    project = _get_project_or_404(state["project_id"], db)
    query = (state["payload"].query or project.research_question or project.title or "").strip()

    add_log(task_id, "langgraph: topic_feasibility_assessment")

    from app.services.llm_service import chat_completion

    system_prompt = (
        "你是一位学术信息检索顾问。请快速评估一个研究主题是否有足够的公开可检索资料。"
    )
    user_prompt = (
        f"研究主题：{query}\n\n"
        f"请评估以下内容，以 JSON 格式输出：\n"
        f'{{"feasibility": "high/medium/low", "reason": "简要原因", '
        f'"suggested_queries": ["建议的搜索查询1", "建议的搜索查询2"], '
        f'"topic_type": "product/academic/policy/general", '
        f'"web_priority": true/false}}\n\n'
        f"评估标准：\n"
        f"- high: 有大量学术论文、技术博客或官方文档\n"
        f"- medium: 有一些资料但可能不够全面\n"
        f"- low: 非常小众的话题，公开资料很少\n\n"
        f"topic_type 判断：\n"
        f"- product: 涉及具体产品、工具或服务\n"
        f"- academic: 学术理论或方法\n"
        f"- policy: 政策分析或社会治理\n"
        f"- general: 通用知识话题\n\n"
        f"web_priority: 如果该话题具有强时效性（新闻事件、产品发布/更新、价格变动、"
        f"行业动态等——最新信息主要存在于网页/新闻而非学术论文中），设为 true。\n\n"
        f"只输出 JSON，不要输出其他内容。"
    )

    assessment = {
        "feasibility": "medium",
        "reason": "Assessment failed, proceeding with default pipeline",
        "topic_type": "general",
        "web_priority": False,
    }

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=512,
            timeout=20.0,
        )
        text = result.get("content", "").strip()
        if text:
            if "```" in text:
                import re
                match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
                if match:
                    text = match.group(1)
            import json as _json
            parsed = _json.loads(text)
            assessment.update(parsed)
    except Exception:
        logger.debug("Topic assessment failed (non-fatal)", exc_info=True)

    feasibility = assessment.get("feasibility", "medium")
    topic_type = assessment.get("topic_type", "general")
    web_priority = assessment.get("web_priority", False)

    # ── 双保险证据策略（时效话题）─────────────────────────────────
    # 话题评估判定时效性，或文章类型本身就是公众号文章（天然时效导向），
    # 都切换为 web 优先：web/社区证据作正文主力，论文只作背景参考。
    web_priority_effective = bool(web_priority or project.article_type == "wechat_article")
    assessment["web_priority_effective"] = web_priority_effective

    add_log(
        task_id,
        f"topic assessment: feasibility={feasibility}, type={topic_type}, "
        f"web_priority={web_priority}, web_priority_effective={web_priority_effective}",
    )

    if feasibility == "low":
        add_log(task_id, f"WARNING: topic '{query[:50]}' assessed as low feasibility — limited public material available")

    if web_priority_effective:
        add_log(
            task_id,
            "evidence strategy: WEB PRIORITY — web/community sources lead the body; "
            "academic papers are demoted to background references",
        )

    return {
        "topic_assessment": assessment,
    }


# ── Node 4: generate_draft ───────────────────────────────────────


def draft_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    payload = state["payload"]
    project = state["project"]
    cards = state["cards"]

    set_progress(task_id, 78, "generating draft")
    content_md, draft_sections = build_draft_markdown(
        project_title=payload.draft_title or project.title,
        research_question=project.research_question,
        article_type=project.article_type,
        citation_style=project.citation_style,
        evidence_cards=[_evidence_to_dict(c) for c in cards],
        thesis_statement=state.get("thesis_statement", ""),
        sections=state.get("draft_sections") or None,
        figure_plans=state.get("figure_plans") or None,
        conflict_groups=state.get("conflict_groups") or None,
        papers_off_topic=bool(state.get("papers_off_topic", False)),
    )
    draft = Draft(
        id=str(uuid4()),
        project_id=state["project_id"],
        version=_next_draft_version(state["project_id"], db),
        title=payload.draft_title or f"{project.title} Draft",
        content_md=content_md,
        status="draft",
        quality_score={"overall_score": 0.75},
        created_at=_now(),
    )
    db.add(draft)
    db.flush()
    # 增量提交：草稿一旦生成立即落盘，进程被杀也能从 drafts 表取回。
    draft_id, draft_version = draft.id, draft.version
    db.commit()
    set_artifact(task_id, "draft_id", draft_id)
    set_artifact(task_id, "draft_version", draft_version)
    add_log(task_id, f"draft generated with {len(draft_sections)} topic-specific sections: {draft_sections}")
    return {"draft": draft, "current_content": draft.content_md, "draft_sections": draft_sections}


# ── Node 4a: thesis_thread (W1) ────────────────────────────────


def thesis_thread_node(state: WorkflowState) -> dict[str, Any]:
    """Distill the article's argument line before drafting (W1).

    Produces a 3-5 sentence thesis (core claim + evidence pillars + expected
    conclusion) that every section writes toward, so the draft reads as one
    argument instead of stitched-together paragraphs.
    """
    task_id = state["task_id"]
    project = state["project"]
    payload = state["payload"]
    cards = state.get("cards", [])

    set_progress(task_id, 77, "distilling thesis thread")
    thesis = build_thesis_statement(
        project_title=payload.draft_title or project.title,
        research_question=project.research_question,
        article_type=project.article_type,
        evidence_cards=[_evidence_to_dict(c) for c in cards],
    )
    # Section headings are also planned at outline stage (before drafting) so
    # plan_figures (F1) and the writing prompt share one section list.
    sections = plan_article_sections(
        article_type=project.article_type,
        project_title=payload.draft_title or project.title,
        research_question=project.research_question,
        evidence_cards=[_evidence_to_dict(c) for c in cards],
    )
    add_log(task_id, f"thesis thread: {thesis[:100]}")
    return {"thesis_statement": thesis, "draft_sections": sections}


# ── Node 4b: plan_figures (F1) ─────────────────────────────────


def plan_figures_node(state: WorkflowState) -> dict[str, Any]:
    """Plan which figure each section needs, grounded in real evidence (F1).

    Runs at outline stage (after thesis_thread, before drafting) so the writing
    prompt can reference planned figures via ``{{ref:fig:N}}`` placeholders and
    image generation executes the plan instead of random placement.
    """
    task_id = state["task_id"]
    sections = state.get("draft_sections") or []
    cards = state.get("cards", [])

    set_progress(task_id, 78, "planning figures per section")
    from app.services.image_service import plan_figures

    plans = plan_figures(
        sections=sections,
        evidence_cards=[_evidence_to_dict(c) for c in cards],
    )
    add_log(
        task_id,
        "planned "
        + f"{len(plans)} figures: "
        + "; ".join(f"fig{p['fig_index']}->{p['section'][:14]}" for p in plans),
    )
    return {"figure_plans": plans}


# ── Node 4c: generate_images ───────────────────────────────────


def image_generation_node(state: WorkflowState) -> dict[str, Any]:
    """Generate illustrations for the article.

    Combines three image sources for a professional WeChat-style article:
    1. **Extracted figures** — real paper figures/tables from PDF (PyMuPDF)
    2. **Data-driven charts** — benchmark/result charts from evidence data (matplotlib)
    3. **Social proof cards** — GitHub stars, citations, HuggingFace (SVG cards)
    4. **Decorative illustrations** — Pollinations.ai + SVG template fallback (existing)
    """
    task_id = state["task_id"]
    project = state["project"]
    draft = state["draft"]
    papers = state.get("selected_papers", [])
    cards = state.get("cards", [])
    extracted_figures = state.get("extracted_figures", [])

    add_log(task_id, "langgraph: generate_images (multi-source)")
    set_progress(task_id, 80, "generating article illustrations")

    from app.services.image_service import (
        finalize_figures,
        generate_article_images,
        inject_images_into_markdown,
        resolve_section_key,
    )
    from app.services.writing_service import _article_sections
    from app.services.chart_service import generate_charts_from_evidence
    from app.services.social_proof_service import generate_social_proof_cards
    from app.services.figure_extraction_service import (
        _CATEGORY_SECTION,
        select_best_figures,
        tag_figures_with_categories,
    )

    # Use actual draft sections (topic-specific) if available, otherwise fallback
    sections = state.get("draft_sections") or _article_sections(project.article_type)
    all_images: list[dict[str, str]] = []

    # ── 1. Extracted paper figures (smart-selected) ──────────────
    # 流程：先语义打标（产出 description）→ 双层准入 → 择优。
    # 图表准入双层判定：
    #   a) 单篇论文标题与主题相关（≥2 个内容词命中）；
    #   b) 图自身的描述与主题相关（≥1 个内容词命中）。
    # 只靠 (a) 不够：标题里 "AI Agent" 双命中的论文，其插图可能是
    # "材料发现工作台"这类与主题无关的图——(b) 把这种漏网拦下。
    # 注意顺序：description 由 tag_figures_with_categories 产出，
    # 必须先打标再判定，否则 (b) 氡图可判。
    if extracted_figures:
        from app.services.search_service import title_query_hits

        # F5: tag figures semantically first so selection and section mapping
        # are driven by content type, not just geometry.
        tag_figures_with_categories(extracted_figures, project.title)

        _rq = (project.research_question or state.get("query") or "").strip()
        before_count = len(extracted_figures)
        topical_figures = [
            fig for fig in extracted_figures
            if title_query_hits(str(fig.get("paper_title") or ""), _rq) >= 2
            and title_query_hits(str(fig.get("description") or ""), _rq) >= 1
        ]
        if len(topical_figures) < before_count:
            add_log(
                task_id,
                f"figure admission: dropped {before_count - len(topical_figures)} figures "
                f"from off-topic papers or with off-topic content "
                f"(kept {len(topical_figures)})",
            )
        extracted_figures = topical_figures
    if extracted_figures:
        best_figures = select_best_figures(extracted_figures, max_count=8)
        # 参考文献页/致谢页截图永远不是"配图"：内容是引用列表或致谢文本，
        # 与任何主题都无关，只会让读者困惑。按 alt 描述与页码特征过滤。
        _REF_PAGE_PATTERNS = ("参考文献", "references", "bibliography", "致谢", "acknowledg")
        admittable: list[dict[str, Any]] = []
        for fig in best_figures:
            desc = str(fig.get("description") or "").lower()
            alt = str(fig.get("alt") or "")
            paper_title = str(fig.get("paper_title") or "")
            # 末页整页渲染 + 描述/标题命中参考文献特征 → 丢弃
            if fig.get("source") == "page_render" and fig.get("page", 0) >= 10:
                combined = f"{desc}{alt}{paper_title}".lower()
                if any(p in combined for p in _REF_PAGE_PATTERNS):
                    continue
            admittable.append(fig)
        if len(admittable) < len(best_figures):
            add_log(
                task_id,
                f"figure admission: dropped {len(best_figures) - len(admittable)} "
                f"reference-page screenshots",
            )
        best_figures = admittable
        for fig in best_figures:
            fig_path = fig.get("path", "")
            if not fig_path:
                continue
            source = fig.get("source", "embedded")
            page = fig.get("page", 0)
            aspect = fig.get("width", 1) / max(fig.get("height", 1), 1)

            # Smart section assignment: semantic category wins when tagged,
            # source/page/aspect heuristics remain the fallback.
            category = fig.get("category", "")
            if category in _CATEGORY_SECTION:
                section = _CATEGORY_SECTION[category]
            elif source == "page_render" and page == 1:
                section = "Background"      # title/overview page
            elif source == "page_render":
                section = "Results"         # rendered table pages
            elif page <= 3:
                section = "Framework"       # architecture diagrams
            elif aspect > 1.5:
                section = "Results"         # wide = result tables/benchmarks
            elif page >= 8:
                section = "Results"         # late pages = experiments
            else:
                section = "Framework"       # method illustrations

            alt = fig.get("description") or f"论文配图（第{page}页）"
            all_images.append({
                "path": fig_path,
                "alt": alt[:80],
                "section": section,
                "source": "extracted_figure",
            })
        add_log(task_id, f"included {len([i for i in all_images if i.get('source') == 'extracted_figure'])} best paper figures (selected from {len(extracted_figures)})")

    # ── 2. Data-driven charts from evidence cards ──────────────────
    try:
        chart_dir = backend_dir / "data" / "storage" / state["project_id"] / "images" / "charts"
        chart_images = generate_charts_from_evidence(
            cards=cards,
            project_id=state["project_id"],
            project_title=project.title or "",
            output_dir=chart_dir,
        )
        all_images.extend(chart_images)
        add_log(task_id, f"generated {len(chart_images)} data-driven charts")
    except Exception:
        logger.exception("Chart generation failed (non-fatal)")
        add_log(task_id, "chart generation failed (non-fatal)")

    # ── 3. Social proof cards ──────────────────────────────────────
    try:
        social_dir = backend_dir / "data" / "storage" / state["project_id"] / "images" / "social"
        social_images = generate_social_proof_cards(
            papers=papers,
            project_id=state["project_id"],
            output_dir=social_dir,
            research_question=(project.research_question or state.get("query") or "").strip(),
        )
        all_images.extend(social_images)
        add_log(task_id, f"generated {len(social_images)} social proof cards")
    except Exception:
        logger.exception("Social proof generation failed (non-fatal)")
        add_log(task_id, "social proof generation failed (non-fatal)")

    # ── 4. Decorative illustrations (existing Pollinations + SVG) ──
    try:
        kind_by_section = {
            p.get("section", ""): p.get("kind", "")
            for p in (state.get("figure_plans") or [])
        }
        # Sections that already carry a matplotlib data chart skip the SVG
        # dashboard pass - the same metrics must not be visualized twice.
        chart_covered_sections = {img.get("section", "") for img in all_images if img.get("source") == "chart"}
        skip_sections = {resolve_section_key(s) for s in chart_covered_sections}
        # 证据语境的图注随 prompt 下发：生图模型画"这节要表达的内容"，
        # 而不是仅凭章节标题的泛泛概念图
        caption_by_section = {
            str(p.get("section", "")): str(p.get("caption", ""))
            for p in (state.get("figure_plans") or [])
            if p.get("caption")
        }
        decorative_images = generate_article_images(
            project_id=state["project_id"],
            project_title=project.title,
            research_question=project.research_question,
            sections=sections,
            article_type=project.article_type,
            draft_content=draft.content_md,
            evidence_cards=[_evidence_to_dict(c) for c in cards],
            kind_by_section=kind_by_section,
            skip_sections=skip_sections,
            caption_by_section=caption_by_section,
        )
        # When we have real evidence images, reduce decorative images
        if len(all_images) >= 3:
            decorative_images = decorative_images[:2]  # keep max 2 decorative
        all_images.extend(decorative_images)
        add_log(task_id, f"added {len(decorative_images)} decorative illustrations")
    except Exception:
        logger.exception("Decorative image generation failed (non-fatal)")
        add_log(task_id, "decorative image generation failed (non-fatal)")

    # ── Inject all images into markdown (F1/F3) ────────────────────
    # Attach plan captions to generated images whose section matches a planned
    # figure, so the caption comes from the evidence-grounded plan. Extracted
    # paper figures are excluded on purpose: the plan describes the figure we
    # *want*, not the figure we *have* — attaching it to an arbitrary extracted
    # image produces confident-looking but wrong captions. Extracted figures
    # keep their own vision/description alt text as the caption.
    # Plans key on LLM-generated topical headings ("实验结果与分析") while
    # images carry hardcoded English tags ("Results") - exact matching almost
    # always missed, leaving charts caption-less and ref_key-less (floating
    # figures). Join through the canonical section keys instead.
    plan_by_canon: dict[str, dict[str, Any]] = {}
    for p in (state.get("figure_plans") or []):
        plan_by_canon.setdefault(resolve_section_key(p.get("section", "")), p)
    for img in all_images:
        plan = plan_by_canon.get(resolve_section_key(img.get("section", "")))
        if plan and img.get("source") != "extracted_figure":
            # Attach the evidence-grounded caption and the plan's ref_key so
            # cross-references resolve to the *actual* figure number after
            # injection (extracted/social images shift the running count).
            if not img.get("caption"):
                img["caption"] = plan.get("caption", "")
            if not img.get("ref_key"):
                img["ref_key"] = plan.get("ref_key", "")

    # Inject images first, then number + resolve cross-refs in a single pass.
    # The ref_key -> figure-number map is only knowable after injection, so
    # placeholders must survive until images are in place; finalize resolves
    # them (falling back to the plan index) even when no images were produced.
    if all_images:
        draft.content_md = inject_images_into_markdown(draft.content_md, all_images)
        draft.content_md = finalize_figures(draft.content_md, all_images)
        state["db"].flush()
        add_log(task_id, f"injected {len(all_images)} total illustrations into draft")
    else:
        draft.content_md = finalize_figures(draft.content_md, all_images)

    # L2: record each figure's evidence dependency so the revise loop can
    # detect sections whose evidence changed and re-sync figure captions.
    deps: list[dict[str, Any]] = []
    if all_images:
        from app.services.image_service import _extract_section_content
        from app.services.writing_service import _cited_evidence_ids

        for img in all_images:
            sec = img.get("section", "")
            sec_text = _extract_section_content(draft.content_md, sec)
            ids = sorted(_cited_evidence_ids(sec_text))
            plan = plan_by_canon.get(resolve_section_key(sec))
            if plan and plan.get("evidence_id"):
                ids = sorted(set(ids) | {str(plan["evidence_id"])})
            deps.append(
                {"path": img.get("path", ""), "section": sec, "evidence_ids": ids}
            )

    # 增量提交：带图草稿落盘，崩溃后 drafts 表保留最新带图版本。
    draft_id, draft_version = draft.id, draft.version
    state["db"].commit()
    set_artifact(task_id, "draft_id", draft_id)
    set_artifact(task_id, "draft_version", draft_version)
    return {
        "generated_images": all_images,
        "figure_deps": deps,
        "current_content": draft.content_md,
    }


# ── Node 5b: refresh_figures (L2) ────────────────────────────────


def refresh_figures_node(state: WorkflowState) -> dict[str, Any]:
    """L2: re-sync figures whose evidence dependency changed after a revision.

    Each figure records the evidence ids cited in its section at generation
    time (``figure_deps``). After a revise round, sections whose cited evidence
    changed get their figure captions/alt refreshed from the current text.
    Idempotent: existing ``**图N：**`` captions are replaced in place, so figure
    numbers stay stable across revision rounds. Image bytes are left untouched
    because charts are derived from evidence cards, which do not change inside
    the revise loop.
    """
    task_id = state["task_id"]
    current = state.get("current_content", "")
    images = state.get("generated_images", [])
    deps = state.get("figure_deps", [])
    if not deps or not images or not current:
        return {}

    from app.services.image_service import _extract_section_content, finalize_figures
    from app.services.writing_service import _cited_evidence_ids

    plan_by_section = {
        p.get("section", ""): p for p in (state.get("figure_plans") or [])
    }

    changed_sections: set[str] = set()
    for dep in deps:
        sec = dep.get("section", "")
        if not sec:
            continue
        current_ids = sorted(
            _cited_evidence_ids(_extract_section_content(current, sec))
        )
        if current_ids != (dep.get("evidence_ids") or []):
            changed_sections.add(sec)

    if not changed_sections:
        add_log(task_id, "figure refresh: no evidence changes, figures stay in sync")
        return {}

    # Refresh captions for images in affected sections. Planned figures keep
    # their evidence-derived caption; others fall back to the section's first
    # sentence so captions always reflect the revised text. Extracted paper
    # figures are skipped: their caption describes the actual image content
    # (vision-tagged), which does not change with the text.
    for img in images:
        if img.get("section") not in changed_sections:
            continue
        if img.get("source") == "extracted_figure":
            continue
        plan = plan_by_section.get(img.get("section", ""))
        if plan and plan.get("caption"):
            img["caption"] = plan["caption"]
            img["alt"] = plan["caption"]
        else:
            sec_text = _extract_section_content(current, img.get("section", ""))
            first_sentence = (
                re.sub(r"\s+", " ", sec_text.split("。", 1)[0]).strip()
                if sec_text
                else ""
            )
            if first_sentence:
                img["caption"] = first_sentence[:60]
                img["alt"] = first_sentence[:60]

    refreshed = finalize_figures(current, images)
    add_log(
        task_id,
        f"figure refresh: {len(changed_sections)} section(s) evidence changed "
        f"({sorted(changed_sections)}), captions re-synced",
    )
    return {"current_content": refreshed}


# ── Node 5: initial_review ───────────────────────────────────────


def initial_review_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    project = state["project"]
    draft = state["draft"]
    cards = state["cards"]

    set_progress(task_id, 86, "reviewing draft (multi-agent debate)")

    # S4: tag cards with their conflict group so the reviewer can flag drafts
    # that cite both sides of a conflict without comparing them.
    group_by_card_id: dict[str, str] = {}
    for group in state.get("conflict_groups") or []:
        for cid in group.get("card_ids", []):
            group_by_card_id[str(cid)] = group.get("group_id", "")
    review_cards = []
    for c in cards:
        card_dict = _evidence_to_dict(c)
        card_dict["conflict_group"] = group_by_card_id.get(str(c.id), "")
        review_cards.append(card_dict)

    review_payloads, review_metrics = debate_review_with_metrics(
        draft.content_md,
        evidence_cards=review_cards,
        article_type=project.article_type,
        task_id=task_id,
    )

    # ── Surface the debate thinking process in the task log ──────
    debate_log = review_metrics.get("debate_log", [])
    for entry in debate_log:
        role = entry.get("role", "")
        phase = entry.get("phase", "")
        if role:
            count = entry.get("count", 0)
            types = entry.get("issue_types") or entry.get("challenge_types", [])
            role_label = {
                "evidence_reviewer": "证据审查员",
                "logic_reviewer": "逻辑审查员",
                "challenger": "对抗性质疑者",
            }.get(role, role)
            add_log(task_id, f"[辩论] {role_label}（{phase}）发现 {count} 个问题：{', '.join(types)}")
        elif phase == "cross_review":
            ev_sup = entry.get("evidence_supplements", 0)
            lg_sup = entry.get("logic_supplements", 0)
            add_log(task_id, f"[辩论] 交叉审查：证据补充 {ev_sup}，逻辑补充 {lg_sup}")
        elif phase == "consolidated":
            total = entry.get("total", 0)
            consensus = entry.get("consensus_count", 0)
            disputed = entry.get("disputed_count", 0)
            add_log(task_id, f"[辩论] 最终合并：共 {total} 个问题，{consensus} 个共识，{disputed} 个争议")

    overall = float(review_metrics.get("overall_score") or 0.0)
    add_log(task_id, f"[辩论] 综合评分：{overall:.3f} | 证据覆盖：{review_metrics.get('evidence_coverage', 0):.2f} | 逻辑：{review_metrics.get('logic_score', 0):.2f} | 表达：{review_metrics.get('style_score', 0):.2f}")

    db.execute(delete(ReviewIssue).where(ReviewIssue.project_id == state["project_id"]))
    created: list[ReviewIssue] = []
    for p in review_payloads:
        issue = ReviewIssue(
            id=str(uuid4()), project_id=state["project_id"], draft_id=draft.id,
            severity=p["severity"], issue_type=p["issue_type"], location=p["location"],
            claim=p["claim"], description=p["description"], suggestion=p["suggestion"],
            evidence_ids=p.get("evidence_ids", []), resolved=False, created_at=_now(),
        )
        db.add(issue)
        created.append(issue)

    critical = len([i for i in created if i.severity == "high"])
    draft.status = "reviewed"
    draft.quality_score = score_quality(len(created), critical, metrics=review_metrics)

    overall = float(review_metrics.get("overall_score") or 0.0)
    return {
        "current_issues": review_payloads,
        "current_metrics": review_metrics,
        "review_rounds": [{"round": 0, "stage": "initial_review", "metrics": dict(review_metrics)}],
        "revision_round": 0,
        "stagnant_rounds": 0,
        "previous_overall": overall,
        "best_score": overall,
        "best_content": draft.content_md,
        "best_issues": review_payloads,
        "best_metrics": review_metrics,
        "created_issues": created,
    }


# ── Node 6: revise ───────────────────────────────────────────────


def revise_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    # Regression rollback: if the last revision scored below the best version,
    # keep revising from best_content instead of compounding edits on a worse
    # draft -- otherwise later rounds burn their budget degrading it further.
    base_content = state["current_content"]
    current_score = float((state.get("current_metrics") or {}).get("overall_score") or 0.0)
    best_score = float(state.get("best_score") or 0.0)
    if state.get("best_content") and best_score > current_score + 1e-9:
        base_content = state["best_content"]
        add_log(
            task_id,
            f"last revision regressed ({current_score:.3f} < best {best_score:.3f}); "
            f"revising from best-scoring content instead",
        )
    # Pass the FULL review issues (description / suggestion / claim /
    # evidence_ids) to the reviser. The old code truncated to
    # issue_type/severity/location, which left _llm_revise_paragraph with
    # `description=None, suggestion=None` -- the reviser literally did not
    # know what was wrong, so the 3-round review↔revise loop was blind.
    revised = revise_draft(
        base_content,
        issues=[
            {
                "issue_type": str(i.get("issue_type") or ""),
                "severity": str(i.get("severity") or ""),
                "location": str(i.get("location") or ""),
                "claim": str(i.get("claim") or ""),
                "description": str(i.get("description") or ""),
                "suggestion": str(i.get("suggestion") or ""),
                "evidence_ids": list(i.get("evidence_ids") or []),
            }
            for i in state["current_issues"]
        ],
    )
    add_log(task_id, f"revised draft (round {state['revision_round'] + 1})")
    return {"current_content": revised}


# ── Node 7: review ───────────────────────────────────────────────


def review_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    project = state["project"]
    cards = state["cards"]

    set_progress(task_id, min(96, 90 + (state["revision_round"] + 1) * 2),
                 f"reviewing revision round {state['revision_round'] + 1} (multi-agent debate)")

    revised_issues, revised_metrics = debate_review_with_metrics(
        state["current_content"],
        evidence_cards=[_evidence_to_dict(c) for c in cards],
        article_type=project.article_type,
        task_id=task_id,
    )

    # ── Surface debate thinking process in the task log ──────────
    round_num = state["revision_round"] + 1
    debate_log = revised_metrics.get("debate_log", [])
    for entry in debate_log:
        role = entry.get("role", "")
        phase = entry.get("phase", "")
        if role:
            count = entry.get("count", 0)
            types = entry.get("issue_types") or entry.get("challenge_types", [])
            role_label = {
                "evidence_reviewer": "证据审查员",
                "logic_reviewer": "逻辑审查员",
                "challenger": "对抗性质疑者",
            }.get(role, role)
            add_log(task_id, f"[第{round_num}轮辩论] {role_label}发现 {count} 个问题：{', '.join(types)}")
        elif phase == "consolidated":
            consensus = entry.get("consensus_count", 0)
            disputed = entry.get("disputed_count", 0)
            add_log(task_id, f"[第{round_num}轮辩论] 合并：{consensus} 共识 / {disputed} 争议")

    overall = float(revised_metrics.get("overall_score") or 0.0)
    improvement = overall - state["previous_overall"]
    new_round = state["revision_round"] + 1

    add_log(task_id, f"review round {new_round}: overall={overall:.3f}, delta={improvement:.3f}")

    best_score = state["best_score"]
    best_content = state["best_content"]
    best_issues = state["best_issues"]
    best_metrics = state["best_metrics"]

    if overall > best_score:
        best_score = overall
        best_content = state["current_content"]
        best_issues = revised_issues
        best_metrics = revised_metrics

    stagnant = state["stagnant_rounds"] + 1 if improvement < MIN_IMPROVEMENT else 0

    rounds = list(state["review_rounds"])
    rounds.append({"round": new_round, "stage": "revision", "metrics": dict(revised_metrics), "improvement": round(improvement, 6)})

    return {
        "current_issues": revised_issues,
        "current_metrics": revised_metrics,
        "review_rounds": rounds,
        "revision_round": new_round,
        "stagnant_rounds": stagnant,
        "previous_overall": overall,
        "best_score": best_score,
        "best_content": best_content,
        "best_issues": best_issues,
        "best_metrics": best_metrics,
    }


# ── Node 8: export ────────────────────────────────────────────────


def export_node(state: WorkflowState) -> dict[str, Any]:
    task_id = state["task_id"]
    db = state["db"]
    payload = state["payload"]
    project = state["project"]
    cards = state["cards"]
    papers = state["selected_papers"]

    best_metrics = state["best_metrics"]
    best_issues = state["best_issues"]

    # Reader-facing final output:
    # 1. Re-run de-AI processing: revision rewrites paragraphs at LLM
    #    temperature, reintroducing the connector/template cliches the first
    #    pass removed (de_ai preserves evidence comments, so run it before
    #    citation rendering).
    # 2. Render evidence traceability comments as visible [N] in-text
    #    citations plus a formatted references section
    #    (project.citation_style drives the format), then strip any remaining
    #    markers. Previously citation_style was accepted but never used and
    #    the final draft carried no citations at all.
    from app.services.citation_service import render_in_text_citations
    from app.services.de_ai_service import de_ai_markdown

    final_content = de_ai_markdown(state["best_content"], intensity=0.3)
    final_content = strip_evidence_comments(
        render_in_text_citations(final_content, cards, state["project"].citation_style)
    )

    revised_status = "publication_prepared" if best_metrics.get("publication_prepared") else "revised_needs_human_review"
    revised_critical = len([i for i in best_issues if i.get("severity") == "high"])

    revised_draft = Draft(
        id=str(uuid4()), project_id=state["project_id"],
        version=_next_draft_version(state["project_id"], db),
        title=(state["draft"].title or "Draft") + " (Revised)",
        content_md=final_content, status=revised_status,
        quality_score=score_quality(len(best_issues), revised_critical, metrics=best_metrics),
        created_at=_now(),
    )
    db.add(revised_draft)
    db.flush()
    # 增量提交：修订稿 + 审稿 issue 落盘，崩溃后可取回最新修订版本。
    rd_id, rd_version = revised_draft.id, revised_draft.version
    db.commit()
    set_artifact(task_id, "draft_id", rd_id)
    set_artifact(task_id, "draft_version", rd_version)

    export_files: dict[str, str] = {}
    if payload.auto_export:
        set_progress(task_id, 97, "exporting package")
        target_dir = ensure_export_dir(backend_dir / "data", state["project_id"])
        stamp = _timestamp()

        md_path = export_markdown(target_dir, f"draft_{revised_draft.version}_{stamp}.md", revised_draft.content_md)
        docx_path = export_docx(target_dir, f"draft_{revised_draft.version}_{stamp}.docx", revised_draft.content_md)
        pdf_path = export_pdf(target_dir, f"draft_{revised_draft.version}_{stamp}.pdf", revised_draft.content_md)

        bib_path = export_bibtex(target_dir, f"refs_{stamp}.bib", [
            {"title": p.title, "authors": p.authors, "year": p.year, "venue": p.venue, "doi": p.doi, "arxiv_id": p.arxiv_id}
            for p in papers
        ])
        evidence_path = export_json(target_dir, f"evidence_map_{stamp}.json", [
            {"id": c.id, "paper_id": c.paper_id, "chunk_ids": c.chunk_ids, "claim": c.claim,
             "supporting_text": c.supporting_text, "evidence_type": c.evidence_type, "strength": c.strength,
             "page_start": c.page_start, "page_end": c.page_end}
            for c in cards
        ])

        review_lines = ["# Review Report", ""]
        for idx, issue in enumerate(state["created_issues"], start=1):
            review_lines.extend([
                f"## Issue {idx}", f"- severity: {issue.severity}", f"- issue_type: {issue.issue_type}",
                f"- location: {issue.location or 'n/a'}", f"- description: {issue.description}",
                f"- suggestion: {issue.suggestion or 'n/a'}", "",
            ])
        review_path = export_markdown(target_dir, f"review_report_{stamp}.md", "\n".join(review_lines))

        quality_path = export_quality_report(
            target_dir, f"quality_report_{stamp}.json",
            draft_version=revised_draft.version, review_rounds=state["review_rounds"],
            final_metrics=dict(best_metrics), publication_prepared=bool(best_metrics.get("publication_prepared")),
        )

        export_files = {
            "markdown": str(md_path), "docx": str(docx_path), "pdf": str(pdf_path),
            "bibtex": str(bib_path), "evidence_map": str(evidence_path),
            "review_report": str(review_path), "quality_report": str(quality_path),
        }

    return {
        "revised_draft": revised_draft,
        "revised_status": revised_status,
        "revised_critical_count": revised_critical,
        "export_files": export_files,
    }


# ── Node 9: assemble_result ──────────────────────────────────────


def result_node(state: WorkflowState) -> dict[str, Any]:
    db = state["db"]
    db.commit()

    result = {
        "query": state["query"],
        "inserted_count": state["inserted"],
        "total_papers": len(state["selected_papers"]),
        "selected_count": len(state["selected_papers"]),
        "auto_selected_count": state["auto_selected_count"],
        "reused_local_pdf_count": state["reused_local_pdf_count"],
        "resolved_via_fallback_count": state["resolved_via_fallback_count"],
        "downloaded_count": state["downloaded_count"],
        "parsed_count": state["parsed_count"],
        "skipped_no_pdf_count": state["skipped_no_pdf_count"],
        "failed_count": state["failed_count"],
        "evidence_count": state["evidence_count"],
        "metadata_fallback_evidence_count": state["metadata_fallback_evidence_count"],
        "low_relevance_filtered_count": state["low_relevance_filtered_count"],
        "draft_id": state["draft"].id,
        "revised_draft_id": state["revised_draft"].id,
        "review_issue_count": len(state["created_issues"]),
        "critical_issue_count": state["revised_critical_count"],
        "revision_rounds_executed": state["revision_round"],
        "publication_prepared": bool(state["best_metrics"].get("publication_prepared")),
        "quality_gate": state["best_metrics"],
        "export_files": state["export_files"],
        "node_timings": state.get("node_timings", {}),
    }
    return {"result": result}


# ── Conditional routing ──────────────────────────────────────────


def _timed_node(step_name: str, node_name: str, fn):
    """Wrap a node function to record step-level timing."""
    def wrapper(state: WorkflowState) -> dict[str, Any]:
        start = time.perf_counter()
        result = fn(state)
        elapsed = time.perf_counter() - start
        try:
            from app.middleware.metrics import metrics_record_step
            metrics_record_step(step_name, elapsed)
        except Exception:
            pass
        timings = dict(state.get("node_timings") or {})
        timings[node_name] = round(elapsed, 3)
        result["node_timings"] = timings
        task_id = state.get("task_id")
        if task_id:
            add_log(task_id, f"[timing] {step_name}: {elapsed:.1f}s")
            add_log(task_id, f"[timing_node] {node_name}: {elapsed:.1f}s")
        return result
    wrapper.__name__ = fn.__name__
    return wrapper


def _route_after_review(state: WorkflowState) -> Literal["revise", "export"]:
    if state["current_metrics"].get("publication_prepared"):
        return "export"
    if state["revision_round"] >= MAX_REVISION_ROUNDS:
        return "export"
    if state["stagnant_rounds"] >= STAGNATION_LIMIT:
        return "export"
    return "revise"


# ── Graph construction ───────────────────────────────────────────


def create_workflow_graph():
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(WorkflowState)

    builder.add_node("topic_assessment", _timed_node("assessment", "topic_assessment", topic_assessment_node))
    builder.add_node("search_and_select", _timed_node("search", "search_and_select", search_node))
    builder.add_node("ingest_papers", _timed_node("ingest", "ingest_papers", ingest_node))
    builder.add_node("build_evidence", _timed_node("evidence", "build_evidence", evidence_node))
    builder.add_node("gather_web_sources", _timed_node("web", "gather_web_sources", web_sources_node))
    builder.add_node("gather_community_sources", _timed_node("community", "gather_community_sources", community_sources_node))
    builder.add_node("relevance_filter", _timed_node("filter", "relevance_filter", relevance_filter_node))
    builder.add_node("conflict_detection", _timed_node("filter", "conflict_detection", conflict_detection_node))
    builder.add_node("thesis_thread", _timed_node("draft", "thesis_thread", thesis_thread_node))
    builder.add_node("plan_figures", _timed_node("draft", "plan_figures", plan_figures_node))
    builder.add_node("generate_draft", _timed_node("draft", "generate_draft", draft_node))
    builder.add_node("generate_images", _timed_node("images", "generate_images", image_generation_node))
    builder.add_node("initial_review", _timed_node("review", "initial_review", initial_review_node))
    builder.add_node("revise", _timed_node("review", "revise", revise_node))
    builder.add_node("refresh_figures", _timed_node("review", "refresh_figures", refresh_figures_node))
    builder.add_node("review", _timed_node("review", "review", review_node))
    builder.add_node("export", _timed_node("export", "export", export_node))
    builder.add_node("assemble_result", _timed_node("export", "assemble_result", result_node))

    builder.add_edge(START, "topic_assessment")
    builder.add_edge("topic_assessment", "search_and_select")
    builder.add_edge("search_and_select", "ingest_papers")
    builder.add_edge("ingest_papers", "build_evidence")
    builder.add_edge("build_evidence", "gather_web_sources")
    builder.add_edge("gather_web_sources", "gather_community_sources")
    builder.add_edge("gather_community_sources", "relevance_filter")
    builder.add_edge("relevance_filter", "conflict_detection")
    builder.add_edge("conflict_detection", "thesis_thread")
    builder.add_edge("thesis_thread", "plan_figures")
    builder.add_edge("plan_figures", "generate_draft")
    builder.add_edge("generate_draft", "generate_images")
    builder.add_edge("generate_images", "initial_review")

    builder.add_conditional_edges("initial_review", _route_after_review, {"revise": "revise", "export": "export"})
    builder.add_conditional_edges("review", _route_after_review, {"revise": "revise", "export": "export"})
    # L2: after each revise round, re-sync figure captions with the revised
    # text before the next review.
    builder.add_edge("revise", "refresh_figures")
    builder.add_edge("refresh_figures", "review")

    builder.add_edge("export", "assemble_result")
    builder.add_edge("assemble_result", END)

    return builder.compile()


_workflow_graph = create_workflow_graph()
