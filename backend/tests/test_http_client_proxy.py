from unittest.mock import patch

from app.services.http_client import normalize_proxy_url, resolve_proxy_url, get_proxy_host


def test_normalize_proxy_localhost_rewritten_to_host_gateway(monkeypatch):
    monkeypatch.delenv("PAPERFORGE_PROXY_HOST", raising=False)
    with patch("app.services.http_client.get_proxy_host", return_value="host.docker.internal"):
        rewritten = normalize_proxy_url("http://127.0.0.1:7890")
    assert rewritten == "http://host.docker.internal:7890"


def test_normalize_proxy_keeps_remote_proxy():
    value = normalize_proxy_url("http://proxy.example.com:8080")
    assert value == "http://proxy.example.com:8080"


def test_resolve_proxy_prefers_paperforge_override(monkeypatch):
    monkeypatch.delenv("PAPERFORGE_PROXY_URL", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setenv("PAPERFORGE_PROXY_URL", "http://127.0.0.1:7890")
    with patch("app.services.http_client.get_proxy_host", return_value="host.docker.internal"):
        proxy = resolve_proxy_url()
    assert proxy == "http://host.docker.internal:7890"


def test_resolve_proxy_ignores_system_proxy_by_default(monkeypatch):
    monkeypatch.delenv("PAPERFORGE_PROXY_URL", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTP_PROXY", raising=False)
    monkeypatch.delenv("PAPERFORGE_USE_SYSTEM_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    proxy = resolve_proxy_url()
    assert proxy is None


def test_resolve_proxy_can_enable_system_proxy(monkeypatch):
    monkeypatch.delenv("PAPERFORGE_PROXY_URL", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTPS_PROXY", raising=False)
    monkeypatch.delenv("PAPERFORGE_HTTP_PROXY", raising=False)
    monkeypatch.setenv("PAPERFORGE_USE_SYSTEM_PROXY", "true")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    with patch("app.services.http_client.get_proxy_host", return_value="host.docker.internal"):
        proxy = resolve_proxy_url()
    assert proxy == "http://host.docker.internal:7890"
