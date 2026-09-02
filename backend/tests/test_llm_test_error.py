"""Tests for surfacing provider error details in the connection test.

A bare httpx "404 Not Found for url ..." message hides the actionable reason
(Volcano Ark: ``UnsupportedModel — model does not support the coding plan``,
``ModelNotOpen — activate the model in the Ark console``). ``_do_test`` now
parses the JSON error body.
"""
from __future__ import annotations

import httpx

from app.api.routes.llm_config import _friendly_test_error


def _status_error(status: int, payload: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(status, content=payload.encode(), request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_error_body_code_and_message_surface():
    exc = _status_error(
        404,
        '{"error": {"code": "UnsupportedModel", "message": "The requested model does not support the coding plan feature."}}',
    )
    msg = _friendly_test_error(exc)
    assert "404" in msg
    assert "UnsupportedModel" in msg
    assert "coding plan" in msg


def test_error_body_string_form():
    exc = _status_error(400, '{"error": "bad request body"}')
    assert "400" in _friendly_test_error(exc)
    assert "bad request body" in _friendly_test_error(exc)


def test_non_json_error_falls_back_to_status():
    exc = _status_error(502, "<html>Bad Gateway</html>")
    assert _friendly_test_error(exc).startswith("HTTP 502")


def test_non_http_error_passthrough():
    assert "boom" in _friendly_test_error(RuntimeError("boom"))
