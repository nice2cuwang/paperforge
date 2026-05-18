from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.llm_config import LLMConfig
from app.schemas.llm_config import (
    LLMConfigCreate,
    LLMConfigListResponse,
    LLMConfigRead,
    LLMConfigUpdate,
    LLMPreset,
    LLMPresetModel,
    LLMPresetsResponse,
    LLMProvidersResponse,
    LLMProvider,
    LLMProviderModel,
    LLMTestRequest,
    LLMTestResponse,
)
from app.services.http_client import create_httpx_client

router = APIRouter(prefix="/api/llm", tags=["llm"])

# ---------- Built-in provider catalog (for UI reference) ----------

_PROVIDER_CATALOG: list[LLMProvider] = [
    LLMProvider(
        id="openai",
        name="OpenAI",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.896zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607 1.5v2.999l-2.597 1.5-2.607-1.5z'/></svg>",
        description="OpenAI GPT 系列模型，支持函数调用和视觉理解。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="gpt-4o", name="GPT-4o", context_length=128000, supports_chinese=True, supports_vision=True, supports_tools=True, description="OpenAI 旗舰多模态模型"),
            LLMProviderModel(id="gpt-4o-mini", name="GPT-4o Mini", context_length=128000, supports_chinese=True, supports_vision=True, supports_tools=True, description="高性价比"),
            LLMProviderModel(id="gpt-4-turbo", name="GPT-4 Turbo", context_length=128000, supports_chinese=True, supports_vision=True, supports_tools=True, description="高能力模型"),
        ],
    ),
    LLMProvider(
        id="anthropic",
        name="Anthropic",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M17.304 3.541h-3.672l6.696 16.918h3.672zm-10.608 0L0 20.459h3.744l1.368-3.6h6.672l1.368 3.6h3.744L9.696 3.541zm-.264 10.656L7.2 8.893l2.832 5.304z'/></svg>",
        description="Anthropic Claude 系列，以长上下文和安全性著称。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="claude-3-5-sonnet-20241022", name="Claude 3.5 Sonnet", context_length=200000, supports_chinese=True, supports_vision=True, supports_tools=True, description="综合性能最佳"),
            LLMProviderModel(id="claude-3-opus-20240229", name="Claude 3 Opus", context_length=200000, supports_chinese=True, supports_vision=True, supports_tools=True, description="最强推理能力"),
            LLMProviderModel(id="claude-3-haiku-20240307", name="Claude 3 Haiku", context_length=200000, supports_chinese=True, supports_vision=False, supports_tools=True, description="极速响应"),
        ],
    ),
    LLMProvider(
        id="azure_openai",
        name="Azure OpenAI",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M5.483 21.3H24L14.025 4.013l-3.038 8.347 5.836 6.938L5.483 21.3zM13.23 2.7L6.105 8.677 0 19.253h5.505l7.961-13.518-.237-.036z'/></svg>",
        description="Microsoft Azure 托管的 OpenAI 服务，适合企业部署。",
        requires_api_key=True,
        supports_custom_base=True,
        default_base_url="https://your-resource.openai.azure.com/",
        models=[
            LLMProviderModel(id="gpt-4o", name="GPT-4o (Azure)", context_length=128000, supports_chinese=True, supports_vision=True, supports_tools=True),
            LLMProviderModel(id="gpt-4", name="GPT-4 (Azure)", context_length=128000, supports_chinese=True, supports_vision=False, supports_tools=True),
        ],
    ),
    LLMProvider(
        id="deepseek",
        name="DeepSeek",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5'/></svg>",
        description="DeepSeek 大模型，推理能力强，性价比高。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="deepseek-chat", name="DeepSeek-V3", context_length=64000, supports_chinese=True, supports_vision=False, supports_tools=True, description="通用对话，性价比高"),
            LLMProviderModel(id="deepseek-reasoner", name="DeepSeek-R1", context_length=64000, supports_chinese=True, supports_vision=False, supports_tools=True, description="推理模型，适合复杂分析"),
        ],
    ),
    LLMProvider(
        id="kimi",
        name="月之暗面 Kimi",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='10'/></svg>",
        description="Kimi 大模型，支持超长上下文。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="kimi-k2.6", name="Kimi K2.6", context_length=256000, supports_chinese=True, supports_vision=True, supports_tools=True, description="当前旗舰模型，多模态，支持推理"),
            LLMProviderModel(id="kimi-k2.5", name="Kimi K2.5", context_length=256000, supports_chinese=True, supports_vision=True, supports_tools=True, description="上一代旗舰模型"),
        ],
    ),
    LLMProvider(
        id="zhipu",
        name="智谱 GLM",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><rect x='4' y='4' width='16' height='16' rx='2'/></svg>",
        description="智谱 AI GLM 系列大模型。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="glm-4-plus", name="GLM-4-Plus", context_length=128000, supports_chinese=True, supports_vision=True, supports_tools=True, description="旗舰模型"),
            LLMProviderModel(id="glm-4-air", name="GLM-4-Air", context_length=128000, supports_chinese=True, supports_vision=False, supports_tools=True, description="高性价比"),
        ],
    ),
    LLMProvider(
        id="qwen",
        name="通义千问",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15h-2v-2h2v2zm0-4h-2V7h2v6zm4 4h-2v-2h2v2zm0-4h-2V7h2v6z'/></svg>",
        description="阿里云通义千问大模型。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="qwen-max", name="Qwen-Max", context_length=32000, supports_chinese=True, supports_vision=True, supports_tools=True, description="最强模型"),
            LLMProviderModel(id="qwen-plus", name="Qwen-Plus", context_length=128000, supports_chinese=True, supports_vision=False, supports_tools=True, description="均衡选择"),
        ],
    ),
    LLMProvider(
        id="kimi-coding",
        name="Kimi Coding",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/></svg>",
        description="Kimi 编程专用计划，针对代码生成和 Agent 场景优化。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="kimi-for-coding", name="Kimi for Coding", context_length=262144, supports_chinese=True, supports_vision=False, supports_tools=True, description="编程专用模型，支持长上下文和工具调用"),
        ],
    ),
    LLMProvider(
        id="astron-coding",
        name="讯飞星辰 Coding",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6-4.8-6 4.8 2.4-7.2-6-4.8h7.6z'/></svg>",
        description="讯飞星辰 MaaS Coding Plan，按月订阅的 AI 编码服务，底层可切换多款旗舰模型。",
        requires_api_key=True,
        supports_custom_base=False,
        default_base_url=None,
        models=[
            LLMProviderModel(id="astron-code-latest", name="Astron Code", context_length=98304, supports_chinese=True, supports_vision=False, supports_tools=True, description="统一模型名，后台可切换 DeepSeek-V3.2/GLM-5 等底层模型"),
        ],
    ),
    LLMProvider(
        id="local",
        name="本地 / 自定义",
        logo_svg="<svg viewBox='0 0 24 24' fill='currentColor'><path d='M20 3H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h7v2H8v2h8v-2h-3v-2h7a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 12H4V5h16z'/></svg>",
        description="兼容 OpenAI API 格式的本地模型或第三方服务。",
        requires_api_key=False,
        supports_custom_base=True,
        default_base_url="http://localhost:8000/v1",
        models=[
            LLMProviderModel(id="local-model", name="自定义模型", context_length=32768, supports_chinese=True, supports_vision=False, supports_tools=False, description="请填写自定义模型 ID"),
        ],
    ),
]

_PROVIDER_MAP: dict[str, LLMProvider] = {p.id: p for p in _PROVIDER_CATALOG}


# ---------- Presets (same as catalog, typed as presets) ----------

_PRESETS: list[LLMPreset] = [
    LLMPreset(
        id=p.id,
        name=p.name,
        logo_svg=p.logo_svg,
        description=p.description,
        requires_api_key=p.requires_api_key,
        supports_custom_base=p.supports_custom_base,
        default_base_url=p.default_base_url,
        models=[LLMPresetModel(**m.model_dump()) for m in p.models],
    )
    for p in _PROVIDER_CATALOG
]

_PRESET_MAP: dict[str, LLMPreset] = {p.id: p for p in _PRESETS}


def _default_provider_id() -> str:
    return "openai"


def _default_model_id(provider_id: str | None = None) -> str:
    pid = provider_id or _default_provider_id()
    prov = _PROVIDER_MAP.get(pid)
    if prov and prov.models:
        return prov.models[0].id
    return "gpt-4o-mini"


# ---------- Helpers ----------

def _get_config_or_404(config_id: str, db: Session) -> LLMConfig:
    config = db.get(LLMConfig, config_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Config not found")
    return config


def _get_or_create_active_config(db: Session) -> LLMConfig:
    config = db.scalar(select(LLMConfig).where(LLMConfig.is_active == True).order_by(LLMConfig.updated_at.desc()))
    if config is None:
        config = LLMConfig(
            id=str(uuid4()),
            name="Default",
            provider=_default_provider_id(),
            model=_default_model_id(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _do_test(payload: LLMTestRequest) -> dict[str, Any]:
    import time

    provider = _PROVIDER_MAP.get(payload.provider)
    if provider is None:
        return {
            "success": False,
            "latency_ms": 0,
            "message": f"Unknown provider: {payload.provider}",
            "model": None,
            "usage": None,
        }

    start = time.time()

    # Resolve default base URL for known providers if not overridden
    base_url = payload.api_base
    if not base_url and provider.default_base_url:
        base_url = provider.default_base_url

    if payload.provider == "openai":
        url = base_url or "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "anthropic":
        url = base_url or "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": payload.api_key or "", "Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "azure_openai":
        url = base_url or ""
        if not url:
            return {"success": False, "latency_ms": 0, "message": "Azure OpenAI requires a custom base URL", "model": None, "usage": None}
        headers = {"api-key": payload.api_key or "", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "deepseek":
        url = base_url or "https://api.deepseek.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "kimi":
        url = base_url or "https://api.moonshot.cn/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "zhipu":
        url = base_url or "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "qwen":
        url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "kimi-coding":
        url = base_url or "https://api.kimi.com/coding/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "astron-coding":
        url = base_url or "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or ''}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5}
    elif payload.provider == "local":
        url = base_url or "http://localhost:8000/v1/chat/completions"
        headers = {"Authorization": f"Bearer {payload.api_key or 'no-key'}", "Content-Type": "application/json"}
        body = {"model": payload.model, "messages": [{"role": "user", "content": "Say 'ok' only."}], "max_tokens": 5, "stream": False}
    else:
        return {"success": False, "latency_ms": 0, "message": f"Test not implemented for provider: {payload.provider}", "model": None, "usage": None}

    try:
        with create_httpx_client(timeout=float(payload.timeout or 30), headers=headers, proxy=payload.proxy_url) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
            latency = int((time.time() - start) * 1000)

            model_used = data.get("model") or payload.model
            usage = data.get("usage")

            content = ""
            if payload.provider == "anthropic":
                content_blocks = data.get("content", [])
                if content_blocks and isinstance(content_blocks[0], dict):
                    content = content_blocks[0].get("text", "")
            else:
                choices = data.get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0].get("message", {})
                    content = msg.get("content", "")

            if "ok" in content.lower():
                return {"success": True, "latency_ms": latency, "message": f"连接成功 ({latency}ms)", "model": model_used, "usage": usage}
            return {"success": True, "latency_ms": latency, "message": f"连接成功，但响应异常: {content[:80]}", "model": model_used, "usage": usage}
    except Exception as exc:
        latency = int((time.time() - start) * 1000)
        return {"success": False, "latency_ms": latency, "message": f"连接失败: {str(exc)[:200]}", "model": None, "usage": None}


# ---------- Routes ----------

@router.get("/providers", response_model=LLMProvidersResponse)
def list_providers() -> dict[str, Any]:
    return {
        "providers": _PROVIDER_CATALOG,
        "default_provider": _default_provider_id(),
        "default_model": _default_model_id(),
    }


@router.get("/presets", response_model=LLMPresetsResponse)
def list_presets() -> dict[str, Any]:
    return {"presets": _PRESETS}


# Legacy single-config compatible routes
@router.get("/config", response_model=LLMConfigRead)
def get_config(db: Session = Depends(get_db)) -> LLMConfig:
    return _get_or_create_active_config(db)


@router.put("/config", response_model=LLMConfigRead)
def update_config(payload: LLMConfigUpdate, db: Session = Depends(get_db)) -> LLMConfig:
    config = _get_or_create_active_config(db)
    changes = payload.model_dump(exclude_unset=True)

    if "provider" in changes:
        pid = changes["provider"]
        if pid not in _PROVIDER_MAP:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider: {pid}")
        prov = _PROVIDER_MAP[pid]
        model_ids = {m.id for m in prov.models}
        if config.model not in model_ids and "model" not in changes:
            changes["model"] = prov.models[0].id if prov.models else ""

    if "model" in changes:
        provider_id = changes.get("provider", config.provider)
        prov = _PROVIDER_MAP.get(provider_id)
        if prov:
            model_ids = {m.id for m in prov.models}
            if changes["model"] not in model_ids and provider_id != "local":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model '{changes['model']}' is not available for provider '{provider_id}'",
                )

    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(config, field, value)

    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    return config


# Multi-config management routes
@router.get("/configs", response_model=LLMConfigListResponse)
def list_configs(db: Session = Depends(get_db)) -> dict[str, Any]:
    stmt = select(LLMConfig).order_by(LLMConfig.updated_at.desc())
    rows = list(db.scalars(stmt).all())
    active_id = None
    for r in rows:
        if r.is_active:
            active_id = r.id
            break
    return {"configs": rows, "active_id": active_id}


@router.post("/configs", response_model=LLMConfigRead, status_code=status.HTTP_201_CREATED)
def create_config(payload: LLMConfigCreate, db: Session = Depends(get_db)) -> LLMConfig:
    preset = _PRESET_MAP.get(payload.provider)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider: {payload.provider}")

    model_ids = {m.id for m in preset.models}
    if payload.model not in model_ids and payload.provider != "local":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{payload.model}' is not available for provider '{payload.provider}'",
        )

    now = datetime.now(timezone.utc)
    config = LLMConfig(
        id=str(uuid4()),
        name=payload.name.strip(),
        provider=payload.provider,
        model=payload.model,
        api_key=payload.api_key.strip() if payload.api_key else None,
        api_base=payload.api_base.strip() if payload.api_base else None,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        timeout=payload.timeout,
        proxy_url=payload.proxy_url.strip() if payload.proxy_url else None,
        use_system_proxy=payload.use_system_proxy,
        extra_headers=payload.extra_headers or {},
        extra_body=payload.extra_body or {},
        strategy_mode=payload.strategy_mode,
        enable_reasoning=payload.enable_reasoning,
        preferred_max_tokens=payload.preferred_max_tokens,
        is_active=False,
        created_at=now,
        updated_at=now,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("/configs/{config_id}", response_model=LLMConfigRead)
def get_config_by_id(config_id: str, db: Session = Depends(get_db)) -> LLMConfig:
    return _get_config_or_404(config_id, db)


@router.patch("/configs/{config_id}", response_model=LLMConfigRead)
def patch_config(config_id: str, payload: LLMConfigUpdate, db: Session = Depends(get_db)) -> LLMConfig:
    config = _get_config_or_404(config_id, db)
    changes = payload.model_dump(exclude_unset=True)

    if "provider" in changes:
        pid = changes["provider"]
        if pid not in _PROVIDER_MAP:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider: {pid}")
        prov = _PROVIDER_MAP[pid]
        model_ids = {m.id for m in prov.models}
        if config.model not in model_ids and "model" not in changes:
            changes["model"] = prov.models[0].id if prov.models else ""

    if "model" in changes:
        provider_id = changes.get("provider", config.provider)
        prov = _PROVIDER_MAP.get(provider_id)
        if prov:
            model_ids = {m.id for m in prov.models}
            if changes["model"] not in model_ids and provider_id != "local":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Model '{changes['model']}' is not available for provider '{provider_id}'",
                )

    for field, value in changes.items():
        if isinstance(value, str):
            value = value.strip()
        setattr(config, field, value)

    config.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(config)
    return config


@router.delete("/configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_config(config_id: str, db: Session = Depends(get_db)) -> None:
    config = _get_config_or_404(config_id, db)
    # Ensure at least one config remains
    all_count = len(list(db.scalars(select(LLMConfig)).all()))
    if all_count <= 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the last config")
    db.delete(config)
    db.commit()
    return None


@router.post("/configs/{config_id}/activate", response_model=LLMConfigRead)
def activate_config(config_id: str, db: Session = Depends(get_db)) -> LLMConfig:
    target = _get_config_or_404(config_id, db)
    now = datetime.now(timezone.utc)

    # Deactivate all others
    for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_active == True)).all():
        cfg.is_active = False
        cfg.updated_at = now

    target.is_active = True
    target.updated_at = now
    db.commit()
    db.refresh(target)
    return target


@router.post("/configs/{config_id}/test", response_model=LLMTestResponse)
def test_config(config_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    config = _get_config_or_404(config_id, db)
    payload = LLMTestRequest(
        provider=config.provider,
        model=config.model,
        api_key=config.api_key,
        api_base=config.api_base,
        proxy_url=config.proxy_url,
        use_system_proxy=config.use_system_proxy,
        timeout=config.timeout,
    )
    return _do_test(payload)


@router.post("/test", response_model=LLMTestResponse)
def test_connection(payload: LLMTestRequest) -> dict[str, Any]:
    return _do_test(payload)
