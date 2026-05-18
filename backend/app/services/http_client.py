from __future__ import annotations

import os
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx


def _ensure_scheme(value: str) -> str:
    if "://" in value:
        return value
    return f"http://{value}"


def _rebuild_netloc(parsed: SplitResult, host: str) -> str:
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
    host_part = host
    if parsed.port:
        host_part = f"{host_part}:{parsed.port}"
    return f"{userinfo}@{host_part}" if userinfo else host_part


def normalize_proxy_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return raw

    parsed = urlsplit(_ensure_scheme(raw))
    host = (parsed.hostname or "").strip().lower()
    if host in {"127.0.0.1", "localhost"}:
        replacement = (os.getenv("PAPERFORGE_PROXY_HOST") or "host.docker.internal").strip()
        if replacement:
            netloc = _rebuild_netloc(parsed, replacement)
            parsed = SplitResult(
                scheme=parsed.scheme or "http",
                netloc=netloc,
                path=parsed.path,
                query=parsed.query,
                fragment=parsed.fragment,
            )
    return urlunsplit(parsed)


def resolve_proxy_url() -> str | None:
    # By default we only trust explicit PaperForge proxy settings.
    # System-level HTTP_PROXY/HTTPS_PROXY can contain host-local loopback
    # addresses (127.0.0.1), which are invalid inside Docker containers.
    use_system_proxy = (os.getenv("PAPERFORGE_USE_SYSTEM_PROXY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    candidates = [
        os.getenv("PAPERFORGE_PROXY_URL"),
        os.getenv("PAPERFORGE_HTTPS_PROXY"),
        os.getenv("PAPERFORGE_HTTP_PROXY"),
    ]
    if use_system_proxy:
        candidates.extend(
            [
                os.getenv("HTTPS_PROXY"),
                os.getenv("HTTP_PROXY"),
                os.getenv("https_proxy"),
                os.getenv("http_proxy"),
            ]
        )
    for item in candidates:
        if item and item.strip():
            return normalize_proxy_url(item.strip())
    return None


def create_httpx_client(
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
    verify: bool = True,
    proxy: str | None = None,
) -> httpx.Client:
    proxy_url = normalize_proxy_url(proxy) if proxy else resolve_proxy_url()
    kwargs: dict[str, object] = {
        "timeout": timeout,
        "headers": headers,
        "follow_redirects": follow_redirects,
        "verify": verify,
        "trust_env": False,
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.Client(**kwargs)
