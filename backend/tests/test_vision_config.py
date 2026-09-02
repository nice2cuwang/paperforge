"""Tests for the designated vision-model config.

Figure tagging sends images to a multimodal LLM, but the main writing model
(e.g. DeepSeek) often cannot see images. A config flagged ``is_vision``
handles image-input calls instead: ``chat_completion(..., images=[...])``
routes to it, and the vision role is exclusive across configs.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

import app.services.llm_service as llm_svc
from app.models.llm_config import LLMConfig


def _create_config(client, name: str, provider: str, model: str, api_key: str = "sk-test-key-1234567890") -> dict:
    resp = client.post(
        "/api/llm/configs",
        json={"name": name, "provider": provider, "model": model, "api_key": api_key},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _patch_session(monkeypatch, test_session_factory):
    monkeypatch.setattr(llm_svc, "SessionLocal", test_session_factory)


# ── Config resolution ─────────────────────────────────────────────


def test_vision_config_falls_back_to_active(monkeypatch, test_session_factory):
    _patch_session(monkeypatch, test_session_factory)
    db: Session = test_session_factory()
    db.add(LLMConfig(id="a1", name="main", provider="deepseek", model="deepseek-chat", is_active=True))
    db.commit()
    db.close()

    cfg = llm_svc._get_vision_config()
    assert cfg is not None and cfg.id == "a1"
    # deepseek-chat is explicitly non-vision in the catalog.
    assert llm_svc.active_model_supports_vision() is False


def test_designated_vision_config_wins(monkeypatch, test_session_factory):
    _patch_session(monkeypatch, test_session_factory)
    db: Session = test_session_factory()
    db.add(LLMConfig(id="a1", name="main", provider="deepseek", model="deepseek-chat", is_active=True))
    db.add(LLMConfig(id="v1", name="vision", provider="openai", model="gpt-4o-mini", is_active=False, is_vision=True))
    db.commit()
    db.close()

    cfg = llm_svc._get_vision_config()
    assert cfg is not None and cfg.id == "v1"
    assert llm_svc.active_model_supports_vision() is True


# ── chat_completion routing ───────────────────────────────────────


def test_chat_completion_uses_vision_config_for_image_calls(monkeypatch, test_session_factory):
    _patch_session(monkeypatch, test_session_factory)
    db: Session = test_session_factory()
    db.add(LLMConfig(id="a1", name="main", provider="deepseek", model="deepseek-chat", is_active=True))
    db.add(LLMConfig(id="v1", name="vision", provider="openai", model="gpt-4o-mini", is_active=False, is_vision=True))
    db.commit()
    db.close()

    seen: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "[]"}}], "usage": {}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            seen["url"] = url
            seen["body"] = json
            return FakeResp()

    monkeypatch.setattr(llm_svc, "create_httpx_client", lambda **kwargs: FakeClient(**kwargs))
    monkeypatch.setattr(llm_svc, "_persist_audit", lambda *a, **k: None)

    result = llm_svc.chat_completion(
        system_prompt="s",
        user_prompt="u",
        images=[{"data": "aGVsbG8=", "media_type": "image/jpeg"}],
    )
    assert not result.get("error")
    # Routed to the vision config (OpenAI endpoint + image_url parts), not DeepSeek.
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    parts = seen["body"]["messages"][-1]["content"]
    assert parts[0] == {"type": "text", "text": "u"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_chat_completion_anthropic_image_block_format(monkeypatch, test_session_factory):
    _patch_session(monkeypatch, test_session_factory)
    db: Session = test_session_factory()
    db.add(LLMConfig(id="v1", name="vision", provider="anthropic", model="claude-sonnet-4-20250514", is_active=True, is_vision=True))
    db.commit()
    db.close()

    seen: dict = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            seen["body"] = json
            return FakeResp()

    monkeypatch.setattr(llm_svc, "create_httpx_client", lambda **kwargs: FakeClient(**kwargs))
    monkeypatch.setattr(llm_svc, "_persist_audit", lambda *a, **k: None)

    llm_svc.chat_completion(
        system_prompt="s",
        user_prompt="u",
        images=[{"data": "aGVsbG8=", "media_type": "image/png"}],
    )
    parts = seen["body"]["messages"][0]["content"]
    assert parts[0]["type"] == "image"
    assert parts[0]["source"] == {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}
    assert parts[-1] == {"type": "text", "text": "u"}


# ── Designate endpoint ────────────────────────────────────────────


def test_vision_endpoint_is_exclusive_and_toggles(client, test_session_factory):
    a = _create_config(client, "Main", "deepseek", "deepseek-chat")
    b = _create_config(client, "Vision", "openai", "gpt-4o-mini")

    resp = client.post(f"/api/llm/configs/{b['id']}/vision")
    assert resp.status_code == 200 and resp.json()["is_vision"] is True

    data = client.get("/api/llm/configs").json()
    assert data["vision_id"] == b["id"]

    # Designating another config moves the role.
    resp = client.post(f"/api/llm/configs/{a['id']}/vision")
    assert resp.json()["is_vision"] is True
    data = client.get("/api/llm/configs").json()
    assert data["vision_id"] == a["id"]
    by_id = {c["id"]: c for c in data["configs"]}
    assert by_id[b["id"]]["is_vision"] is False

    # Toggling the current vision config removes the role.
    resp = client.post(f"/api/llm/configs/{a['id']}/vision")
    assert resp.json()["is_vision"] is False
    assert client.get("/api/llm/configs").json()["vision_id"] is None


def test_patch_is_vision_unsets_others(client):
    a = _create_config(client, "Main", "deepseek", "deepseek-chat")
    b = _create_config(client, "Vision", "openai", "gpt-4o-mini")

    client.post(f"/api/llm/configs/{b['id']}/vision")
    resp = client.patch(f"/api/llm/configs/{a['id']}", json={"is_vision": True})
    assert resp.status_code == 200, resp.text

    data = client.get("/api/llm/configs").json()
    assert data["vision_id"] == a["id"]
