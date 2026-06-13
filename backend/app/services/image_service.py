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
    """Fetch a generated image from Pollinations.ai.

    Returns the raw image bytes, or None on failure.
    """
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
) -> list[dict[str, str]]:
    """Full pipeline: generate prompts → fetch images → save to disk.

    Falls back to SVG template engine if external image APIs are unavailable.

    Returns list of dicts: [{"section": "...", "filename": "...", "path": "/api/...", "prompt": "..."}]
    """
    prompts = generate_image_prompts(project_title, research_question, sections, article_type)
    if not prompts:
        return []

    results: list[dict[str, str]] = []
    for i, prompt_info in enumerate(prompts):
        section = prompt_info.get("section", f"section_{i}")
        prompt = prompt_info.get("prompt", "")
        style = prompt_info.get("style", "infographic")
        if not prompt:
            continue

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


def generate_svg_illustration(
    section: str,
    project_title: str,
    prompt: str,
    index: int,
    section_content: str = "",
) -> str | None:
    """Generate a professional SVG illustration using the template engine.

    Uses LLM to extract structured data from the section, then renders via
    pre-designed Python templates for magazine-quality output.
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
        "规则：\n"
        "- 只提取章节中明确提到的信息\n"
        "- 如果某项信息未提及，用合理的推断填充\n"
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

    # ── Chinese ↔ English section alias map ──────────────────────
    _SECTION_ALIASES: dict[str, list[str]] = {
        "background": ["背景", "研究背景", "背景介绍", "引言", "introduction"],
        "framework": ["框架", "方法", "技术架构", "关键技术", "方法论", "methods", "approach"],
        "results": ["实验结果", "结果", "实验", "评测", "性能", "results", "evaluation", "experiments"],
        "discussion": ["讨论", "分析", "讨论与分析", "discussion", "analysis"],
        "conclusion": ["结论", "总结", "展望", "conclusion", "summary"],
    }

    def _resolve_section(img_section: str) -> str:
        """Normalize an image's section tag to a canonical key."""
        s = img_section.strip().lower()
        for canonical, aliases in _SECTION_ALIASES.items():
            if s == canonical or any(a in s or s in a for a in aliases):
                return canonical
        return s  # return as-is for unknown sections

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
