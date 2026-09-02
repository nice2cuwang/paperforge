"""LLM unified calling layer with Provider Strategy Pattern.

Each provider has its own Strategy class that handles:
- Request preparation (temperature limits, max_tokens allocation, timeout)
- Response parsing (content extraction, reasoning_content handling, error mapping)
- Retry policy (rate limits, backoff strategy)
- Capability declaration (supports reasoning, default params)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from contextlib import contextmanager
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.llm_config import LLMConfig
from app.services.http_client import create_httpx_client
from app.services.llm_providers import (
    ANTHROPIC_PROTOCOL_IDS,
    CAPABILITY_MAP,
    CapabilityDef,
    DEFAULT_BASE_URLS,
    STRATEGY_ANTHROPIC,
    STRATEGY_DEEPSEEK_R1,
    STRATEGY_DEEPSEEK_V3,
    STRATEGY_DEFAULT,
    STRATEGY_KIMI_K2,
    STRATEGY_OPENAI,
    STRATEGY_KEYS,
    chat_endpoint,
    chat_headers,
    get_provider,
    provider_or_default,
)


# ---------------------------------------------------------------------------
# Current task context (token accounting attribution)
# ---------------------------------------------------------------------------

# 进程级"当前任务"上下文：工作流 runner / 各分步路由在开始干活前设置，
# 结束后清除。所有 chat_completion_* 调用据此把审计日志归属到 task/project。
# 用模块级全局而非 ContextVar，因为 debate_service 的 ThreadPoolExecutor
# 线程不会继承调用方的 contextvars，但能看到模块全局。
# 注：同一进程并发跑两个工作流时归属可能交叉（单用户本地工具可接受）。
_task_context: dict[str, str] | None = None


def set_task_context(task_id: str, project_id: str | None = None) -> None:
    """Attribute subsequent LLM calls (audit logs) to the given task/project."""
    global _task_context
    _task_context = {"task_id": task_id, "project_id": project_id} if task_id else None


def clear_task_context() -> None:
    global _task_context
    _task_context = None


def get_task_context() -> dict[str, str] | None:
    return _task_context


@contextmanager
def task_context(task_id: str, project_id: str | None = None):
    """``with task_context(tid, pid):`` - set/clear around a block of work."""
    set_task_context(task_id, project_id)
    try:
        yield
    finally:
        clear_task_context()


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
        return 8192

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
        return 8192

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
    """Anthropic Claude family (native Messages API protocol).

    Anthropic uses a different response shape than OpenAI:
    - Response: ``content[].text`` instead of ``choices[0].message.content``
    - Request: ``system`` is a top-level field, not a message role
    - Auth: ``x-api-key`` header instead of ``Authorization: Bearer``
    """

    def prepare_request(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("temperature") is None:
            body["temperature"] = 0.7
        return body

    def parse_response(self, data: dict[str, Any]) -> dict[str, Any]:
        # Anthropic Messages API format: {"content": [{"type": "text", "text": "..."}]}
        content_blocks = data.get("content", [])
        if content_blocks and isinstance(content_blocks, list):
            text_parts: list[str] = []
            thinking_parts: list[str] = []
            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
            content = "\n".join(text_parts).strip()
            reasoning = "\n".join(thinking_parts).strip() or None
            if not content:
                return {
                    "content": "",
                    "usage": data.get("usage"),
                    "error": "Anthropic response contained no text content",
                    "_reasoning": reasoning,
                }
            # Normalize Anthropic usage format to match OpenAI
            usage = data.get("usage")
            if usage and isinstance(usage, dict):
                usage = {
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                }
            return {
                "content": content,
                "usage": usage,
                "error": None,
                "_reasoning": reasoning,
            }

        # Fallback: try OpenAI-compatible format (for proxy services)
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            return {
                "content": content.strip(),
                "usage": data.get("usage"),
                "error": None,
                "_reasoning": None,
            }

        return {
            "content": "",
            "usage": data.get("usage"),
            "error": "Unrecognized Anthropic response format",
            "_reasoning": None,
        }

    def get_retry_policy(self) -> dict[str, Any]:
        return {"max_retries": 3, "base_delay": 3.0, "max_delay": 30.0}

    @property
    def supports_reasoning(self) -> bool:
        return True

    @property
    def default_max_tokens(self) -> int:
        return 8192

    @property
    def default_timeout(self) -> float:
        return 120.0


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

# strategy_key -> strategy class. Provider ids map to a key via the registry
# (llm_providers.STRATEGY_KEYS); model-level overrides below take precedence.
_STRATEGY_CLASSES: dict[str, type[ProviderStrategy]] = {
    STRATEGY_OPENAI: OpenAIStrategy,
    STRATEGY_ANTHROPIC: AnthropicStrategy,
    STRATEGY_KIMI_K2: KimiK2Strategy,
    STRATEGY_DEEPSEEK_V3: DeepSeekV3Strategy,
    STRATEGY_DEEPSEEK_R1: DeepSeekR1Strategy,
    STRATEGY_DEFAULT: DefaultStrategy,
}

# Model-level overrides (e.g. reasoning models within a non-reasoning provider)
_MODEL_STRATEGY_OVERRIDES: dict[str, type[ProviderStrategy]] = {
    "deepseek-reasoner": DeepSeekR1Strategy,
    "step-2-16k": KimiK2Strategy,
    "step-1-256k": KimiK2Strategy,
    "claude-3-7-sonnet-20250219": AnthropicStrategy,
    "claude-sonnet-4-20250514": AnthropicStrategy,
}


def get_strategy(provider: str, model: str = "") -> ProviderStrategy:
    """Resolve the strategy for a provider, with optional model-level overrides."""
    if model and model in _MODEL_STRATEGY_OVERRIDES:
        return _MODEL_STRATEGY_OVERRIDES[model]()
    key = STRATEGY_KEYS.get(provider, STRATEGY_DEFAULT)
    return _STRATEGY_CLASSES.get(key, DefaultStrategy)()


# ---------------------------------------------------------------------------
# Endpoint / Config Resolution
# ---------------------------------------------------------------------------

# Runtime default base URLs live in the single registry (llm_providers).
# Re-exported under the legacy name for any existing import sites.
_DEFAULT_BASE_URLS: dict[str, str] = DEFAULT_BASE_URLS

# ---------------------------------------------------------------------------
# Provider Capability Matrix
# ---------------------------------------------------------------------------

class ProviderCapability:
    """Compatibility shim over the registry's ``CapabilityDef``."""

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

    @classmethod
    def from_def(cls, cap: CapabilityDef) -> "ProviderCapability":
        return cls(cap.json_mode, cap.reasoning, cap.tool_call, cap.max_context)


# Capability matrix is derived from the single registry.
_CAPABILITIES: dict[str, ProviderCapability] = {
    pid: ProviderCapability.from_def(cap) for pid, cap in CAPABILITY_MAP.items()
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


def _get_vision_config() -> LLMConfig | None:
    """Config for image-input calls: the designated vision model, else active.

    The vision role exists so figure tagging can use a cheap multimodal model
    without forcing the main writing model to be multimodal.
    """
    db = SessionLocal()
    try:
        stmt = select(LLMConfig).where(LLMConfig.is_vision == True)
        config = db.scalar(stmt)
        if config is not None:
            return config
        stmt = select(LLMConfig).where(LLMConfig.is_active == True)
        return db.scalar(stmt)
    finally:
        db.close()


def _resolve_endpoint(config: LLMConfig) -> str:
    p = get_provider(config.provider) or provider_or_default(config.provider)
    if not (config.api_base or p.base_url):
        raise ValueError(f"No base URL configured for provider '{config.provider}'")
    return chat_endpoint(p, config.api_base, config.model)


def active_model_supports_vision() -> bool:
    """Whether the config that handles image calls can accept image inputs.

    Checks the designated vision config when one is set, otherwise the active
    config. Returns ``False`` only when the provider catalog explicitly marks
    the model as non-vision (e.g. DeepSeek). Unknown providers/models return
    ``True`` so callers can attempt the call and fall back on API errors.
    Any failure (no config, DB error) returns ``False`` -- vision tagging is
    an enhancement and must never block the pipeline.
    """
    try:
        config = _get_vision_config()
        if config is None:
            return False
        p = get_provider(config.provider)
        if p is None:
            return True
        for m in p.models:
            if m.id == config.model:
                return m.supports_vision
        return True
    except Exception:
        logger.warning("Vision capability check failed; assuming no vision", exc_info=True)
        return False


def _build_headers(config: LLMConfig) -> dict[str, str]:
    p = get_provider(config.provider) or provider_or_default(config.provider)
    return chat_headers(p, config.api_key, config.extra_headers)


# ---------------------------------------------------------------------------
# Image generation (text-to-image)
# ---------------------------------------------------------------------------

def _get_image_gen_config() -> LLMConfig | None:
    """The designated image-generation config, else None."""
    db = SessionLocal()
    try:
        stmt = select(LLMConfig).where(LLMConfig.is_image_gen == True)
        return db.scalar(stmt)
    finally:
        db.close()


def image_gen_configured() -> bool:
    try:
        return _get_image_gen_config() is not None
    except Exception:
        return False


def generate_image(
    prompt: str,
    *,
    size: str = "1024x576",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Generate an image via the OpenAI-compatible ``/images/generations`` API.

    SiliconFlow / OpenAI / 智谱 / 火山方舟等主流推理平台均兼容该协议，
    所以直接复用 designated 配置的 api_base + api_key + model 即可。

    Returns::
        {
            "image_bytes": bytes | None,   # b64_json 解码后的图像字节
            "image_url": str | None,       # 部分平台直接返回 URL
            "revised_prompt": str | None,
            "error": str | None,
        }
    """
    config = _get_image_gen_config()
    if config is None:
        return {"image_bytes": None, "image_url": None, "revised_prompt": None,
                "error": "No image-generation config designated"}

    base = (config.api_base or "").strip().rstrip("/")
    if not base:
        p = get_provider(config.provider) or provider_or_default(config.provider)
        base = (p.base_url or "").rstrip("/")
    if not base:
        return {"image_bytes": None, "image_url": None, "revised_prompt": None,
                "error": f"No base URL for image generation via '{config.provider}'"}

    url = f"{base}/images/generations"
    headers = _build_headers(config)

    def _post(body: dict[str, Any]):
        eff_timeout = timeout or max(30.0, float(config.timeout or 60))
        with create_httpx_client(timeout=eff_timeout, headers=headers) as client:
            resp = client.post(url, json=body)
            resp.raise_for_status()
            return resp.json()

    body: dict[str, Any] = {
        "model": config.model,
        "prompt": prompt[:1000],
        "size": size,
        "n": 1,
        # 优先 b64：URL 有时效且需二次下载；不支持的平台会报错，随后去掉重试
        "response_format": "b64_json",
    }
    body.update(config.extra_body or {})

    data: Any = None
    try:
        data = _post(body)
    except Exception as exc:
        if "response_format" in body:
            body.pop("response_format")
            try:
                data = _post(body)
            except Exception as exc2:
                logger.warning("Image generation failed: %s", exc2)
                return {"image_bytes": None, "image_url": None, "revised_prompt": None,
                        "error": f"{type(exc2).__name__}: {exc2}"}
        else:
            logger.warning("Image generation failed: %s", exc)
            return {"image_bytes": None, "image_url": None, "revised_prompt": None,
                    "error": f"{type(exc).__name__}: {exc}"}

    items = data.get("data") or [] if isinstance(data, dict) else []
    if not items or not isinstance(items[0], dict):
        return {"image_bytes": None, "image_url": None, "revised_prompt": None,
                "error": f"Empty image generation response: {str(data)[:200]}"}

    item = items[0]
    b64 = item.get("b64_json")
    image_bytes = None
    if b64:
        import base64
        try:
            image_bytes = base64.b64decode(b64)
        except Exception:
            image_bytes = None
    return {
        "image_bytes": image_bytes,
        "image_url": item.get("url"),
        "revised_prompt": item.get("revised_prompt"),
        "error": None if (image_bytes or item.get("url")) else "No image data in response",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# 透明化落库的原文截断长度：完整展示工作过程足够，同时防止异常超长 prompt
# 把 audit_logs 撑爆（1024 tokens 级别的 system prompt 远小于此上限）。
_PROMPT_SNIPPET_LIMIT = 12_000


def _truncate_text(text: str | None, limit: int = _PROMPT_SNIPPET_LIMIT) -> str | None:
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + f"\n…(已截断，共 {len(text)} 字符)"


# 用途标签推断：system_prompt 首句特征 → 前端可读的调用类别。
_PURPOSE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("资深内容编辑", "写作"),
    ("学术编辑", "摘要"),
    ("信息检索顾问", "话题评估"),
    ("配图", "配图"),
    ("审稿", "审稿"),
    ("辩论", "辩论"),
    ("大纲", "大纲"),
    ("主编", "修订"),
    ("证据", "证据"),
    ("论点", "论点提炼"),
    ("图表", "图表"),
    ("检索", "检索"),
)


def _infer_purpose(system_prompt: str) -> str | None:
    head = (system_prompt or "")[:400]
    for pattern, label in _PURPOSE_PATTERNS:
        if pattern in head:
            return label
    return None


def _persist_audit(
    call_id: str,
    task_id: str | None,
    provider: str,
    model: str,
    strategy_mode: str | None,
    system_prompt_hash: str,
    user_prompt_hash: str,
    temperature: float | None,
    max_tokens: int | None,
    response_format: dict[str, str] | None,
    latency_ms: int,
    usage: dict | None,
    error: str | None,
    reasoning: str | None,
    project_id: str | None = None,
    *,
    system_prompt_text: str | None = None,
    user_prompt_text: str | None = None,
    response_text: str | None = None,
    purpose: str | None = None,
) -> None:
    try:
        db = SessionLocal()
        db.add(
            AuditLog(
                id=str(uuid.uuid4()),
                call_id=call_id,
                task_id=task_id,
                project_id=project_id,
                provider=provider,
                model=model,
                strategy_mode=strategy_mode,
                system_prompt_hash=system_prompt_hash,
                user_prompt_hash=user_prompt_hash,
                system_prompt_text=_truncate_text(system_prompt_text),
                user_prompt_text=_truncate_text(user_prompt_text),
                response_text=_truncate_text(response_text),
                purpose=purpose or _infer_purpose(system_prompt_text or ""),
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=json.dumps(response_format) if response_format else None,
                latency_ms=latency_ms,
                usage=usage,
                error=error,
                reasoning=reasoning,
            )
        )
        db.commit()
    except Exception:
        logger.exception("Failed to persist audit log")
    finally:
        try:
            db.close()
        except Exception:
            pass


def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    response_format: dict[str, str] | None = None,
    task_id: str | None = None,
    images: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Call the active LLM using the provider's Strategy.

    ``images`` (optional) is a list of ``{"data": <base64>, "media_type":
    "image/jpeg"}`` dicts. When provided, the user message is sent as a
    multi-part content array -- OpenAI-protocol providers get ``image_url``
    data-URL parts, Anthropic-protocol providers get base64 ``image`` blocks.

    Returns::
        {
            "content": str,
            "usage": dict | None,
            "latency_ms": int,
            "error": str | None,
            "_reasoning": str | None,
        }
    """
    call_id = str(uuid.uuid4())
    system_prompt_hash = hashlib.sha256((system_prompt or "").encode("utf-8")).hexdigest()
    user_prompt_hash = hashlib.sha256((user_prompt or "").encode("utf-8")).hexdigest()

    # 归属解析：显式 task_id 优先，否则落到当前任务上下文（工作流 runner
    # 在入口处 set_task_context 设置），使审计日志能按 task/project 聚合
    # token 消耗。
    ctx = _task_context or {}
    eff_task_id = task_id if task_id is not None else ctx.get("task_id")
    eff_project_id = ctx.get("project_id")

    config = _get_vision_config() if images else _get_active_config()
    if config is None:
        result = {"content": "", "usage": None, "latency_ms": 0, "error": "No active LLM config found", "_reasoning": None}
        _persist_audit(
            call_id, eff_task_id, "", "", None, system_prompt_hash, user_prompt_hash,
            temperature, max_tokens, response_format, 0, None, result["error"], None, eff_project_id,
            system_prompt_text=system_prompt, user_prompt_text=user_prompt, response_text="",
        )
        return result

    strategy = get_strategy(config.provider, config.model)

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

    # Build request body — Anthropic and Anthropic-compatible providers use a different format
    if config.provider in ANTHROPIC_PROTOCOL_IDS:
        if images:
            user_content: Any = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.get("media_type", "image/jpeg"),
                        "data": img.get("data", ""),
                    },
                }
                for img in images
            ] + [{"type": "text", "text": user_prompt}]
        else:
            user_content = user_prompt
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": final_max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
    else:
        if images:
            user_content = [
                {"type": "text", "text": user_prompt},
                *(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.get('media_type', 'image/jpeg')};base64,{img.get('data', '')}"
                        },
                    }
                    for img in images
                ),
            ]
        else:
            user_content = user_prompt
        body = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
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
            try:
                from app.middleware.metrics import metrics_inc_tagged
                metrics_inc_tagged("paperforge_llm_api_calls", f"{config.provider}.err")
            except Exception:
                pass
            result = {"content": "", "usage": None, "latency_ms": latency, "error": last_error, "_reasoning": None}
            _persist_audit(
                call_id, eff_task_id, config.provider, config.model, config.strategy_mode,
                system_prompt_hash, user_prompt_hash, temperature, max_tokens, response_format,
                latency, None, result["error"], None, eff_project_id,
                system_prompt_text=system_prompt, user_prompt_text=user_prompt, response_text="",
            )
            return result

        latency = int((time.time() - start) * 1000)
        try:
            from app.middleware.metrics import metrics_inc_tagged
            metrics_inc_tagged("paperforge_llm_api_calls", f"{config.provider}.ok")
        except Exception:
            pass
        parsed = strategy.parse_response(data)
        result = {**parsed, "latency_ms": latency}
        _persist_audit(
            call_id, eff_task_id, config.provider, config.model, config.strategy_mode,
            system_prompt_hash, user_prompt_hash, temperature, max_tokens, response_format,
            latency, parsed.get("usage"), parsed.get("error"), parsed.get("_reasoning"), eff_project_id,
            system_prompt_text=system_prompt, user_prompt_text=user_prompt,
            response_text=parsed.get("content"),
        )
        return result

    try:
        from app.middleware.metrics import metrics_inc_tagged
        metrics_inc_tagged("paperforge_llm_api_calls", f"{config.provider}.exhausted")
    except Exception:
        pass
    result = {"content": "", "usage": None, "latency_ms": 0, "error": last_error or "Max retries exceeded", "_reasoning": None}
    _persist_audit(
        call_id, eff_task_id, config.provider, config.model, config.strategy_mode,
        system_prompt_hash, user_prompt_hash, temperature, max_tokens, response_format,
        0, None, result["error"], None, eff_project_id,
        system_prompt_text=system_prompt, user_prompt_text=user_prompt, response_text="",
    )
    return result


def chat_completion_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    task_id: str | None = None,
) -> str:
    """Convenience wrapper returning only text (empty string on error)."""
    result = chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        task_id=task_id,
    )
    return result.get("content") or ""


def chat_completion_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
    task_id: str | None = None,
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
        task_id=task_id,
    )
    text = result.get("content") or ""
    if not text:
        return {"_error": result.get("error") or "Empty response"}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {"_error": f"JSON parse failed: {exc}", "_raw": text[:800]}
