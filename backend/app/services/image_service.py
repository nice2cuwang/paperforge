"""Image generation service for article illustrations.

Primary: Pollinations.ai (free, no API key) for raster images.
Fallback: Professional SVG template engine for high-quality infographics.

The SVG pipeline uses LLM to extract structured data (metrics, steps, comparisons)
from article sections, then renders magazine-quality SVGs via Python templates.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

logger = logging.getLogger(__name__)

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
IMAGE_FETCH_TIMEOUT = 45.0
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 576
MAX_IMAGES_PER_ARTICLE = 5


def _claim_tokens(text: str) -> set[str]:
    """CJK bigram/trigram + alphanumeric tokens, for section<->evidence matching."""
    t = (text or "").lower()
    tokens = set(re.findall(r"[a-z0-9]{2,}", t))
    cjk = [ch for ch in t if "一" <= ch <= "鿿"]
    for idx in range(len(cjk) - 1):
        tokens.add(cjk[idx] + cjk[idx + 1])
    for idx in range(len(cjk) - 2):
        tokens.add(cjk[idx] + cjk[idx + 1] + cjk[idx + 2])
    return tokens


def plan_figures(
    sections: list[str],
    evidence_cards: list[dict[str, Any]],
    max_figures: int = 6,
) -> list[dict[str, Any]]:
    """Plan which figure each section needs, grounded in a real evidence card (F1).

    Returns one plan per section (capped at ``max_figures``):
    ``{"fig_index", "section", "kind", "evidence_id", "caption", "ref_key"}``.
    The caption derives from the section's best-matching evidence card so
    figures never decorate without data support; ``ref_key`` is what the
    writing prompt asks the LLM to reference as ``{{ref:fig:N}}``.
    """
    plans: list[dict[str, Any]] = []
    for i, section in enumerate(sections[:max_figures]):
        sec_tokens = _claim_tokens(section)
        best_card: dict[str, Any] | None = None
        best_score = 0.0
        for card in evidence_cards:
            claim = (card.get("_clean_claim") or card.get("claim") or "")
            claim_tokens = _claim_tokens(claim)
            score = len(sec_tokens & claim_tokens)
            if score > best_score:
                best_card, best_score = card, score
        claim = (best_card.get("_clean_claim") or best_card.get("claim") or "") if best_card else ""
        kind = "chart" if re.search(r"[0-9%]", claim) else "illustration"
        caption = re.sub(r"\s+", " ", claim).strip()[:60] or f"{section}概念示意"
        plans.append(
            {
                "fig_index": i + 1,
                "section": section,
                "kind": kind,
                "evidence_id": (best_card.get("id") or "") if best_card else "",
                "caption": caption,
                "ref_key": f"fig:{i + 1}",
            }
        )
    return plans


def finalize_figures(content_md: str, images: list[dict[str, str]]) -> str:
    """F3: figure numbering + captions + cross-reference resolution.

    1. Every ``![...](...)`` line gets a running number and a bold caption line
       below it (``**图N：** caption``), preferring the plan-provided caption
       and falling back to the image prompt/alt.
    2. Every ``{{ref:fig:N}}`` placeholder the writer left in the body becomes
       「（如图M所示）」 where M is the *actual* figure number the referenced
       image received after injection -- not the plan index N. Extracted paper
       figures and social cards occupy running numbers too, so the plan index
       and the displayed number diverge whenever such images precede a planned
       one. Mapping via each image's ``ref_key`` keeps the cross-reference
       pointing at the right figure; when no image carries that ref_key (e.g.
       images were not injected), N is used as-is so the reference still
       resolves instead of leaving a raw placeholder behind.

    Idempotent (L2): if a ``**图N：**`` caption already follows an image line,
    the caption is replaced in place and its number kept -- so re-running after
    a revision refreshes captions without duplicating or renumbering figures.
    """
    images_by_path = {img.get("path"): img for img in images if img.get("path")}
    raw_lines = content_md.split("\n")
    lines: list[str] = []
    fig_no = 0
    # ref_key (e.g. "fig:1") -> actual displayed figure number, so a
    # cross-reference survives when extracted/social images shift the running
    # count ahead of a planned figure.
    ref_to_fig_no: dict[str, int] = {}
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        match = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", line)
        if not match:
            lines.append(line)
            i += 1
            continue
        alt, path = match.group(1), match.group(2)
        img = images_by_path.get(path, {})
        # Social proof cards are credibility cards, not numbered figures:
        # keep the image in place but skip the 图N caption line so they
        # neither inflate the figure count nor steal a number from a planned
        # figure appearing later in the flow.
        if img.get("source") == "social_proof":
            lines.append(f"![{alt}]({path})")
            lines.append("")
            i += 1
            continue
        fig_no += 1
        caption = (
            img.get("caption")
            or img.get("alt")
            or img.get("prompt")
            or alt
            or "示意图"
        )
        # Check whether a caption already follows this image (L2 refresh path).
        j = i + 1
        while j < len(raw_lines) and not raw_lines[j].strip():
            j += 1
        existing = None
        if j < len(raw_lines):
            cap_match = re.match(r"^\*\*图(\d+)：\*\*", raw_lines[j])
            if cap_match:
                existing = (j, cap_match.group(1))
        # Display number: keep the existing L2 number, else the running count.
        display_no = int(existing[1]) if existing else fig_no
        ref_key = img.get("ref_key")
        if ref_key:
            ref_to_fig_no[ref_key] = display_no
        lines.append(f"![{alt}]({path})")
        lines.append("")
        if existing:
            lines.append(f"**图{existing[1]}：** {caption}")
            i = existing[0] + 1
        else:
            lines.append(f"**图{fig_no}：** {caption}")
            lines.append("")
            i += 1
    text = "\n".join(lines)
    # Resolve cross-reference placeholders using the ref_key -> figure-number
    # map built during numbering. Fall back to the placeholder's own N when no
    # image carries that ref_key (e.g. images not yet injected).
    def _resolve_ref(m: re.Match) -> str:
        ref_key = f"fig:{int(m.group(1))}"
        if ref_key in ref_to_fig_no:
            return f"（如图{ref_to_fig_no[ref_key]}所示）"
        if not images:
            # No images injected -- use the plan index so a draft with
            # placeholders but no figures still reads naturally.
            return f"（如图{int(m.group(1))}所示）"
        # Images exist but this ref_key has no anchor: its planned figure was
        # never generated. Falling back to the plan index N would point at the
        # Nth image by position (unrelated), and neutralizing to "下图" left
        # the word stranded mid-sentence or at paragraph ends. Delete the
        # placeholder outright -- no reference beats a false one.
        return ""

    text = re.sub(r"\{\{\s*ref:fig:(\d+)\s*\}\}", _resolve_ref, text)
    # Collapse double-wrapped refs ("（（如图N所示））" -> "（如图N所示）") left
    # when the writer wrapped the placeholder in its own parentheses.
    text = re.sub(r"（（如图(\d+)所示））", r"（如图\1所示）", text)
    # Tidy the gaps deleted placeholders leave behind: stray spaces before
    # newlines, around CJK punctuation ("。 对研究团队" -> "。对研究团队"),
    # and doubled spaces between CJK characters ("见  与" -> "见与").
    text = re.sub(r" +\n", "\n", text)
    text = re.sub(r"([。！？；，、：]) +", r"\1", text)
    text = re.sub(r" +([。！？；，、：])", r"\1", text)
    text = re.sub(r"([一-鿿]) +([一-鿿])", r"\1\2", text)
    return text


def generate_image_prompts(
    project_title: str,
    research_question: str,
    sections: list[str],
    article_type: str,
) -> list[dict[str, str]]:
    """Use LLM to generate image prompts for each article section.

    Returns list of dicts: [{"section": "章节名", "prompt": "English image prompt", "placement": "after_section"}]
    """
    from app.services.llm_service import chat_completion
    import json

    system_prompt = (
        "You are a creative director for technical articles. Generate vivid, professional "
        "image generation prompts for article illustrations. Each prompt should describe a "
        "clean, modern infographic-style or conceptual illustration that visually represents "
        "the section content.\n\n"
        "Rules:\n"
        "- Prompts must be in English\n"
        "- Style: clean, professional, flat design illustration or infographic\n"
        "- Include specific visual elements (charts, diagrams, icons, technology components)\n"
        "- Use a consistent color palette (blues, teals, whites for tech articles)\n"
        "- Avoid text/words in the image (AI image generators handle text poorly)\n"
        "- Each prompt should be 20-40 words\n"
        "- Generate prompts for ALL sections (up to 5), especially sections that contain "
        "concrete examples, case studies, or comparisons — these sections MUST have illustrations\n"
    )

    user_prompt = (
        f"Article topic: {project_title}\n"
        f"Research question: {research_question}\n"
        f"Article type: {article_type}\n\n"
        f"Article sections: {', '.join(sections)}\n\n"
        f"Generate image prompts for ALL sections listed above (up to 5). "
        f"Sections containing concrete examples, case studies, comparisons, or "
        f"practical applications are HIGHEST priority — they must have illustrations "
        f"to visually reinforce the examples.\n"
        f"Output as a JSON array:\n"
        f'[{{"section": "section name", "prompt": "English image prompt", "style": "infographic/illustration/diagram"}}]\n\n'
        f"Only output the JSON array."
    )

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            timeout=30.0,
        )
        text = result.get("content", "").strip()
        if not text:
            return []

        # Extract JSON from code fences if present
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)

        prompts = json.loads(text)
        return prompts[:MAX_IMAGES_PER_ARTICLE]

    except Exception:
        logger.exception("Image prompt generation failed")
        return []


def fetch_image(prompt: str, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> bytes | None:
    """Generate an illustration for *prompt*.

    Priority: designated image-generation model (llm_settings 里指定的生图
    配置，OpenAI 兼容 /images/generations) → Pollinations.ai 免费回退。
    Returns raw image bytes, or None when both fail.
    """
    # ── 1) designated text-to-image model ──
    try:
        from app.services.llm_service import generate_image, image_gen_configured

        if image_gen_configured():
            size = f"{width}x{height}"
            result = generate_image(prompt, size=size, timeout=90.0)
            if result.get("image_bytes"):
                logger.info("Image generated via designated image model")
                return result["image_bytes"]
            # 部分平台只返回 URL：下载之
            url = result.get("image_url")
            if url:
                try:
                    with httpx.Client(timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=True, verify=False) as client:
                        resp = client.get(url)
                        resp.raise_for_status()
                        if "image" in resp.headers.get("content-type", ""):
                            logger.info("Image generated via designated image model (url)")
                            return resp.content
                except Exception:
                    logger.debug("Failed to download generated image url", exc_info=True)
            logger.warning(
                "Designated image model failed (%s); falling back to Pollinations",
                result.get("error"),
            )
    except Exception:
        logger.debug("Image model path unavailable; using Pollinations", exc_info=True)

    # ── 2) Pollinations free fallback ──
    import urllib.parse
    encoded = urllib.parse.quote(prompt[:300])  # Truncate long prompts
    url = f"{POLLINATIONS_BASE_URL}/{encoded}?width={width}&height={height}&nologo=true&seed=42"

    try:
        with httpx.Client(timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=True, verify=False) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "image" in content_type:
                return resp.content
            logger.warning("Pollinations returned non-image content-type: %s", content_type)
            return None
    except Exception:
        logger.debug("Image fetch failed for prompt: %s", prompt[:80], exc_info=True)
        return None


def save_image(image_bytes: bytes, project_id: str, filename: str) -> str | None:
    """Save image bytes to the project's data directory.

    Returns the relative path for use in markdown.
    """
    from app.database import backend_dir

    storage_dir = backend_dir / "data" / "storage" / project_id / "images"
    storage_dir.mkdir(parents=True, exist_ok=True)

    filepath = storage_dir / filename
    try:
        filepath.write_bytes(image_bytes)
        # Return path relative to the backend data directory
        return f"/api/projects/{project_id}/images/{filename}"
    except Exception:
        logger.exception("Failed to save image to %s", filepath)
        return None


def generate_article_images(
    project_id: str,
    project_title: str,
    research_question: str,
    sections: list[str],
    article_type: str,
    draft_content: str = "",
    evidence_cards: list[dict[str, Any]] | None = None,
    kind_by_section: dict[str, str] | None = None,
    skip_sections: set[str] | None = None,
    caption_by_section: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Full pipeline: generate prompts → fetch images → save to disk.

    Falls back to SVG template engine if external image APIs are unavailable.

    F2: ``evidence_cards`` binds SVG data extraction to real evidence text
    (no inferred numbers); ``kind_by_section`` (from the figure plan) routes
    quantitative claims to data-chart templates. ``caption_by_section``
    (evidence-grounded plan captions) is appended to the generation prompt so
    a real image model draws what the caption describes, not a loose guess
    from the section title alone.

    Returns list of dicts: [{"section": "...", "filename": "...", "path": "/api/...", "prompt": "..."}]
    """
    prompts = generate_image_prompts(project_title, research_question, sections, article_type)
    if not prompts:
        return []

    evidence_text = _format_evidence_for_svg(evidence_cards or [])
    kind_by_section = kind_by_section or {}
    caption_by_section = caption_by_section or {}
    # Canonical keys of sections that already carry a matplotlib data chart;
    # the SVG dashboard there would visualize the same metrics twice.
    skip_sections = skip_sections or set()

    results: list[dict[str, str]] = []
    for i, prompt_info in enumerate(prompts):
        section = prompt_info.get("section", f"section_{i}")
        if resolve_section_key(section) in skip_sections:
            logger.info("Skipping image for '%s' (matplotlib chart already covers it)", section)
            continue
        prompt = prompt_info.get("prompt", "")
        style = prompt_info.get("style", "infographic")
        if not prompt:
            continue

        # 生图模型按证据语境作画：计划图注（中文）描述了这节真正要展示的内容
        caption = caption_by_section.get(section) or caption_by_section.get(resolve_section_key(section)) or ""
        if caption:
            prompt = f"{prompt}. The illustration must visually convey this idea: {caption[:200]}"

        # Extract section content from draft for richer SVG generation
        section_content = _extract_section_content(draft_content, section)

        logger.info("Generating image %d/%d for section '%s'", i + 1, len(prompts), section)
        image_bytes = fetch_image(prompt)
        if image_bytes:
            filename = f"img_{i:02d}_{section[:20].replace(' ', '_').replace('/', '_')}.png"
            path = save_image(image_bytes, project_id, filename)
            if path:
                results.append({
                    "section": section,
                    "filename": filename,
                    "path": path,
                    "prompt": prompt,
                })
                logger.info("Image saved: %s", filename)
        else:
            # Fallback: generate SVG illustration using template engine
            logger.info("External API failed, generating template SVG for '%s'", section)
            svg_content = generate_svg_illustration(
                section=section,
                project_title=project_title,
                prompt=prompt,
                index=i,
                section_content=section_content,
                kind=kind_by_section.get(section, ""),
                evidence_text=evidence_text,
            )
            if svg_content:
                filename = f"img_{i:02d}_{section[:20].replace(' ', '_').replace('/', '_')}.svg"
                path = save_image(svg_content.encode("utf-8"), project_id, filename)
                if path:
                    results.append({
                        "section": section,
                        "filename": filename,
                        "path": path,
                        "prompt": prompt,
                    })
                    logger.info("SVG saved: %s", filename)

    return results


# F2: plan kind -> template routing. "chart" (quantitative claim) renders as a
# metrics dashboard; "illustration" keeps section-based selection.
_KIND_TEMPLATE: dict[str, tuple[str, str]] = {"chart": ("metrics_dashboard", "ocean")}


def _format_evidence_for_svg(cards: list[dict[str, Any]]) -> str:
    """Compact evidence-card text used as the verbatim-data source for figures."""
    lines: list[str] = []
    for i, card in enumerate(cards):
        claim = str(card.get("claim") or card.get("_clean_claim") or "")[:200]
        support = str(card.get("supporting_text") or "")[:400]
        if claim or support:
            lines.append(f"[{i}] claim: {claim}")
            if support:
                lines.append(f"    data: {support}")
        if len(lines) >= 32:
            break
    return "\n".join(lines)


def generate_svg_illustration(
    section: str,
    project_title: str,
    prompt: str,
    index: int,
    section_content: str = "",
    kind: str = "",
    evidence_text: str = "",
) -> str | None:
    """Generate a professional SVG illustration using the template engine.

    Uses LLM to extract structured data from the section, then renders via
    pre-designed Python templates for magazine-quality output.

    F2: extraction is bound to the evidence cards (``evidence_text``) and must
    only use values that appear verbatim there -- never "合理推断" numbers.
    """
    from app.services.llm_service import chat_completion
    from app.services.svg_templates import (
        get_template_for_section,
        render_process_flow,
        render_comparison,
        render_metrics_dashboard,
        render_architecture,
        THEME_KEYS,
    )
    import json as _json

    # Route by planned kind first (data chart), section name as fallback.
    if kind in _KIND_TEMPLATE:
        template_type, theme_key = _KIND_TEMPLATE[kind]
    else:
        template_type, theme_key = get_template_for_section(section)

    # Determine what data schema to request from the LLM based on template type
    schema_prompts = {
        "process_flow": (
            '提取 3-5 个关键步骤，输出 JSON：\n'
            '{"title": "图表标题(8字内)", "subtitle": "副标题(15字内)", '
            '"steps": [{"label": "步骤名(6字内)", "detail": "描述(20字内)", "icon": "emoji"}]}'
        ),
        "comparison": (
            '提取 3-4 个对比项及其指标，输出 JSON：\n'
            '{"title": "对比标题(8字内)", "subtitle": "副标题(15字内)", '
            '"items": [{"label": "名称(10字内)", "score": 数值, "tag": "标签(6字内)"}]}'
        ),
        "metrics_dashboard": (
            '提取 4-6 个关键指标，输出 JSON：\n'
            '{"title": "指标标题(8字内)", "subtitle": "副标题(15字内)", '
            '"metrics": [{"label": "指标名(8字内)", "value": "数值", "unit": "单位", "delta": "变化%", "icon": "emoji"}]}'
        ),
        "architecture": (
            '提取 3-5 个架构层次，输出 JSON：\n'
            '{"title": "架构标题(8字内)", "subtitle": "副标题(15字内)", '
            '"layers": [{"label": "层名(8字内)", "detail": "描述(25字内)", "icon": "emoji"}]}'
        ),
    }

    schema_prompt = schema_prompts.get(template_type, schema_prompts["metrics_dashboard"])

    system_prompt = (
        "你是一位数据可视化专家。请从给定的文章章节中提取关键信息，"
        "并按照指定的 JSON 格式输出结构化数据。这些数据将用于生成信息图表。\n\n"
        "规则（F2 数据真实性）：\n"
        "- 只提取章节内容或证据卡中明确提到的信息\n"
        "- **禁止推断或编造**：如果某项数据未提及，数值字段留空或省略该条目，"
        "绝不使用'合理推断'的数值\n"
        "- 所有数值、名称、指标必须能在提供的章节内容或证据卡中逐字找到\n"
        "- emoji 用单个表情符号表示概念\n"
        "- 所有文本使用中文\n"
        "- 数值尽量用数字表示\n"
        "- 只输出 JSON，不要其他内容"
    )

    user_prompt = (
        f"文章：{project_title}\n"
        f"章节：{section}\n"
        f"章节描述：{prompt}\n\n"
        f"章节内容摘要：\n{section_content[:1200] if section_content else prompt}\n\n"
    )
    if evidence_text:
        user_prompt += f"证据卡数据（提取的数值必须逐字出自以下内容）：\n{evidence_text}\n\n"
    user_prompt += (
        f"{schema_prompt}\n\n"
        f"只输出 JSON。"
    )

    try:
        result = chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            timeout=30.0,
        )
        text = result.get("content", "").strip()
        if not text:
            return None

        # Extract JSON from code fences
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)

        data = _json.loads(text)

        # F2 deterministic backstop: drop numeric entries whose value cannot
        # be found verbatim in the evidence/section corpus. The prompt rule
        # alone let invented numbers onto dashboards.
        from app.services.chart_service import matches_evidence_number

        _num_re = re.compile(r"[-+]?\d+(?:\.\d+)?")
        corpus = f"{evidence_text}\n{section_content}"

        def _verified(value: Any) -> bool:
            m = _num_re.search(str(value or ""))
            if not m:
                return True  # non-numeric text: nothing to verify
            return matches_evidence_number(m.group(0), corpus)

        if template_type == "metrics_dashboard":
            original_metrics = [m for m in data.get("metrics", []) if isinstance(m, dict)]
            metrics = [
                m for m in original_metrics
                if _verified(m.get("value")) and _verified(m.get("delta"))
            ]
            if original_metrics and not metrics:
                logger.warning("SVG metrics all failed verbatim validation for '%s'; dropping illustration", section)
                return None
            data["metrics"] = metrics
        elif template_type == "comparison":
            original_items = [it for it in data.get("items", []) if isinstance(it, dict)]
            items = [
                it for it in original_items
                if _verified(it.get("score"))
            ]
            if original_items and not items:
                logger.warning("SVG comparison items all failed verbatim validation for '%s'; dropping illustration", section)
                return None
            data["items"] = items

        # Rotate theme based on index for variety
        theme = THEME_KEYS[index % len(THEME_KEYS)]

        # Route to appropriate renderer
        if template_type == "process_flow":
            return render_process_flow(
                title=data.get("title", section),
                subtitle=data.get("subtitle", project_title),
                steps=data.get("steps", []),
                theme_key=theme,
            )
        elif template_type == "comparison":
            return render_comparison(
                title=data.get("title", section),
                subtitle=data.get("subtitle", project_title),
                items=data.get("items", []),
                theme_key=theme,
            )
        elif template_type == "architecture":
            return render_architecture(
                title=data.get("title", section),
                subtitle=data.get("subtitle", project_title),
                layers=data.get("layers", []),
                theme_key=theme,
            )
        else:  # metrics_dashboard
            return render_metrics_dashboard(
                title=data.get("title", section),
                subtitle=data.get("subtitle", project_title),
                metrics=data.get("metrics", []),
                theme_key=theme,
            )

    except Exception:
        logger.exception("Template SVG generation failed for section '%s'", section)
        return None


# ── Chinese ↔ English section alias map (shared: injection & plan matching) ──
_SECTION_ALIASES: dict[str, list[str]] = {
    "background": ["背景", "研究背景", "背景介绍", "引言", "introduction"],
    "framework": ["框架", "方法", "技术架构", "关键技术", "方法论", "methods", "approach"],
    "results": ["实验结果", "结果", "实验", "评测", "性能", "results", "evaluation", "experiments"],
    "discussion": ["讨论", "分析", "讨论与分析", "discussion", "analysis"],
    "conclusion": ["结论", "总结", "展望", "conclusion", "summary"],
}


def resolve_section_key(section: str) -> str:
    """Normalize a section name to a canonical key (实验结果与分析 -> results).

    Plan sections are LLM-generated topical headings while image sections are
    hardcoded English tags; exact string matching between the two almost
    always fails, so every section join goes through this canonicalization.
    """
    s = (section or "").strip().lower()
    for canonical, aliases in _SECTION_ALIASES.items():
        if s == canonical or any(a in s or s in a for a in aliases):
            return canonical
    return s


def inject_images_into_markdown(content_md: str, images: list[dict[str, str]]) -> str:
    """Insert images into article markdown using a position-based strategy.

    Instead of relying solely on section-name matching (which breaks with
    Chinese headers vs English image metadata), this function:

    1.  Tries section-name matching first (with Chinese/English alias map).
    2.  Falls back to proportional position placement — distributing images
        evenly across the article at paragraph break-points.
    """
    if not images:
        return content_md

    def _resolve_section(img_section: str) -> str:
        """Normalize an image's section tag to a canonical key."""
        return resolve_section_key(img_section)

    # ── Categorise images by canonical section ───────────────────
    section_buckets: dict[str, list[dict[str, str]]] = {}
    uncategorised: list[dict[str, str]] = []

    for img in images:
        raw_sec = img.get("section", "")
        if raw_sec:
            canon = _resolve_section(raw_sec)
            section_buckets.setdefault(canon, []).append(img)
        else:
            uncategorised.append(img)

    # ── Find paragraph break-points in the markdown ──────────────
    lines = content_md.split("\n")
    # A "break-point" is an empty line that sits between two text paragraphs
    # (not right after a heading or inside a code block).
    break_points: list[int] = []  # line indices of empty lines
    section_ranges: dict[str, tuple[int, int]] = {}  # canon → (start_line, end_line)
    current_section_lines: list[int] = []
    current_canon: str | None = None
    in_code_block = False

    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if line.startswith("## "):
            # Close previous section
            if current_canon and current_section_lines:
                section_ranges[current_canon] = (
                    current_section_lines[0], current_section_lines[-1],
                )
            header = line[3:].strip().lower()
            current_canon = _resolve_section(header)
            current_section_lines = [i]
            continue

        if current_canon is not None:
            current_section_lines.append(i)

        # Detect paragraph breaks (empty line between content).
        if (
            not line.strip()
            and i > 0
            and i < len(lines) - 1
            and lines[i - 1].strip()
            and lines[i + 1].strip()
            and not lines[i - 1].startswith("#")
            and not lines[i + 1].startswith("#")
        ):
            break_points.append(i)

    # Close last section
    if current_canon and current_section_lines:
        section_ranges[current_canon] = (
            current_section_lines[0], current_section_lines[-1],
        )

    if not break_points:
        # No break-points found; just append images at the end.
        extra = []
        for img in images[:8]:
            path = img.get("path", "")
            alt = img.get("prompt", "") or img.get("alt", "") or "illustration"
            if path:
                extra.extend(["", f"![{alt[:80]}]({path})", ""])
        return content_md + "\n" + "\n".join(extra)

    # ── Build insertion plan: (break_point_index, image_dict) ────
    insertions: dict[int, list[dict[str, str]]] = {}

    # Section-matched images: place at the first break-point inside
    # the matching section's line range.
    for canon, imgs in section_buckets.items():
        if canon in section_ranges:
            sec_start, sec_end = section_ranges[canon]
            # Find break-points within this section.
            candidates = [
                bp for bp in break_points if sec_start <= bp <= sec_end
            ]
            if candidates:
                # Place first image after 2nd paragraph, rest spaced out.
                for j, img in enumerate(imgs[:3]):
                    idx = min(j + 1, len(candidates) - 1)
                    bp = candidates[idx]
                    insertions.setdefault(bp, []).append(img)
            else:
                # No break-points in section; use uncategorised fallback.
                uncategorised.extend(imgs)
        else:
            uncategorised.extend(imgs)

    # Uncategorised / overflow images: distribute at proportional
    # positions across the article.
    if uncategorised:
        # Determine how many slots are still available.
        total_bp = len(break_points)
        used_bp = set(insertions.keys())
        available = [bp for bp in break_points if bp not in used_bp]

        if not available:
            available = break_points  # reuse if all taken

        n = min(len(uncategorised), len(available), 6)
        if n > 0:
            step = max(1, len(available) // n)
            for j in range(n):
                bp = available[j * step]
                insertions.setdefault(bp, []).append(uncategorised[j])

    # ── Apply insertions ─────────────────────────────────────────
    result_lines: list[str] = []
    inserted_count = 0

    for i, line in enumerate(lines):
        result_lines.append(line)
        if i in insertions:
            for img in insertions[i]:
                path = img.get("path", "")
                alt = img.get("prompt", "") or img.get("alt", "") or "illustration"
                if path:
                    result_lines.append("")
                    result_lines.append(f"![{alt[:80]}]({path})")
                    result_lines.append("")
                    inserted_count += 1

    return "\n".join(result_lines)


def _section_matches(actual_section: str, target_section: str) -> bool:
    """Fuzzy match section names (Chinese or English)."""
    actual = actual_section.strip().lower()
    target = target_section.strip().lower()

    if actual == target:
        return True

    # Partial match
    if target in actual or actual in target:
        return True

    # Keyword overlap
    actual_words = set(re.findall(r"[\w\u4e00-\u9fff]+", actual))
    target_words = set(re.findall(r"[\w\u4e00-\u9fff]+", target))
    overlap = actual_words & target_words
    return len(overlap) >= 1 and len(overlap) / max(len(target_words), 1) >= 0.5


def _extract_section_content(draft_content: str, section: str) -> str:
    """Extract the text content of a specific ## section from the draft markdown."""
    if not draft_content:
        return ""

    lines = draft_content.split("\n")
    capturing = False
    content_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            header = line[3:].strip()
            if _section_matches(header, section):
                capturing = True
                continue
            elif capturing:
                # Hit next section, stop
                break

        if capturing and line.strip():
            # Skip markdown artifacts
            if line.startswith("<!--") or line.startswith("!["):
                continue
            content_lines.append(line.strip())

    return "\n".join(content_lines[:20])  # Cap at 20 lines to avoid token overflow
