"""Web search service for gathering evidence from internet sources.

Uses DuckDuckGo for search (no API key required) with Bing as a fallback
when DuckDuckGo is unavailable (e.g. blocked by GFW).  httpx + BeautifulSoup
for page content extraction. Creates "virtual paper" records with source_type="web".
"""

from __future__ import annotations

import logging
import os as _os
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Timeout for web page fetches
WEB_FETCH_TIMEOUT = 15.0
SEARCH_TIMEOUT = 20.0
MAX_WEB_RESULTS = 12

# search_web(recency=...) → DDGS.text(timelimit=...) 映射
_RECENCY_TO_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}


# ── 发布时间解析 ──────────────────────────────────────────────────────────
# 搜索结果与页面里常见三种时间表达：绝对日期、相对日期（"6 天之前"）、
# 元数据（<time datetime> / article:published_time）。时效话题的排序与
# 写作都依赖它 —— 解析不出时返回 None，调用方按"时间未知"处理。

_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"), "ymd"),
    # 美式 "Aug 15, 2026"
    (re.compile(r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s*,?\s+(\d{4})", re.I), "mdy_first"),
    # 欧式 "15 Aug 2026"
    (re.compile(r"(\d{1,2})\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*,?\s+(\d{4})", re.I), "dmy"),
)

_MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_REL_DATE_RE = re.compile(r"(\d+)\s*(秒|分|小?时|天|日|周|星期|月|年|week|day|hour|minute|month|year)s?\s*(之前|前|ago)", re.I)
_ABBR_WEEK = {"w": 7, "week": 7, "周": 7, "星期": 7}
_ABBRV_DAY = {"d": 1, "day": 1, "天": 1, "日": 1}


def _extract_date_hint(text: str | None) -> str | None:
    """从 snippet / 页面文本中尽力解析发布日期，返回 ISO 字符串（仅日期）。"""
    if not text:
        return None
    sample = text[:400]

    match = _REL_DATE_RE.search(sample)
    if match:
        try:
            value = int(match.group(1))
            unit = (match.group(2) or match.group(0)).lower()
            now = datetime.now()
            if "秒" in unit or "second" in unit:
                delta = timedelta(seconds=value)
            elif "分" in unit or "minute" in unit:
                delta = timedelta(minutes=value)
            elif "时" in unit or "hour" in unit:
                delta = timedelta(hours=value)
            elif "周" in unit or "星期" in unit or "week" in unit or unit == "w":
                delta = timedelta(days=value * 7)
            elif "月" in unit or "month" in unit:
                delta = timedelta(days=value * 30)
            elif "年" in unit or "year" in unit:
                delta = timedelta(days=value * 365)
            else:
                delta = timedelta(days=value)
            return (now - delta).strftime("%Y-%m-%d")
        except Exception:
            pass

    match = _DATE_PATTERNS[0][0].search(sample)
    if match:
        try:
            y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 2000 <= y <= datetime.now().year + 1 and 1 <= m <= 12 and 1 <= d <= 31:
                return f"{y}-{m:02d}-{d:02d}"
        except Exception:
            pass

    for pattern, order in _DATE_PATTERNS[1:]:
        match = pattern.search(sample)
        if not match:
            continue
        if order == "mdy_first":
            day_raw, year_raw, month_raw = match.group(1), match.group(2), match.group(0)
        else:  # dmy
            day_raw, year_raw, month_raw = match.group(1), match.group(2), match.group(0)
        month_token = re.sub(r"[^a-z]", "", month_raw.lower())
        month_token = next((tok[:3] for tok in re.findall(r"[a-z]{3,}", month_token) if tok[:3] in _MONTHS_EN), "")
        month = _MONTHS_EN.get(month_token)
        try:
            if month:
                return f"{int(year_raw)}-{month:02d}-{int(day_raw):02d}"
        except Exception:
            pass
    return None


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except Exception:
        return None


def _normalize_date_str(value: str | None) -> str | None:
    """把 DDG date 字段 / 各种表达规整为 ISO 日期，解析失败返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    parsed = _parse_iso_date(text)
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    iso = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return iso.strftime("%Y-%m-%d")

_SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# ── Domain blocklist ──────────────────────────────────────────────────────
# Domains that rarely contain useful article content (login walls, shops,
# social-media profiles, link-aggregators, etc.).  Matching is suffix-based
# so subdomains are caught too.
_BLOCKED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    # Social / profile sites (login walls or thin content)
    "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "tiktok.com", "pinterest.com",
    # E-commerce / shops
    "amazon.com", "ebay.com", "etsy.com", "satyajewelry.com",
    "alibaba.com", "taobao.com", "jd.com",
    # Link aggregators / Q&A with thin scrape
    "reddit.com", "t.co", "bit.ly", "tinyurl.com",
    # Video-only platforms (no readable text)
    "youtube.com", "youtu.be", "bilibili.com", "vimeo.com",
    # App stores / download pages
    "apps.apple.com", "play.google.com",
    # Signup / auth portals
    "signup.live.com", "login.live.com", "accounts.google.com",
    "account.microsoft.com", "login.microsoftonline.com",
)


def _is_blocked_domain(url: str) -> bool:
    """Return True if *url* belongs to the domain blocklist."""
    domain = _extract_domain(url)
    return any(domain.endswith(suffix) for suffix in _BLOCKED_DOMAIN_SUFFIXES)


def search_web(
    query: str,
    max_results: int = MAX_WEB_RESULTS,
    recency: str | None = None,
) -> list[dict[str, Any]]:
    """Search the web using DuckDuckGo, falling back to Bing when unavailable.

    Args:
        recency: 可选时效窗口 "day"/"week"/"month"/"year"，映射为 DDG 的
            ``timelimit``，让时效话题拿到最新结果而非 SEO 权重最高的旧文。
            Bing 回退不支持时效过滤，传了 recency 时跳过回退（宁缺旧滥）。

    Returns a list of dicts with: title, url, snippet, full_text, source_domain, published
    """
    timelimit = _RECENCY_TO_TIMELIMIT.get(recency or "")

    # ── 1) Try DuckDuckGo first ──────────────────────────────────────────
    results = _search_duckduckgo(query, max_results, timelimit=timelimit)
    if results:
        logger.info(
            "DuckDuckGo returned %d results for query=%r recency=%s",
            len(results), query[:60], recency,
        )
        return _filter_results(results)

    if timelimit:
        # DDG 不可用（常见：代理出口 IP 被限流）。回退 Bing 但按已解析的
        # published 日期后过滤：可证明超出时效窗口的结果丢弃，日期未知者
        # 保留（无法判定过期），交由上层按新鲜度排序。
        window_days = {"d": 1, "w": 7, "m": 30, "y": 365}[timelimit]
        bing = _filter_results(_search_bing(query, max_results))
        kept: list[dict[str, Any]] = []
        dropped = 0
        for r in bing:
            published = _parse_iso_date(r.get("published"))
            if published and (datetime.now() - published).days > window_days:
                dropped += 1
                continue
            kept.append(r)
        logger.info(
            "DDG unavailable; Bing recency post-filter kept %d / dropped %d for query=%r recency=%s",
            len(kept), dropped, query[:60], recency,
        )
        return kept

    # ── 2) Fallback to Bing (accessible in mainland China w/o proxy) ─────
    logger.info("DuckDuckGo returned 0 results, falling back to Bing for query=%r", query[:60])
    results = _search_bing(query, max_results)
    if results:
        logger.info("Bing returned %d results for query=%r", len(results), query[:60])
    else:
        logger.warning("Both DuckDuckGo and Bing returned 0 results for query=%r", query[:60])
    return _filter_results(results)


# ── 导览页/下载页过滤 ──────────────────────────────────────────────────────
# Bing 中文兜底常返回官网镜像/下载站的 SEO 导览页（"XXX官网-官方下载入口"、
# "加入平台访问模型"），无任何主题相关内容。这类页面占据证据位比没有更糟。
# 分两级特征：
#   强导览模式 —— "官方下载/网页版入口/Join X API platform" 等，从不是新闻标题；
#   镜像模式   —— "官网"后紧跟分隔符（"DeepSeek官网-DeepSeekAI"），区别于
#                 正常新闻的"官网发布/官网公告"（"官网"后接动词）。
_STRONG_NAV_RE = re.compile(
    r"(官方下载|下载入口|网页版入口|客户端下载|官方入口|正式版发布是什么|"
    r"探索未至之境|join\s+\w+\s+api\s+platform|how\s+to\s+(start|use)\s+\w+)",
    re.I,
)
_MIRROR_TITLE_RE = re.compile(r"官网\s*[-|–—]\s*|官网\s*$|[-|–—]\s*官网", re.I)
_NAV_DOMAIN_HINTS = (
    "-mc.com.cn", "app-", "download.", "agents-",  # 镜像站命名特征
)


def _is_landing_page(result: dict[str, Any]) -> bool:
    """判定是否官网镜像/导览页（无主题内容的 SEO 页面）。"""
    title = str(result.get("title") or "").strip()
    snippet = str(result.get("snippet") or "").strip()
    domain = str(result.get("source_domain") or "")
    if not title:
        return False

    strong = bool(_STRONG_NAV_RE.search(title))
    mirror = bool(_MIRROR_TITLE_RE.search(title))
    if not (strong or mirror):
        return False

    # 镜像站域名特征直接判死
    if any(domain.startswith(h) or domain.endswith(h) for h in _NAV_DOMAIN_HINTS):
        return True
    # 强导览模式（下载入口/Join platform…）不会是新闻标题
    if strong:
        return True
    # 镜像模式：snippet 为空或极短（导览页通常只有口号）才判死；
    # 带具体报道内容的保留（如"官网发布涨价公告"的新闻）。
    return not snippet or len(snippet) < 40


def _filter_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove blocked domains and deduplicate by domain (max 3 per domain)."""
    filtered: list[dict[str, Any]] = []
    domain_counts: dict[str, int] = {}
    dropped_nav = 0
    for r in results:
        url = r.get("url", "")
        if not url or _is_blocked_domain(url):
            continue
        if _is_landing_page(r):
            dropped_nav += 1
            continue
        domain = r.get("source_domain") or _extract_domain(url)
        count = domain_counts.get(domain, 0)
        if count >= 3:  # cap results per domain
            continue
        domain_counts[domain] = count + 1
        filtered.append(r)
    if dropped_nav:
        logger.info("Dropped %d landing/navigation pages from web results", dropped_nav)
    return filtered


# ────────────────────────── DuckDuckGo ────────────────────────────────────

def _search_duckduckgo(
    query: str,
    max_results: int,
    timelimit: str | None = None,
) -> list[dict[str, Any]]:
    from app.services.http_client import resolve_proxy_url

    results: list[dict[str, Any]] = []

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        logger.warning("duckduckgo-search not installed, skipping DuckDuckGo")
        return results

    proxy_url = resolve_proxy_url()

    # The DDGS library uses primp (a Rust/reqwest-based client) which does not
    # always honour an explicit ``proxy=`` kwarg when running inside Docker.
    # Setting ``DDGS_PROXY`` as an env-var is the most reliable way to route
    # traffic through the proxy.
    if proxy_url:
        _os.environ["DDGS_PROXY"] = proxy_url
    else:
        _os.environ.pop("DDGS_PROXY", None)

    # DDG 的反爬限流（202 Ratelimit）与代理节点抖动通常是瞬时的：
    # 短退避后重试一次可以救回大部分"间歇性 0 结果"，避免整个 web
    # 证据链在时效话题下直接断供。
    import time as _time

    raw_results: list[dict[str, Any]] = []
    for attempt in range(2):
        try:
            with DDGS(timeout=SEARCH_TIMEOUT, proxy=proxy_url) as ddgs:
                raw_results = list(
                    ddgs.text(query, max_results=max_results, timelimit=timelimit)
                )
            break
        except Exception as exc:
            logger.warning(
                "DuckDuckGo search attempt %d failed (%s: %s)",
                attempt + 1, type(exc).__name__, str(exc)[:120],
            )
            if attempt == 0:
                _time.sleep(3.0)

    for item in raw_results:
        title = (item.get("title") or "").strip()
        url = (item.get("href") or "").strip()
        snippet = (item.get("body") or "").strip()
        if not url or not title:
            continue
        # DDG 部分结果带发布日期字段；缺省时从 snippet 里猜（"6 天之前 ·"）
        published = (item.get("date") or "").strip() or _extract_date_hint(snippet)
        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_domain": _extract_domain(url),
            "source_type": "web",
            "full_text": None,
            "published": _normalize_date_str(published),
        })

    return results


# ────────────────────────── Bing (fallback) ───────────────────────────────

def _search_bing(query: str, max_results: int) -> list[dict[str, Any]]:
    """Scrape Bing web-search results via httpx + BeautifulSoup.

    Bing is accessible in mainland China without a proxy, making it a
    reliable fallback when DuckDuckGo is blocked.
    """
    from app.services.http_client import resolve_proxy_url

    results: list[dict[str, Any]] = []
    proxy_url = resolve_proxy_url()

    # Bing works both with and without proxy; prefer direct connection
    # when proxy is unreliable (TLS errors are common with some nodes).
    search_url = "https://www.bing.com/search"

    client_kwargs: dict[str, Any] = {
        "timeout": SEARCH_TIMEOUT,
        "follow_redirects": True,
        "verify": False,
        "headers": _SEARCH_HEADERS,
    }
    # Try direct first, then with proxy if available
    attempts: list[dict[str, Any]] = [dict(client_kwargs)]  # direct
    if proxy_url:
        attempts.append({**client_kwargs, "proxy": proxy_url})

    # 多组参数组合（国际版 + 中国版市场）依次尝试：www.bing.com 在部分
    # 网络下会返回空壳页（无 b_algo 结果），cn.bing.com + mkt 参数可救回。
    param_variants: list[dict[str, str]] = [
        {"q": query, "count": str(min(max_results, 30)), "setlang": "zh-Hans"},
        {"q": query, "count": str(min(max_results, 30)), "setlang": "zh-Hans", "mkt": "zh-CN"},
    ]

    html = ""
    for kwargs in attempts:
        for params in param_variants:
            try:
                with httpx.Client(**kwargs) as client:
                    resp = client.get(search_url, params=params)
                    resp.raise_for_status()
                    html = resp.text
                    if html and len(html) > 500 and "b_algo" in html:
                        break
            except Exception:
                logger.debug(
                    "Bing attempt failed (%s)",
                    "direct" if "proxy" not in kwargs else "proxy",
                    exc_info=True,
                )
        if html and len(html) > 500 and "b_algo" in html:
            break

    if not html:
        logger.warning("Bing search returned no HTML")
        return results

    soup = BeautifulSoup(html, "html.parser")

    # Bing wraps each organic result in <li class="b_algo">
    for li in soup.select("li.b_algo"):
        a_tag = li.select_one("h2 a")
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        url = a_tag.get("href", "")
        if not title or not url:
            continue

        # Snippet lives in <p> inside <div class="b_caption">
        snippet = ""
        caption = li.select_one(".b_caption p") or li.select_one("p")
        if caption:
            snippet = caption.get_text(strip=True)

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
            "source_domain": _extract_domain(url),
            "source_type": "web",
            "full_text": None,
            "published": _extract_date_hint(snippet),
        })

        if len(results) >= max_results:
            break

    return results


def fetch_page_text(url: str, timeout: float = WEB_FETCH_TIMEOUT) -> str | None:
    """Fetch and extract readable text from a web page."""
    details = fetch_page_details(url, timeout=timeout)
    return details.get("text") or None


_PUBLISHED_META_RE = re.compile(
    r'(?:article:published_time|og:updated_time|og:published_time|pubdate|publishdate|datePublished)["\']?\s*[:=]\s*["\']?(\d{4}-\d{2}-\d{2})',
    re.I,
)
_TIME_TAG_RE = re.compile(r'<time[^>]+datetime=["\'](\d{4}-\d{2}-\d{2})', re.I)


def fetch_page_details(url: str, timeout: float = WEB_FETCH_TIMEOUT) -> dict[str, Any]:
    """Fetch a page once, returning both readable text and a publish date.

    Publish date comes from common meta tags (article:published_time 等) 或
    <time datetime>；都缺失时退回正则扫 HTML 头部的 ISO 日期。时效排序
    依赖该字段，抓不到则为 None。
    """
    from app.services.http_client import resolve_proxy_url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        proxy_url = resolve_proxy_url()
        client_kwargs: dict[str, Any] = {
            "timeout": timeout,
            "follow_redirects": True,
            "verify": False,
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except Exception:
        logger.debug("Failed to fetch page: %s", url, exc_info=True)
        return {"text": None, "published": None}

    published: str | None = None
    head = html[:20000]
    match = _PUBLISHED_META_RE.search(head) or _TIME_TAG_RE.search(head)
    if match:
        published = match.group(1)
    if not published:
        published = _extract_date_hint(head[:2000])

    return {"text": _extract_text_from_html(html), "published": published}


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, removing scripts, styles, and navigation."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements by tag name
    for tag in soup.find_all([
        "script", "style", "nav", "header", "footer", "aside",
        "iframe", "noscript", "svg", "form",
    ]):
        tag.decompose()

    # Remove common noise containers by class / id / role
    _noise_patterns = re.compile(
        r"(login|sign.?in|sign.?up|subscribe|newsletter|cookie|consent|"
        r"social|share|comment|sidebar|widget|ad[sz]?|banner|popup|"
        r"modal|overlay|menu|navbar|breadcrumb|pagination|related|"
        r"recommend|footer|copyright|disclaimer)",
        re.I,
    )
    for tag in soup.find_all(attrs={"class": _noise_patterns}):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": _noise_patterns}):
        tag.decompose()
    for tag in soup.find_all(attrs={"role": re.compile(r"(navigation|banner|complementary|contentinfo)", re.I)}):
        tag.decompose()

    # Try to find main content area
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.find(attrs={"class": re.compile(r"(content|article|post|entry|body|text)", re.I)})
    )

    if main_content:
        text = main_content.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # Clean up: remove excessive blank lines and whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    # Filter out very short lines (likely UI elements) and login-related noise
    _noise_line_patterns = re.compile(
        r"^(登录|注册|密码|邮箱|手机|忘记密码|立即加入|用户协议|隐私政策|"
        r"Cookie|Sign in|Sign up|Log in|Log out|Password|Forgot|"
        r"Share|Facebook|Twitter|LinkedIn|Email$|Subscribe|Newsletter|"
        r"©|Copyright|All rights reserved)",
        re.I,
    )
    lines = [
        line for line in lines
        if len(line) > 15 and not _noise_line_patterns.search(line)
    ]

    cleaned = "\n".join(lines)

    # Limit to ~5000 chars to avoid excessive token usage
    return cleaned[:5000]


def _extract_domain(url: str) -> str:
    """Extract domain name from URL."""
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "unknown"


def build_web_evidence(
    project_id: str,
    web_results: list[dict[str, Any]],
    db: Any,
) -> list[Any]:
    """Create Paper + EvidenceCard records from web search results.

    Creates lightweight "virtual paper" records for web sources, then
    builds evidence cards from the content.
    """
    from app.models import Paper, EvidenceCard
    from app.services.evidence_service import build_evidence_from_chunks, infer_evidence_type, infer_strength
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    created_evidence = []

    for result in web_results:
        title = result["title"]
        url = result["url"]
        snippet = result.get("snippet", "")
        full_text = result.get("full_text")

        # ── Quality gate: skip thin / noisy content ──────────────────────
        text_for_evidence = full_text if full_text and len(full_text) > 100 else snippet
        snippet_only = not (full_text and len(full_text) > 100)
        # 页面抓取失败时退化为 snippet 级证据卡（时效话题断供比弱证据更糟），
        # 但 snippet 本身也要有最低信息量。
        min_chars = 30 if snippet_only else 80
        if not text_for_evidence or len(text_for_evidence) < min_chars:
            logger.debug("Skipping %s — text too short (%d chars)", url, len(text_for_evidence or ""))
            continue

        # Quick heuristic: if more than 40% of lines are < 30 chars,
        # this is likely navigation / UI noise rather than article content.
        # （snippet 是单行摘要，不做该检测）
        if not snippet_only:
            text_lines = [l for l in text_for_evidence.splitlines() if l.strip()]
            if text_lines:
                short_ratio = sum(1 for l in text_lines if len(l.strip()) < 30) / len(text_lines)
                if short_ratio > 0.4:
                    logger.debug("Skipping %s — short-line ratio %.2f (likely noise)", url, short_ratio)
                    continue

        # Create a virtual Paper record for this web source
        paper_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            project_id=project_id,
            title=title,
            authors=[],
            year=None,
            doi=None,
            arxiv_id=None,
            venue=result.get("source_domain", "web"),
            abstract=snippet[:2000] if snippet else None,
            source="web_search",
            source_type="web",
            source_url=url,
            pdf_url=None,
            oa_status=None,
            license=None,
            local_pdf_path=None,
            local_tei_path=None,
            relevance_score=0.5,
            selected=True,
            parse_status="parsed",
            metadata_json={
                "source_domain": result.get("source_domain", ""),
                "web_url": url,
                "published_hint": result.get("published"),
            },
            created_at=now,
            updated_at=now,
        )
        db.add(paper)

        # Create chunk-like payload for evidence building
        chunk_payload = [{
            "id": str(uuid4()),
            "text": text_for_evidence[:2400],
            "page_start": None,
            "page_end": None,
        }]

        evidence_items = build_evidence_from_chunks(paper_id, chunk_payload, limit=3)
        for item in evidence_items:
            ev = EvidenceCard(
                id=str(uuid4()),
                project_id=project_id,
                paper_id=paper_id,
                chunk_ids=item["chunk_ids"],
                claim=item["claim"],
                supporting_text=item["supporting_text"],
                evidence_type=item.get("evidence_type", "web_source"),
                source_type="web",
                strength="low" if snippet_only else item.get("strength", "medium"),
                limitations=(
                    "Snippet-only web evidence (page fetch failed); verify before citing."
                    if snippet_only
                    else "Web source evidence; credibility should be verified."
                ),
                page_start=None,
                page_end=None,
                citation_key=None,
                used_in_draft=False,
                created_at=now,
                updated_at=now,
            )
            db.add(ev)
            created_evidence.append(ev)

    return created_evidence
