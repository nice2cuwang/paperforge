"""Professional SVG infographic templates for article illustrations.

Instead of asking LLM to generate raw SVG code (which produces simple geometric shapes),
this module uses pre-designed templates rendered by Python with structured data extracted
by the LLM. The result is magazine-quality infographics with gradients, glassmorphism,
and professional typography.
"""

from __future__ import annotations

import logging
import math
import textwrap
from typing import Any

logger = logging.getLogger(__name__)

# ── Color Themes ──────────────────────────────────────────────────

THEMES = {
    "ocean": {
        "bg_1": "#0f172a", "bg_2": "#1e3a5f", "bg_3": "#0c4a6e",
        "card_bg": "rgba(255,255,255,0.08)", "card_border": "rgba(255,255,255,0.15)",
        "text_primary": "#f8fafc", "text_secondary": "#94a3b8", "text_accent": "#38bdf8",
        "accent_1": "#0ea5e9", "accent_2": "#06b6d4", "accent_3": "#8b5cf6",
        "glow": "#0ea5e9",
    },
    "emerald": {
        "bg_1": "#022c22", "bg_2": "#064e3b", "bg_3": "#065f46",
        "card_bg": "rgba(255,255,255,0.07)", "card_border": "rgba(255,255,255,0.12)",
        "text_primary": "#f0fdf4", "text_secondary": "#86efac", "text_accent": "#34d399",
        "accent_1": "#10b981", "accent_2": "#14b8a6", "accent_3": "#6366f1",
        "glow": "#10b981",
    },
    "sunset": {
        "bg_1": "#1c1917", "bg_2": "#431407", "bg_3": "#7c2d12",
        "card_bg": "rgba(255,255,255,0.08)", "card_border": "rgba(255,255,255,0.12)",
        "text_primary": "#fff7ed", "text_secondary": "#fdba74", "text_accent": "#fb923c",
        "accent_1": "#f97316", "accent_2": "#ef4444", "accent_3": "#ec4899",
        "glow": "#f97316",
    },
    "indigo": {
        "bg_1": "#0f0a2e", "bg_2": "#1e1b4b", "bg_3": "#312e81",
        "card_bg": "rgba(255,255,255,0.07)", "card_border": "rgba(255,255,255,0.12)",
        "text_primary": "#eef2ff", "text_secondary": "#a5b4fc", "text_accent": "#818cf8",
        "accent_1": "#6366f1", "accent_2": "#8b5cf6", "accent_3": "#ec4899",
        "glow": "#8b5cf6",
    },
}

THEME_KEYS = list(THEMES.keys())

W = 1000
H = 520


# ── Shared SVG Defs ──────────────────────────────────────────────

def _svg_defs(theme: dict, uid: str) -> str:
    """Generate shared SVG defs (gradients, filters, patterns)."""
    return f"""<defs>
  <linearGradient id="bg_{uid}" x1="0%" y1="0%" x2="100%" y2="100%">
    <stop offset="0%" stop-color="{theme['bg_1']}"/>
    <stop offset="50%" stop-color="{theme['bg_2']}"/>
    <stop offset="100%" stop-color="{theme['bg_3']}"/>
  </linearGradient>
  <linearGradient id="accent_{uid}" x1="0%" y1="0%" x2="100%" y2="0%">
    <stop offset="0%" stop-color="{theme['accent_1']}"/>
    <stop offset="100%" stop-color="{theme['accent_2']}"/>
  </linearGradient>
  <linearGradient id="glow_{uid}" x1="0%" y1="0%" x2="0%" y2="100%">
    <stop offset="0%" stop-color="{theme['glow']}" stop-opacity="0.4"/>
    <stop offset="100%" stop-color="{theme['glow']}" stop-opacity="0"/>
  </linearGradient>
  <filter id="glass_{uid}" x="-5%" y="-5%" width="110%" height="110%">
    <feGaussianBlur in="SourceGraphic" stdDeviation="0.5"/>
    <feColorMatrix type="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 0.6 0"/>
  </filter>
  <filter id="shadow_{uid}" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#000" flood-opacity="0.3"/>
  </filter>
  <filter id="neon_{uid}" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <pattern id="dots_{uid}" width="24" height="24" patternUnits="userSpaceOnUse">
    <circle cx="12" cy="12" r="0.8" fill="{theme['text_secondary']}" opacity="0.15"/>
  </pattern>
  <clipPath id="roundClip_{uid}"><rect width="{W}" height="{H}" rx="12"/></clipPath>
</defs>"""


def _background(theme: dict, uid: str) -> str:
    """Generate the background layer with gradient, dot pattern, and decorative shapes."""
    return f"""
  <!-- Background gradient -->
  <rect width="{W}" height="{H}" fill="url(#bg_{uid})" rx="12"/>
  <!-- Dot pattern overlay -->
  <rect width="{W}" height="{H}" fill="url(#dots_{uid})" opacity="0.5"/>
  <!-- Decorative glow orbs -->
  <circle cx="120" cy="80" r="180" fill="{theme['accent_1']}" opacity="0.04"/>
  <circle cx="{W-100}" cy="{H-60}" r="200" fill="{theme['accent_2']}" opacity="0.04"/>
  <circle cx="{W//2}" cy="{H//2}" r="250" fill="{theme['accent_3']}" opacity="0.02"/>
  <!-- Top accent line -->
  <rect x="0" y="0" width="{W}" height="3" fill="url(#accent_{uid})" rx="1"/>
"""


def _glass_card(x: int, y: int, w: int, h: int, theme: dict, uid: str, rx: int = 12) -> str:
    """Render a glassmorphism card."""
    return f"""
  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"
        fill="{theme['card_bg']}" stroke="{theme['card_border']}" stroke-width="1"
        filter="url(#shadow_{uid})"/>
"""


def _escape(text: str) -> str:
    """Escape text for SVG."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


# ── Template 1: Process Flow ──────────────────────────────────────

def render_process_flow(
    title: str,
    subtitle: str,
    steps: list[dict[str, str]],
    theme_key: str = "ocean",
) -> str:
    """Render a step-by-step process flow with glassmorphism cards and gradient connectors.

    steps: [{"label": "步骤名", "detail": "描述", "icon": "emoji"}]
    """
    uid = "pf"
    theme = THEMES.get(theme_key, THEMES["ocean"])
    n = min(len(steps), 5)
    if n == 0:
        return ""

    card_w = 150
    card_h = 160
    gap = 28
    total_w = n * card_w + (n - 1) * gap
    start_x = (W - total_w) // 2
    card_y = 200

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="system-ui, -apple-system, \'Microsoft YaHei\', sans-serif">',
        _svg_defs(theme, uid),
        _background(theme, uid),
    ]

    # Title area
    parts.append(f"""
  <text x="50" y="52" font-size="26" font-weight="700" fill="{theme['text_primary']}">
    {_escape(title)}</text>
  <text x="50" y="80" font-size="14" fill="{theme['text_secondary']}">
    {_escape(subtitle)}</text>
""")

    # Decorative line under title
    parts.append(f"""
  <line x1="50" y1="96" x2="260" y2="96" stroke="url(#accent_{uid})" stroke-width="2.5" stroke-linecap="round"/>
""")

    # Connector line behind cards
    if n > 1:
        line_y = card_y + card_h // 2
        x1 = start_x + card_w // 2
        x2 = start_x + (n - 1) * (card_w + gap) + card_w // 2
        parts.append(f"""
  <line x1="{x1}" y1="{line_y}" x2="{x2}" y2="{line_y}"
        stroke="{theme['accent_1']}" stroke-width="2" stroke-dasharray="8,4" opacity="0.35"/>
""")

    # Step cards
    for i, step in enumerate(steps[:n]):
        cx = start_x + i * (card_w + gap)
        icon = step.get("icon", "⚙")
        label = _escape(_truncate(step.get("label", ""), 10))
        detail = _escape(_truncate(step.get("detail", ""), 28))

        # Step number badge
        badge_x = cx + card_w // 2
        badge_y = card_y - 18

        parts.append(_glass_card(cx, card_y, card_w, card_h, theme, uid))

        # Step number circle
        parts.append(f"""
  <circle cx="{badge_x}" cy="{badge_y}" r="16"
          fill="url(#accent_{uid})" filter="url(#shadow_{uid})"/>
  <text x="{badge_x}" y="{badge_y + 5}" text-anchor="middle"
        font-size="13" font-weight="700" fill="#fff">{i + 1}</text>
""")

        # Icon
        parts.append(f"""
  <text x="{cx + card_w // 2}" y="{card_y + 52}" text-anchor="middle" font-size="32">{icon}</text>
""")

        # Label
        parts.append(f"""
  <text x="{cx + card_w // 2}" y="{card_y + 88}" text-anchor="middle"
        font-size="15" font-weight="600" fill="{theme['text_primary']}">{label}</text>
""")

        # Detail text (wrapped)
        detail_lines = textwrap.wrap(detail, width=12) if detail else []
        for j, dl in enumerate(detail_lines[:2]):
            parts.append(f"""
  <text x="{cx + card_w // 2}" y="{card_y + 110 + j * 17}" text-anchor="middle"
        font-size="11" fill="{theme['text_secondary']}">{_escape(dl)}</text>
""")

        # Arrow connector between cards
        if i < n - 1:
            ax = cx + card_w + 4
            ay = card_y + card_h // 2
            parts.append(f"""
  <polygon points="{ax},{ay - 5} {ax + 18},{ay} {ax},{ay + 5}"
           fill="{theme['accent_1']}" opacity="0.6"/>
""")

    # Bottom decoration
    parts.append(f"""
  <text x="{W - 50}" y="{H - 20}" text-anchor="end" font-size="10"
        fill="{theme['text_secondary']}" opacity="0.4">PaperForge Auto-Illustration</text>
</svg>""")

    return "\n".join(parts)


# ── Template 2: Comparison Dashboard ─────────────────────────────

def render_comparison(
    title: str,
    subtitle: str,
    items: list[dict[str, Any]],
    theme_key: str = "indigo",
) -> str:
    """Render a comparison dashboard with metric bars.

    items: [{"label": "模型A", "score": 83.1, "tag": "DeepSeek"}, {"label": "模型B", "score": 79.7, "tag": "Claude"}]
    """
    uid = "cmp"
    theme = THEMES.get(theme_key, THEMES["indigo"])
    n = min(len(items), 4)
    if n == 0:
        return ""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="system-ui, -apple-system, \'Microsoft YaHei\', sans-serif">',
        _svg_defs(theme, uid),
        _background(theme, uid),
    ]

    # Title
    parts.append(f"""
  <text x="50" y="52" font-size="26" font-weight="700" fill="{theme['text_primary']}">
    {_escape(title)}</text>
  <text x="50" y="80" font-size="14" fill="{theme['text_secondary']}">
    {_escape(subtitle)}</text>
  <line x1="50" y1="96" x2="260" y2="96" stroke="url(#accent_{uid})" stroke-width="2.5" stroke-linecap="round"/>
""")

    # Metric bars
    max_score = max((item.get("score", 0) for item in items[:n]), default=100)
    if max_score == 0:
        max_score = 100
    bar_max_w = 420
    bar_h = 36
    bar_y_start = 130
    bar_gap = 22

    for i, item in enumerate(items[:n]):
        label = _escape(_truncate(item.get("label", ""), 18))
        score = item.get("score", 0)
        tag = _escape(_truncate(item.get("tag", ""), 14))
        bar_w = int(bar_max_w * (score / max_score))
        by = bar_y_start + i * (bar_h + bar_gap)

        # Label
        parts.append(f"""
  <text x="50" y="{by + 23}" font-size="14" font-weight="600" fill="{theme['text_primary']}">{label}</text>
""")

        # Bar background
        parts.append(f"""
  <rect x="280" y="{by + 4}" width="{bar_max_w}" height="{bar_h}" rx="8"
        fill="{theme['card_bg']}" stroke="{theme['card_border']}" stroke-width="0.5"/>
""")

        # Bar fill with gradient
        parts.append(f"""
  <rect x="280" y="{by + 4}" width="{bar_w}" height="{bar_h}" rx="8"
        fill="url(#accent_{uid})" opacity="0.85"/>
""")

        # Score value
        parts.append(f"""
  <text x="{280 + bar_w + 12}" y="{by + 27}" font-size="16" font-weight="700"
        fill="{theme['text_accent']}">{score}</text>
""")

        # Tag badge
        if tag:
            tag_x = 280 + bar_max_w + 60
            parts.append(f"""
  <rect x="{tag_x}" y="{by + 8}" width="{len(tag) * 10 + 16}" height="24" rx="12"
        fill="{theme['accent_3']}" opacity="0.2"/>
  <text x="{tag_x + (len(tag) * 10 + 16) // 2}" y="{by + 24}" text-anchor="middle"
        font-size="11" fill="{theme['text_secondary']}">{tag}</text>
""")

    # Decorative element - vertical stat summary
    summary_x = W - 180
    parts.append(_glass_card(summary_x, bar_y_start, 140, n * (bar_h + bar_gap) - bar_gap + 30, theme, uid))
    parts.append(f"""
  <text x="{summary_x + 70}" y="{bar_y_start + 24}" text-anchor="middle"
        font-size="12" font-weight="600" fill="{theme['text_accent']}">数据对比</text>
""")
    for i, item in enumerate(items[:n]):
        score = item.get("score", 0)
        iy = bar_y_start + 50 + i * 28
        parts.append(f"""
  <text x="{summary_x + 16}" y="{iy}" font-size="22" font-weight="700" fill="{theme['text_primary']}">{score}</text>
  <text x="{summary_x + 16}" y="{iy + 16}" font-size="10" fill="{theme['text_secondary']}">{_escape(_truncate(item.get('label', ''), 10))}</text>
""")

    parts.append(f"""
  <text x="{W - 50}" y="{H - 20}" text-anchor="end" font-size="10"
        fill="{theme['text_secondary']}" opacity="0.4">PaperForge Auto-Illustration</text>
</svg>""")

    return "\n".join(parts)


# ── Template 3: Key Metrics Dashboard ────────────────────────────

def render_metrics_dashboard(
    title: str,
    subtitle: str,
    metrics: list[dict[str, Any]],
    theme_key: str = "emerald",
) -> str:
    """Render a metrics dashboard with stat cards.

    metrics: [{"label": "MMLU", "value": "83.1", "unit": "分", "delta": "+4.2%", "icon": "📊"}]
    """
    uid = "md"
    theme = THEMES.get(theme_key, THEMES["emerald"])
    n = min(len(metrics), 6)
    if n == 0:
        return ""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="system-ui, -apple-system, \'Microsoft YaHei\', sans-serif">',
        _svg_defs(theme, uid),
        _background(theme, uid),
    ]

    # Title
    parts.append(f"""
  <text x="50" y="52" font-size="26" font-weight="700" fill="{theme['text_primary']}">
    {_escape(title)}</text>
  <text x="50" y="80" font-size="14" fill="{theme['text_secondary']}">
    {_escape(subtitle)}</text>
  <line x1="50" y1="96" x2="260" y2="96" stroke="url(#accent_{uid})" stroke-width="2.5" stroke-linecap="round"/>
""")

    # Grid layout for metric cards
    cols = min(n, 3)
    rows = math.ceil(n / cols)
    card_w = 240
    card_h = 130
    gap_x = 24
    gap_y = 20
    grid_w = cols * card_w + (cols - 1) * gap_x
    start_x = (W - grid_w) // 2
    start_y = 120

    for i, metric in enumerate(metrics[:n]):
        col = i % cols
        row = i // cols
        cx = start_x + col * (card_w + gap_x)
        cy = start_y + row * (card_h + gap_y)

        icon = metric.get("icon", "📊")
        label = _escape(_truncate(metric.get("label", ""), 14))
        value = _escape(str(metric.get("value", "")))
        unit = _escape(metric.get("unit", ""))
        delta = metric.get("delta", "")

        parts.append(_glass_card(cx, cy, card_w, card_h, theme, uid, rx=14))

        # Icon and label
        parts.append(f"""
  <text x="{cx + 20}" y="{cy + 32}" font-size="22">{icon}</text>
  <text x="{cx + 50}" y="{cy + 32}" font-size="13" font-weight="500"
        fill="{theme['text_secondary']}">{label}</text>
""")

        # Value
        parts.append(f"""
  <text x="{cx + 20}" y="{cy + 75}" font-size="32" font-weight="800"
        fill="{theme['text_primary']}">{value}</text>
  <text x="{cx + 20 + len(value) * 18}" y="{cy + 75}" font-size="14"
        fill="{theme['text_secondary']}">{unit}</text>
""")

        # Delta badge
        if delta:
            delta_color = "#4ade80" if delta.startswith("+") else "#f87171"
            parts.append(f"""
  <rect x="{cx + card_w - 70}" y="{cy + 58}" width="56" height="24" rx="12"
        fill="{delta_color}" opacity="0.15"/>
  <text x="{cx + card_w - 42}" y="{cy + 74}" text-anchor="middle"
        font-size="12" font-weight="600" fill="{delta_color}">{_escape(delta)}</text>
""")

        # Subtle bottom border accent
        parts.append(f"""
  <line x1="{cx + 16}" y1="{cy + card_h - 2}" x2="{cx + card_w - 16}" y2="{cy + card_h - 2}"
        stroke="url(#accent_{uid})" stroke-width="2" stroke-linecap="round" opacity="0.5"/>
""")

    parts.append(f"""
  <text x="{W - 50}" y="{H - 20}" text-anchor="end" font-size="10"
        fill="{theme['text_secondary']}" opacity="0.4">PaperForge Auto-Illustration</text>
</svg>""")

    return "\n".join(parts)


# ── Template 4: Architecture / Layer Diagram ─────────────────────

def render_architecture(
    title: str,
    subtitle: str,
    layers: list[dict[str, str]],
    theme_key: str = "sunset",
) -> str:
    """Render a layered architecture diagram.

    layers: [{"label": "应用层", "detail": "视觉问答 / OCR / 文档理解", "icon": "🖥"}]
    """
    uid = "arch"
    theme = THEMES.get(theme_key, THEMES["sunset"])
    n = min(len(layers), 5)
    if n == 0:
        return ""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="system-ui, -apple-system, \'Microsoft YaHei\', sans-serif">',
        _svg_defs(theme, uid),
        _background(theme, uid),
    ]

    # Title
    parts.append(f"""
  <text x="50" y="52" font-size="26" font-weight="700" fill="{theme['text_primary']}">
    {_escape(title)}</text>
  <text x="50" y="80" font-size="14" fill="{theme['text_secondary']}">
    {_escape(subtitle)}</text>
  <line x1="50" y1="96" x2="260" y2="96" stroke="url(#accent_{uid})" stroke-width="2.5" stroke-linecap="round"/>
""")

    # Layered blocks (top to bottom, first layer at top)
    layer_w = 520
    layer_h = 58
    gap = 12
    total_h = n * layer_h + (n - 1) * gap
    start_y = ((H - 100) - total_h) // 2 + 110
    lx = (W - layer_w) // 2

    for i, layer in enumerate(layers[:n]):
        ly = start_y + i * (layer_h + gap)
        label = _escape(_truncate(layer.get("label", ""), 16))
        detail = _escape(_truncate(layer.get("detail", ""), 30))
        icon = layer.get("icon", "⚙")

        # Progressively change color from accent to secondary
        opacity = 0.9 - i * 0.12

        parts.append(f"""
  <rect x="{lx}" y="{ly}" width="{layer_w}" height="{layer_h}" rx="10"
        fill="{theme['card_bg']}" stroke="{theme['card_border']}" stroke-width="1"
        filter="url(#shadow_{uid})" opacity="{opacity}"/>

  <!-- Left accent bar -->
  <rect x="{lx}" y="{ly}" width="5" height="{layer_h}" rx="2"
        fill="url(#accent_{uid})" opacity="{opacity}"/>

  <!-- Icon -->
  <text x="{lx + 30}" y="{ly + 35}" font-size="22">{icon}</text>

  <!-- Label -->
  <text x="{lx + 62}" y="{ly + 27}" font-size="16" font-weight="700"
        fill="{theme['text_primary']}">{label}</text>

  <!-- Detail -->
  <text x="{lx + 62}" y="{ly + 46}" font-size="12" fill="{theme['text_secondary']}">{detail}</text>
""")

        # Connector arrow between layers
        if i < n - 1:
            arrow_y = ly + layer_h + 1
            arrow_x = lx + layer_w // 2
            parts.append(f"""
  <line x1="{arrow_x}" y1="{arrow_y}" x2="{arrow_x}" y2="{arrow_y + gap - 2}"
        stroke="{theme['accent_1']}" stroke-width="1.5" opacity="0.4"/>
  <polygon points="{arrow_x - 4},{arrow_y + gap - 5} {arrow_x},{arrow_y + gap} {arrow_x + 4},{arrow_y + gap - 5}"
           fill="{theme['accent_1']}" opacity="0.5"/>
""")

    # Side annotation panel
    panel_x = lx + layer_w + 40
    panel_w = W - panel_x - 40
    if panel_w > 100:
        parts.append(_glass_card(panel_x, start_y, panel_w, total_h, theme, uid, rx=10))
        parts.append(f"""
  <text x="{panel_x + 16}" y="{start_y + 26}" font-size="13" font-weight="600"
        fill="{theme['text_accent']}">技术亮点</text>
""")
        # Add bullet points from layers
        for i, layer in enumerate(layers[:n]):
            bullet_y = start_y + 52 + i * 24
            label = _escape(_truncate(layer.get("label", ""), 12))
            parts.append(f"""
  <circle cx="{panel_x + 22}" cy="{bullet_y - 4}" r="3" fill="{theme['accent_1']}" opacity="0.7"/>
  <text x="{panel_x + 32}" y="{bullet_y}" font-size="11" fill="{theme['text_secondary']}">{label}</text>
""")

    parts.append(f"""
  <text x="{W - 50}" y="{H - 20}" text-anchor="end" font-size="10"
        fill="{theme['text_secondary']}" opacity="0.4">PaperForge Auto-Illustration</text>
</svg>""")

    return "\n".join(parts)


# ── Section → Template Router ────────────────────────────────────

SECTION_TEMPLATE_MAP = {
    "问题引入": ("process_flow", "ocean"),
    "问题界定": ("process_flow", "ocean"),
    "关键发现": ("comparison", "indigo"),
    "证据对比": ("comparison", "indigo"),
    "案例与启发": ("architecture", "sunset"),
    "行动建议": ("process_flow", "emerald"),
    "实施路径": ("process_flow", "emerald"),
    "政策建议": ("metrics_dashboard", "emerald"),
    "风险与限制": ("metrics_dashboard", "sunset"),
    "研究脉络": ("architecture", "ocean"),
    "核心争议": ("comparison", "sunset"),
    "研究空白": ("metrics_dashboard", "indigo"),
    "引言": ("process_flow", "ocean"),
    "方法与证据": ("architecture", "indigo"),
    "结果": ("metrics_dashboard", "emerald"),
    "讨论": ("comparison", "indigo"),
    "局限": ("metrics_dashboard", "sunset"),
    "结论": ("metrics_dashboard", "emerald"),
    "结语": ("metrics_dashboard", "emerald"),
}


def get_template_for_section(section: str) -> tuple[str, str]:
    """Return (template_type, theme_key) for a given section name."""
    section = section.strip()
    if section in SECTION_TEMPLATE_MAP:
        return SECTION_TEMPLATE_MAP[section]
    # Fuzzy match
    for key, value in SECTION_TEMPLATE_MAP.items():
        if key in section or section in key:
            return value
    return ("metrics_dashboard", "ocean")
