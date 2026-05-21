"""LLM unified calling layer with Provider Strategy Pattern.

Each provider has its own Strategy class that handles:
- Request preparation (temperature limits, max_tokens allocation, timeout)
- Response parsing (content extraction, reasoning_content handling, error mapping)
- Retry policy (rate limits, backoff strategy)
- Capability declaration (supports reasoning, default params)
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.database import SessionLocal
from app.models.llm_config import LLMConfig
from app.services.http_client import create_httpx_client


# ---------------------------------------------------------------------------
# Provider Strategy Interface
# ---------------------------------------------------------------------------

class ProviderStrategy(ABC):
    """Abstract base for per-provider behavior adaptation."""

    @abstractmethod
    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        """Mutate the request body before sending."""

    @abstractmethod
    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        """Parse raw provider response into a uniform dict.

        Returns::
            {
                "content": str,
                "usage": dict | None,
                "error": str | None,
                "_reasoning": str | None,  # optional internal reasoning text
            }
        """

    @abstractmethod
    def get_retry_policy(self) -> dict[str, Any]:
        """Return retry config: {max_retries, base_delay, max_delay}."""

    @property
    @abstractmethod
    def supports_reasoning(self) -> bool:
        """Whether this provider/model family uses a reasoning/thinking stage."""

    @property
    @abstractmethod
    def default_max_tokens(self) -> int:
        """Sensible default max_tokens for writing tasks."""

    @property
    @abstractmethod
    def default_timeout(self) -> float:
        """Sensible default timeout in seconds."""

    # ---------- helpers ----------

    def adjust_max_tokens(
        self,
        requested: int | None,
        strategy_mode: str,
        enable_reasoning: bool,
    ) -> int:
        """Calculate final max_tokens based on strategy and reasoning settings."""
        base = requested or self.default_max_tokens
        if not enable_reasoning or not self.supports_reasoning:
            return base
        multiplier = {
            "fast": 1.2,
            "balanced": 1.5,
            "quality": 2.0,
            "reasoning": 2.5,
        }
        return int(base * multiplier.get(strategy_mode, 1.5))


# ---------------------------------------------------------------------------
# Concrete Strategies
# ---------------------------------------------------------------------------

class KimiK2Strategy(ProviderStrategy):
    """Moonshot Kimi K2.x family (K2.5 / K2.6).

    Characteristics:
    - reasoning/thinking mode consumes tokens before emitting content
    - temperature is locked to 1.0
    - longer timeout needed for reasoning
    """

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        body["temperature"] = 1.0
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")

        if not content and reasoning:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": (
                    "Model produced reasoning but no output content. "
                    "Reasoning consumed all available tokens. "
                    "Try increasing max_tokens or disabling reasoning."
                ),
                "_reasoning": reasoning,
            }

        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": reasoning if reasoning else None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 5.0, "max_delay": 60.0}

    @property
    def supports_reasoning(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 3000

    @property
    def default_timeout(self) -> float:
        return 120.0


class DeepSeekV3Strategy(ProviderStrategy):
    """DeepSeek V3 (standard chat model, no reasoning overhead)."""

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        content = choices[0].get("message", {}).get("content", "")
        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 3.0, "max_delay": 30.0}

    @property
    def supports_reasoning(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 2000

    @property
    def default_timeout(self) -> float:
        return 60.0


class DeepSeekR1Strategy(ProviderStrategy):
    """DeepSeek R1 (reasoning model).

    Similar to Kimi K2 but uses DeepSeek's API format.
    Reasoning content may be in a different field or streamed separately.
    For now we treat it like Kimi K2 with conservative token预留.
    """

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")
        if not content and reasoning:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": (
                    "Reasoning model produced thinking but no output. "
                    "Increase max_tokens or switch to a non-reasoning model."
                ),
                "_reasoning": reasoning,
            }
        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": reasoning if reasoning else None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 5.0, "max_delay": 60.0}

    @property
    def supports_reasoning(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 3000

    @property
    def default_timeout(self) -> float:
        return 120.0


class OpenAIStrategy(ProviderStrategy):
    """OpenAI GPT family (GPT-4o, GPT-4o-mini, etc.)."""

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        content = choices[0].get("message", {}).get("content", "")
        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 3.0, "max_delay": 30.0}

    @property
    def supports_reasoning(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 2000

    @property
    def default_timeout(self) -> float:
        return 60.0


class AnthropicStrategy(ProviderStrategy):
    """Anthropic Claude family.

    NOTE: Anthropic uses a different API shape (messages API).
    This is a placeholder for future full Anthropic protocol support.
    For now we keep it conservative.
    """

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        content = choices[0].get("message", {}).get("content", "")
        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 3.0, "max_delay": 30.0}

    @property
    def supports_reasoning(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 2000

    @property
    def default_timeout(self) -> float:
        return 60.0


class DefaultStrategy(ProviderStrategy):
    """Conservative fallback for unknown providers."""

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices", [])
        if not choices:
            return {
                "content": "",
                "usage": data.get("usage"),
                "error": "Empty choices in LLM response",
                "_reasoning": None,
            }
        content = choices[0].get("message", {}).get("content", "")
        return {
            "content": content.strip(),
            "usage": data.get("usage"),
            "error": None,
            "_reasoning": None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 5.0, "max_delay": 60.0}

    @property
    def supports_reasoning(self) -> bool:
        return False

    @property
    def default_max_tokens(self) -> int:
        return 1500

    @property
    def default_timeout(self) -> float:
        return 60.0


# ---------------------------------------------------------------------------
# Strategy Registry
# ---------------------------------------------------------------------------

_STRATEGIES: dict[str, type[ProviderStrategy]] = {
    "openai": OpenAIStrategy,
    "azure_openai": OpenAIStrategy,
    "anthropic": AnthropicStrategy,
    "deepseek": DeepSeekV3Strategy,
    "kimi": KimiK2Strategy,
    "kimi-coding": KimiK2Strategy,
    "zhipu": OpenAIStrategy,
    "qwen": OpenAIStrategy,
    "astron-coding": OpenAIStrategy,
    "local": OpenAIStrategy,
}


def get_strategy(provider: str) -> ProviderStrategy:
    strategy_cls = _STRATEGIES.get(provider, DefaultStrategy)
    return strategy_cls()


# ---------------------------------------------------------------------------
# Endpoint / Config Resolution
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "kimi": "https://api.moonshot.cn/v1",
    "kimi-coding": "https://api.kimi.com/coding/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "astron-coding": "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
    "local": "http://localhost:8000/v1",
}

_NON_OPENAI_PROVIDERS: set[str] = {"anthropic"}

# ---------------------------------------------------------------------------
# Provider Capability Matrix
# ---------------------------------------------------------------------------

class ProviderCapability:
    def __init__(
        self,
        json_mode: bool = True,
        reasoning: bool = False,
        tool_call: bool = False,
        max_context: int = 128000,
    ) -> None:
        self.json_mode = json_mode
        self.reasoning = reasoning
        self.tool_call = tool_call
        self.max_context = max_context


_CAPABILITIES: dict[str, ProviderCapability] = {
    "openai": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=128000),
    "azure_openai": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=128000),
    "anthropic": ProviderCapability(json_mode=True, reasoning=True, tool_call=True, max_context=200000),
    "deepseek": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=64000),
    "kimi": ProviderCapability(json_mode=True, reasoning=True, tool_call=True, max_context=256000),
    "kimi-coding": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=262144),
    "zhipu": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=128000),
    "qwen": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=128000),
    "astron-coding": ProviderCapability(json_mode=True, reasoning=False, tool_call=True, max_context=98304),
    "local": ProviderCapability(json_mode=True, reasoning=False, tool_call=False, max_context=32768),
}


def get_capability(provider: str) -> ProviderCapability:
    return _CAPABILITIES.get(provider, ProviderCapability())


# ---------------------------------------------------------------------------
# Retryable error classification
# ---------------------------------------------------------------------------

def _is_retryable_error(err_text: str, status_code: int | None = None) -> bool:
    """Determine if an error is transient and worth retrying."""
    if status_code is not None:
        # Retryable HTTP status codes
        if status_code in {429, 502, 503, 504}:
            return True
        # Non-retryable client errors
        if status_code in {400, 401, 403, 404, 422}:
            return False
    text = err_text.lower()
    retryable_keywords = [
        "timeout",
        "timed out",
        "connection",
        "connecttimeout",
        "readtimeout",
        "too many requests",
        "rate limit",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
    ]
    if any(kw in text for kw in retryable_keywords):
        return True
    non_retryable_keywords = [
        "invalid api key",
        "incorrect api key",
        "authentication",
        "unauthorized",
        "not found",
        "bad request",
        "context length exceeded",
        "maximum context length",
    ]
    if any(kw in text for kw in non_retryable_keywords):
        return False
    # Default: retry network-ish errors, don't retry others
    return "connection" in text or "timeout" in text


def _get_active_config() -> LLMConfig | None:
    db = SessionLocal()
    try:
        stmt = select(LLMConfig).where(LLMConfig.is_active == True)
        return db.scalar(stmt)
    finally:
        db.close()


def _resolve_endpoint(config: LLMConfig) -> str:
    base = config.api_base
    if not base:
        base = _DEFAULT_BASE_URLS.get(config.provider, "")
    if not base:
        raise ValueError(f"No base URL configured for provider '{config.provider}'")
    base = base.rstrip("/")
    return f"{base}/chat/completions"


def _build_headers(config: LLMConfig) -> dict[str, str]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key or ''}",
    }
    for k, v in (config.extra_headers or {}).items():
        if isinstance(v, str):
            headers[str(k)] = v
    return headers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    response_format: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Call the active LLM using the provider's Strategy.

    Returns::
        {
            "content": str,
            "usage": dict | None,
            "latency_ms": int,
            "error": str | None,
            "_reasoning": str | None,
        }
    """
    config = _get_active_config()
    if config is None:
        return {"content": "", "usage": None, "latency_ms": 0, "error": "No active LLM config found", "_reasoning": None}

    if config.provider in _NON_OPENAI_PROVIDERS:
        return {
            "content": "",
            "usage": None,
            "latency_ms": 0,
            "error": f"Provider '{config.provider}' is not yet supported by llm_service (OpenAI-compatible only)",
            "_reasoning": None,
        }

    strategy = get_strategy(config.provider)

    url = _resolve_endpoint(config)
    headers = _build_headers(config)

    # Determine final max_tokens via Strategy
    final_max_tokens = strategy.adjust_max_tokens(
        max_tokens,
        config.strategy_mode or "balanced",
        config.enable_reasoning,
    )
    if config.preferred_max_tokens:
        final_max_tokens = config.preferred_max_tokens

    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": final_max_tokens,
    }

    temp = temperature if temperature is not None else config.temperature
    if temp is not None:
        body["temperature"] = temp

    if response_format:
        body["response_format"] = response_format

    for k, v in (config.extra_body or {}).items():
        body[str(k)] = v

    # Let Strategy mutate the request (temperature locks, etc.)
    body = strategy.prepare_request(body)

    final_timeout = timeout if timeout is not None else strategy.default_timeout
    retry_policy = strategy.get_retry_policy()
    max_retries = retry_policy.get("max_retries", 3)
    base_delay = retry_policy.get("base_delay", 5.0)
    max_delay = retry_policy.get("max_delay", 60.0)

    last_error = ""
    for attempt in range(max_retries):
        start = time.time()
        try:
            with create_httpx_client(
                timeout=final_timeout,
                headers=headers,
                proxy=config.proxy_url,
            ) as client:
                resp = client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            latency = int((time.time() - start) * 1000)
            err_text = str(exc)
            last_error = err_text[:500]
            status_code: int | None = None
            if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
                status_code = exc.response.status_code
            elif "429" in err_text:
                status_code = 429
            if _is_retryable_error(err_text, status_code):
                if attempt < max_retries - 1:
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    logger.warning(
                        "LLM call attempt %d/%d failed (retryable: %s), retrying in %.1fs",
                        attempt + 1,
                        max_retries,
                        last_error,
                        delay,
                    )
                    time.sleep(delay)
                    continue
            logger.error("LLM call failed after %d attempts (non-retryable): %s", attempt + 1, last_error)
            return {"content": "", "usage": None, "latency_ms": latency, "error": last_error, "_reasoning": None}

        latency = int((time.time() - start) * 1000)
        return {**strategy.parse_response(data), "latency_ms": latency}

    return {"content": "", "usage": None, "latency_ms": 0, "error": last_error or "Max retries exceeded", "_reasoning": None}


def chat_completion_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    """Convenience wrapper returning only text (empty string on error)."""
    result = chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return result.get("content") or ""


def chat_completion_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Convenience wrapper forcing JSON mode and parsing the result.

    Returns parsed dict on success, or {"_error": str} on failure.
    """
    result = chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        response_format={"type": "json_object"},
    )
    text = result.get("content") or ""
    if not text:
        return {"_error": result.get("error") or "Empty response"}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {"_error": f"JSON parse failed: {exc}", "_raw": text[:800]}
