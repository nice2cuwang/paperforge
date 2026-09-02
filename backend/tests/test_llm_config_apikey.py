"""Tests for the API-key masking pitfall on the LLM config edit routes.

``LLMConfigRead`` masks ``api_key`` as ``sk-1****abcd`` for display. The edit
form used to echo that masked value (or an empty string) back on PATCH, which
clobbered the real stored key and made every subsequent /test call fail with
401. The routes now skip masked/empty values.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.llm_config import LLMConfig


def _create_config(client, api_key: str = "sk-real-key-1234567890") -> dict:
    resp = client.post(
        "/api/llm/configs",
        json={
            "name": "Test Kimi",
            "provider": "kimi",
            "model": "kimi-k2.6",
            "api_key": api_key,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _stored_api_key(test_session_factory, config_id: str) -> str | None:
    db: Session = test_session_factory()
    try:
        cfg = db.get(LLMConfig, config_id)
        return cfg.api_key if cfg else None
    finally:
        db.close()


def test_read_response_masks_api_key(client):
    cfg = _create_config(client, api_key="sk-real-key-1234567890")
    assert cfg["api_key"] == "sk-r****7890"


def test_patch_with_masked_api_key_preserves_real_key(client, test_session_factory):
    cfg = _create_config(client, api_key="sk-real-key-1234567890")
    config_id = cfg["id"]
    masked = cfg["api_key"]  # echoed back by the edit form
    assert "****" in masked

    resp = client.patch(
        f"/api/llm/configs/{config_id}",
        json={"name": "Test Kimi", "provider": "kimi", "model": "kimi-k2.6", "api_key": masked},
    )
    assert resp.status_code == 200, resp.text

    assert _stored_api_key(test_session_factory, config_id) == "sk-real-key-1234567890"


def test_patch_with_empty_api_key_preserves_real_key(client, test_session_factory):
    cfg = _create_config(client, api_key="sk-real-key-1234567890")
    config_id = cfg["id"]

    resp = client.patch(f"/api/llm/configs/{config_id}", json={"api_key": ""})
    assert resp.status_code == 200, resp.text

    assert _stored_api_key(test_session_factory, config_id) == "sk-real-key-1234567890"


def test_patch_with_new_real_api_key_updates(client, test_session_factory):
    cfg = _create_config(client, api_key="sk-old-key-1234567890")
    config_id = cfg["id"]

    resp = client.patch(f"/api/llm/configs/{config_id}", json={"api_key": "sk-brand-new-key-9999"})
    assert resp.status_code == 200, resp.text

    assert _stored_api_key(test_session_factory, config_id) == "sk-brand-new-key-9999"


def test_patch_omitting_api_key_preserves_real_key(client, test_session_factory):
    cfg = _create_config(client, api_key="sk-real-key-1234567890")
    config_id = cfg["id"]

    # No api_key field at all in the payload.
    resp = client.patch(f"/api/llm/configs/{config_id}", json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text

    assert _stored_api_key(test_session_factory, config_id) == "sk-real-key-1234567890"
