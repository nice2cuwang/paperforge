"""Generate social proof cards from academic/community APIs.

Fetches real-world popularity signals — GitHub stars, Semantic Scholar
citations, arXiv metadata, Hugging Face downloads — and renders them as
styled SVG cards for article illustrations, matching the "social proof"
screenshot style seen in high-quality WeChat academic articles.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from app.services.http_client import create_httpx_client

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 8.0
_HEADERS = {"User-Agent": "PaperForge/0.3 (+https://paperforge.local)"}


def _to_api_path(filepath: Path, project_id: str) -> str:
    """Convert an absolute filesystem path to a frontend-compatible API path."""
    marker = f"images{os.sep}"
    full = str(filepath)
    idx = full.find(marker)
    if idx != -1:
        relative = full[idx:]
        return f"/api/projects/{project_id}/{relative.replace(os.sep, '/')}"
    return f"/api/projects/{project_id}/images/{filepath.name}"


def _esc(text: str) -> str:
    """Escape text for safe SVG/XML embedding."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


# ── API fetchers ────────────────────────────────────────────────────────


def _fetch_semantic_solar(paper_title: str, doi: str | None = None) -> dict[str, Any] | None:
    """Fetch citation data from Semantic Scholar Academic Graph."""
    try:
        with create_httpx_client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            params: dict[str, Any] = {
                "fields": "title,citationCount,influentialCitationCount,year,venue,externalIds,url",
            }
            if doi:
                url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            else:
                url = "https://api.semanticscholar.org/graph/v1/paper/search"
                params["query"] = paper_title
                params["limit"] = 1

            resp = client.get(url, params=params)
            if resp.status_code != 200:
                return None

            data = resp.json()
            if "data" in data and data["data"]:
                data = data["data"][0]

            return {
                "title": data.get("title", ""),
                "citations": data.get("citationCount", 0),
                "influential_citations": data.get("influentialCitationCount", 0),
                "year": data.get("year"),
                "venue": data.get("venue", ""),
                "url": data.get("url", ""),
            }
    except Exception:
        logger.debug("Semantic Scholar fetch failed", exc_info=True)
        return None


def _fetch_github_info(paper_title: str) -> dict[str, Any] | None:
    """Search GitHub for the paper's repository."""
    try:
        with create_httpx_client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(
                "https://api.github.com/search/repositories",
                params={"q": paper_title, "sort": "stars", "order": "desc", "per_page": 3},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            items = data.get("items", [])
            if not items:
                return None

            repo = items[0]
            return {
                "full_name": repo.get("full_name", ""),
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "description": (repo.get("description") or "")[:200],
                "url": repo.get("html_url", ""),
                "language": repo.get("language", ""),
            }
    except Exception:
        logger.debug("GitHub fetch failed", exc_info=True)
        return None


def _fetch_arxiv_info(arxiv_id: str) -> dict[str, Any] | None:
    """Fetch metadata from the arXiv Atom API."""
    try:
        with create_httpx_client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
            if resp.status_code != 200:
                return None

            text = resp.text
            title_match = re.search(r"<title>(.*?)</title>", text, re.DOTALL)
            if not title_match:
                return None

            return {
                "title": re.sub(r"\s+", " ", title_match.group(1)).strip(),
                "arxiv_id": arxiv_id,
            }
    except Exception:
        logger.debug("arXiv fetch failed", exc_info=True)
        return None


def _fetch_huggingface(paper_title: str) -> dict[str, Any] | None:
    """Search Hugging Face for related models."""
    try:
        with create_httpx_client(timeout=_HTTP_TIMEOUT, headers=_HEADERS, follow_redirects=True) as client:
            resp = client.get(
                "https://huggingface.co/api/models",
                params={"search": paper_title, "sort": "likes", "direction": "-1", "limit": 3},
            )
            if resp.status_code != 200:
                return None

            models = resp.json()
            if not models:
                return None

            model = models[0]
            return {
                "model_id": model.get("modelId", model.get("id", "")),
                "downloads": model.get("downloads", 0),
                "likes": model.get("likes", 0),
                "author": model.get("author", ""),
            }
    except Exception:
        logger.debug("Hugging Face fetch failed", exc_info=True)
        return None


# ── SVG card renderers ──────────────────────────────────────────────────


def _render_stat_card(
    title: str,
    value: str,
    subtitle: str,
    output_path: Path,
    project_id: str,
    *,
    accent_color: str = "#4C72B0",
    icon: str = "",
) -> str | None:
    """Render a single metric stat card as SVG."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = 480, 180
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">',
        f'<stop offset="0%" stop-color="#FAFBFC"/><stop offset="100%" stop-color="#F0F2F5"/>',
        f'</linearGradient></defs>',
        f'<rect width="{w}" height="{h}" fill="url(#bg)" rx="12"/>',
        f'<rect x="0" y="0" width="6" height="{h}" fill="{accent_color}" rx="3"/>',
    ]

    # Icon.
    if icon:
        svg.append(
            f'<text x="28" y="42" font-size="22">{icon}</text>'
        )

    # Title (truncate if needed).
    display_title = _esc(title[:48] + ("..." if len(title) > 48 else title))
    t_fs = 13 if len(title) <= 38 else 11
    svg.append(
        f'<text x="{44 if icon else 28}" y="40" font-family="Arial,sans-serif" '
        f'font-size="{t_fs}" fill="#666" font-weight="500">{display_title}</text>'
    )

    # Big value.
    safe_value = _esc(value)
    v_fs = 36 if len(value) <= 8 else (28 if len(value) <= 12 else 22)
    svg.append(
        f'<text x="28" y="100" font-family="Arial,sans-serif" '
        f'font-size="{v_fs}" fill="{accent_color}" font-weight="bold">{safe_value}</text>'
    )

    # Subtitle.
    safe_subtitle = _esc(subtitle)
    svg.append(
        f'<text x="28" y="140" font-family="Arial,sans-serif" '
        f'font-size="12" fill="#888">{safe_subtitle}</text>'
    )

    # Bottom accent bar.
    svg.append(
        f'<rect x="28" y="158" width="80" height="3" fill="{accent_color}" '
        f'rx="1.5" opacity="0.3"/>'
    )
    svg.append("</svg>")

    svg_path = output_path.with_suffix(".svg")
    svg_path.write_text("\n".join(svg), encoding="utf-8")
    return _to_api_path(svg_path, project_id) if project_id else str(svg_path.resolve())


def _render_paper_info_card(
    paper_info: dict[str, Any],
    output_path: Path,
    project_id: str = "",
) -> str | None:
    """Render a paper information card (title + venue + citations)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    w, h = 520, 200
    title = (paper_info.get("title") or "")[:75]
    if len(paper_info.get("title", "")) > 75:
        title += "..."
    venue = paper_info.get("venue", "") or "arXiv"
    year = paper_info.get("year", "")
    citations = paper_info.get("citations", 0)
    citations_display = f"{citations:,}"

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">',
        f'<rect width="{w}" height="{h}" fill="#FFFFFF" rx="12" '
        f'stroke="#E0E0E0" stroke-width="1"/>',
        # Header bar.
        f'<rect x="0" y="0" width="{w}" height="48" fill="#2C3E50" rx="12"/>',
        f'<rect x="0" y="36" width="{w}" height="12" fill="#2C3E50"/>',
        f'<text x="20" y="32" font-family="Arial,sans-serif" font-size="15" '
        f'fill="white" font-weight="bold">{_esc(title)}</text>',
    ]

    # Venue + year badge.
    badge = f"{venue} {year}".strip() if venue or year else ""
    if badge:
        svg.append(
            f'<rect x="20" y="62" width="{min(len(badge) * 8 + 16, 280)}" height="24" '
            f'fill="#E8ECF1" rx="12"/>'
        )
        svg.append(
            f'<text x="28" y="79" font-family="Arial,sans-serif" font-size="11" '
            f'fill="#555">{_esc(badge)}</text>'
        )

    # Citation count — use display string length for positioning.
    svg.append(
        f'<text x="20" y="125" font-family="Arial,sans-serif" font-size="28" '
        f'fill="#E74C3C" font-weight="bold">{citations_display}</text>'
    )
    svg.append(
        f'<text x="{20 + len(citations_display) * 18 + 8}" y="125" '
        f'font-family="Arial,sans-serif" font-size="13" fill="#888">citations</text>'
    )

    # Influential citations if available.
    inf_cit = paper_info.get("influential_citations")
    if inf_cit:
        svg.append(
            f'<text x="20" y="155" font-family="Arial,sans-serif" font-size="12" '
            f'fill="#888">Influential citations: {inf_cit}</text>'
        )

    # URL.
    url = paper_info.get("url", "")
    if url and len(url) < 75:
        svg.append(
            f'<text x="20" y="182" font-family="Arial,sans-serif" font-size="10" '
            f'fill="#4C72B0">{_esc(url)}</text>'
        )

    svg.append("</svg>")

    svg_path = output_path.with_suffix(".svg")
    svg_path.write_text("\n".join(svg), encoding="utf-8")
    return _to_api_path(svg_path, project_id) if project_id else str(svg_path.resolve())


# ── Main orchestrator ───────────────────────────────────────────────────


def generate_social_proof_cards(
    papers: list[Any],
    project_id: str,
    output_dir: Path,
) -> list[dict[str, str]]:
    """Generate social proof cards for selected papers.

    Returns a list of image metadata dicts::

        [{"path": "...", "alt": "...", "section": "...", "source": "social_proof"}]
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cards: list[dict[str, str]] = []

    # ── Collect data from APIs ─────────────────────────────────
    paper_meta: list[dict[str, Any]] = []
    github_repos: list[dict[str, Any]] = []
    hf_models: list[dict[str, Any]] = []

    for paper in papers[:4]:
        title = (paper.title or "").strip()
        if not title:
            continue

        meta: dict[str, Any] = {"title": title, "paper_id": paper.id}

        # Semantic Scholar.
        ss = _fetch_semantic_solar(title, doi=getattr(paper, "doi", None))
        if ss:
            meta["citations"] = ss.get("citations", 0)
            meta["influential_citations"] = ss.get("influential_citations", 0)
            meta["venue"] = ss.get("venue", "")
            meta["year"] = ss.get("year", "")
            meta["ss_url"] = ss.get("url", "")

        # arXiv.
        arxiv_id = getattr(paper, "arxiv_id", None)
        if arxiv_id:
            arxiv = _fetch_arxiv_info(arxiv_id)
            if arxiv:
                meta["arxiv_id"] = arxiv_id

        # GitHub.
        gh = _fetch_github_info(title)
        if gh and (gh.get("stars") or 0) > 5:
            meta["github"] = gh
            github_repos.append(gh)

        # Hugging Face.
        hf = _fetch_huggingface(title)
        if hf:
            meta["huggingface"] = hf
            hf_models.append(hf)

        paper_meta.append(meta)

    # ── Render stat cards ─────────────────────────────────────
    stat_index = 0

    # GitHub stars.
    for gh in github_repos[:2]:
        path = _render_stat_card(
            title=f"GitHub: {gh['full_name']}",
            value=f"{gh['stars']:,}",
            subtitle=f"stars | {gh.get('forks', 0):,} forks | {gh.get('language', 'N/A')}",
            output_path=output_dir / f"social_stat_{stat_index}.svg",
            project_id=project_id,
            accent_color="#E74C3C",
            icon="",
        )
        if path:
            cards.append({
                "path": path,
                "alt": f"GitHub repository: {gh['full_name']}",
                "section": "Background",
                "source": "social_proof",
            })
            stat_index += 1

    # Top citation count.
    top_cited = max(
        paper_meta,
        key=lambda m: m.get("citations", 0),
        default=None,
    )
    if top_cited and top_cited.get("citations", 0) > 0:
        path = _render_stat_card(
            title=top_cited["title"][:60],
            value=f"{top_cited['citations']:,}",
            subtitle=(
                f"citations | {top_cited.get('venue', 'arXiv')} "
                f"{top_cited.get('year', '')}".strip()
            ),
            output_path=output_dir / f"social_stat_{stat_index}.svg",
            project_id=project_id,
            accent_color="#27AE60",
            icon="",
        )
        if path:
            cards.append({
                "path": path,
                "alt": f"Citation count: {top_cited['title'][:50]}",
                "section": "Background",
                "source": "social_proof",
            })
            stat_index += 1

    # Hugging Face.
    for hf in hf_models[:1]:
        if hf.get("downloads", 0) > 0 or hf.get("likes", 0) > 0:
            path = _render_stat_card(
                title=f"HuggingFace: {hf['model_id']}",
                value=f"{hf.get('downloads', 0):,}",
                subtitle=f"downloads | {hf.get('likes', 0):,} likes",
                output_path=output_dir / f"social_stat_{stat_index}.svg",
                project_id=project_id,
                accent_color="#F39C12",
                icon="",
            )
            if path:
                cards.append({
                    "path": path,
                    "alt": f"HuggingFace model: {hf['model_id']}",
                    "section": "Background",
                    "source": "social_proof",
                })
                stat_index += 1

    # ── Paper info cards ──────────────────────────────────────
    sorted_papers = sorted(
        paper_meta,
        key=lambda m: m.get("citations", 0),
        reverse=True,
    )
    for i, meta in enumerate(sorted_papers[:3]):
        if meta.get("citations", 0) > 0 or meta.get("venue"):
            path = _render_paper_info_card(
                meta,
                output_dir / f"social_paper_{meta.get('paper_id', i)}.svg",
                project_id=project_id,
            )
            if path:
                cards.append({
                    "path": path,
                    "alt": f"Paper info: {meta['title'][:50]}",
                    "section": "Background",
                    "source": "social_proof",
                })

    logger.info(
        "Generated %d social proof cards for project %s", len(cards), project_id,
    )
    return cards
