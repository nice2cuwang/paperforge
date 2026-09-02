from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import logging
import time as _time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

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
    LLMModelsFetchRequest,
    LLMModelsFetchResponse,
    LLMFetchedModel,
)
from app.services.http_client import create_httpx_client, resolve_proxy_url
from app.services.llm_providers import (
    PROVIDER_LIST,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_AZURE,
    PROTOCOL_BEDROCK,
    ProviderDef,
    chat_endpoint,
    get_provider,
    models_endpoint,
    models_headers,
    no_models_message,
    parse_models_response,
    test_body,
    test_headers,
)

router = APIRouter(prefix="/api/llm", tags=["llm"])

# ---------- In-memory cache for fetched model lists ----------
# Key: "{provider}|{base_url}" -> (timestamp, models: list[dict])
_MODELS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_MODELS_CACHE_TTL = 600  # 10 minutes


# ---------- Built-in provider catalog (derived from the single registry) ----------
#
# The source of truth is ``app.services.llm_providers``. The Pydantic objects
# below are projections built once at import time for the ``/providers`` and
# ``/presets`` API responses.

def _provider_model(m: Any) -> LLMProviderModel:
    return LLMProviderModel(
        id=m.id,
        name=m.name,
        context_length=m.context_length,
        supports_chinese=m.supports_chinese,
        supports_vision=m.supports_vision,
        supports_tools=m.supports_tools,
        description=m.description,
    )


def _to_provider(p: ProviderDef) -> LLMProvider:
    return LLMProvider(
        id=p.id,
        name=p.name,
        logo_svg=p.logo_svg,
        description=p.description,
        requires_api_key=p.requires_api_key,
        supports_custom_base=p.supports_custom_base,
        default_base_url=p.default_base_url,
        models=[_provider_model(m) for m in p.models],
    )


def _to_preset(p: ProviderDef) -> LLMPreset:
    return LLMPreset(
        id=p.id,
        name=p.name,
        logo_svg=p.logo_svg,
        description=p.description,
        requires_api_key=p.requires_api_key,
        supports_custom_base=p.supports_custom_base,
        default_base_url=p.default_base_url,
        category=p.category,
        models=[
            LLMPresetModel(
                id=m.id,
                name=m.name,
                context_length=m.context_length,
                supports_chinese=m.supports_chinese,
                supports_vision=m.supports_vision,
                supports_tools=m.supports_tools,
                description=m.description,
            )
            for m in p.models
        ],
    )


_PROVIDER_CATALOG: list[LLMProvider] = [_to_provider(p) for p in PROVIDER_LIST]
_PROVIDER_MAP: dict[str, LLMProvider] = {p.id: p for p in _PROVIDER_CATALOG}

_PRESETS: list[LLMPreset] = [_to_preset(p) for p in PROVIDER_LIST]
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


def _is_unchanged_api_key(value: str | None) -> bool:
    """True for values that must not overwrite the stored API key.

    ``LLMConfigRead`` masks the key as ``sk-1****abcd`` for display. When the
    edit form posts that masked value (or an empty string) back, we must keep
    the original key instead of clobbering it — otherwise the next test/chat
    call sends the masked string and the provider returns 401.
    """
    if not value:
        return True
    return "****" in value


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

    p = get_provider(payload.provider)
    if p is None:
        return {
            "success": False,
            "latency_ms": 0,
            "message": f"Unknown provider: {payload.provider}",
            "model": None,
            "usage": None,
        }

    # AWS Bedrock needs AWS credentials, not an API key body.
    if p.protocol == PROTOCOL_BEDROCK:
        return {
            "success": False,
            "latency_ms": 0,
            "message": "AWS Bedrock 需要配置 AWS 凭证，请通过 .env 设置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION",
            "model": payload.model,
            "usage": None,
        }

    # Resolve endpoint/headers/body via the registry. Using the provider's
    # runtime base_url (always set in the registry) fixes the old behavior
    # where testing a bare OpenAI/local/OpenRouter config produced a relative
    # URL and failed.
    url = chat_endpoint(p, payload.api_base, payload.model)
    headers = test_headers(p, payload.api_key)
    body = test_body(p, payload.model)

    start = time.time()
    try:
        with create_httpx_client(timeout=float(payload.timeout or 30), headers=headers, proxy=payload.proxy_url) as client:
            response = client.post(url, json=body)
            response.raise_for_status()
            data = response.json()
            latency = int((time.time() - start) * 1000)

            model_used = data.get("model") or payload.model
            usage = data.get("usage")

            content = ""
            if p.protocol == PROTOCOL_ANTHROPIC:
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
        return {"success": False, "latency_ms": latency, "message": f"连接失败: {_friendly_test_error(exc)}", "model": None, "usage": None}


def _friendly_test_error(exc: Exception) -> str:
    """Surface the provider's own error message instead of a bare HTTP status.

    A raw "404 Not Found for url ..." hides the actionable reason — e.g.
    Volcano Ark returns ``UnsupportedModel: the model does not support the
    coding plan feature`` or ``ModelNotOpen: activate the model in the Ark
    console``. Parsing the JSON error body turns a confusing failure into a
    fix instruction.
    """
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            detail = resp.json()
            err = detail.get("error") if isinstance(detail, dict) else None
            if isinstance(err, dict):
                code = str(err.get("code") or "")
                message = str(err.get("message") or "")
                text = " — ".join(x for x in (code, message) if x)
                if text:
                    return f"HTTP {resp.status_code}: {text[:180]}"
            elif isinstance(err, str) and err:
                return f"HTTP {resp.status_code}: {err[:180]}"
        except Exception:
            pass
        return f"HTTP {resp.status_code}（接口路径或模型可能不正确）"
    return str(exc)[:200]


# ---------- Live model list fetching (CC Switch-style) ----------

def _fetch_models_from_provider(
    p: ProviderDef | None,
    api_key: str | None,
    api_base: str | None,
    proxy_url: str | None,
    timeout: float,
) -> tuple[list[dict[str, Any]], str, int]:
    """Call the provider's /models endpoint. Returns (models, message, latency_ms)."""
    if p is None:
        return [], "未知 provider，请在注册表中确认 provider id", 0

    url = models_endpoint(p, api_base)
    if not url:
        friendly = no_models_message(p)
        return [], friendly or f"无法解析 {p.id} 的 /models 端点", 0

    headers = models_headers(p, api_key)
    start = _time.time()
    try:
        with create_httpx_client(timeout=float(timeout), headers=headers, proxy=proxy_url) as client:
            resp = client.get(url)
            # Surface auth errors with friendlier message before raise
            if resp.status_code in (401, 403):
                latency = int((_time.time() - start) * 1000)
                try:
                    detail = resp.json()
                    msg_obj = detail.get("error") if isinstance(detail, dict) else detail
                    if isinstance(msg_obj, dict):
                        msg_obj = msg_obj.get("message") or msg_obj.get("type") or str(msg_obj)
                    elif not isinstance(msg_obj, str):
                        msg_obj = resp.text[:120]
                except Exception:
                    msg_obj = resp.text[:120]
                logger.warning("Fetch models auth failed for %s: %s %s", p.id, resp.status_code, msg_obj)
                return [], f"鉴权失败 ({resp.status_code})：请检查 API Key 是否有效。{str(msg_obj)[:120]}", latency
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        latency = int((_time.time() - start) * 1000)
        msg = str(exc)[:200]
        logger.warning("Fetch models failed for %s: %s", p.id, msg)
        return [], f"拉取失败: {msg}", latency

    latency = int((_time.time() - start) * 1000)
    models = parse_models_response(data)
    if not models:
        return [], "接口返回为空或格式无法解析", latency
    return models, f"成功获取 {len(models)} 个模型 ({latency}ms)", latency


@router.post("/models-fetch", response_model=LLMModelsFetchResponse)
def fetch_models(payload: LLMModelsFetchRequest, db: Session = Depends(get_db)) -> LLMModelsFetchResponse:
    """Fetch live available models from a provider's API (like CC Switch).

    If `config_id` is given, provider/api_key/api_base/proxy_url are loaded
    from that saved config; fields in the payload override the saved ones.
    Results are cached in memory for 10 minutes per (provider, base_url).
    """
    # Resolve from saved config if requested
    provider = payload.provider
    api_key = payload.api_key
    api_base = payload.api_base
    proxy_url = payload.proxy_url

    if payload.config_id:
        cfg = db.get(LLMConfig, payload.config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        # Config is the base; payload fields override when explicitly provided
        provider = cfg.provider
        if api_key is None:
            api_key = cfg.api_key
        if api_base is None:
            api_base = cfg.api_base
        if proxy_url is None:
            proxy_url = cfg.proxy_url

    # If no explicit proxy was given (via payload or config_id), honor the
    # PaperForge process-level proxy env var so the call can actually leave
    # the container / host.
    if proxy_url is None:
        proxy_url = resolve_proxy_url()

    p = get_provider(provider)

    # Check cache (use resolved base_url, fall back to registry default for key)
    effective_base = api_base or (p.base_url if p else None) or ""
    cache_key = f"{provider}|{effective_base}"
    now = _time.time()
    cached = _MODELS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _MODELS_CACHE_TTL:
        models = [LLMFetchedModel(**m) for m in cached[1]]
        return LLMModelsFetchResponse(
            success=True,
            models=models,
            count=len(models),
            message=f"缓存命中 ({len(models)} 个模型)",
            cached=True,
            latency_ms=0,
        )

    models_raw, message, latency = _fetch_models_from_provider(
        p=p,
        api_key=api_key,
        api_base=api_base,
        proxy_url=proxy_url,
        timeout=payload.timeout,
    )
    if not models_raw:
        return LLMModelsFetchResponse(
            success=False, models=[], count=0, message=message, cached=False, latency_ms=latency
        )

    # Cache only on success
    _MODELS_CACHE[cache_key] = (now, models_raw)
    models = [LLMFetchedModel(**m) for m in models_raw]
    return LLMModelsFetchResponse(
        success=True,
        models=models,
        count=len(models),
        message=message,
        cached=False,
        latency_ms=latency,
    )


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

    # Never clobber the stored key with the masked display value or an empty
    # string echoed back from the edit form.
    if "api_key" in changes and _is_unchanged_api_key(changes.get("api_key")):
        del changes["api_key"]

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
            if changes["model"] not in model_ids and provider_id not in ("local", "ollama", "openrouter", "xiaomi-mimo"):
                # Allow any model ID but warn - new releases / OpenRouter custom model names / Xiaomi Mimo [1m] suffixes
                logger.warning("Accepting unknown model '%s' for provider '%s'", changes["model"], provider_id)

    # The vision role is exclusive: at most one config carries it.
    if changes.get("is_vision"):
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_vision == True)).all():
            if cfg.id != config.id:
                cfg.is_vision = False

    # The image-generation role is exclusive too.
    if changes.get("is_image_gen"):
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_image_gen == True)).all():
            if cfg.id != config.id:
                cfg.is_image_gen = False

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
    vision_id = None
    image_gen_id = None
    for r in rows:
        if r.is_active and active_id is None:
            active_id = r.id
        if r.is_vision and vision_id is None:
            vision_id = r.id
        if r.is_image_gen and image_gen_id is None:
            image_gen_id = r.id
    return {"configs": rows, "active_id": active_id, "vision_id": vision_id, "image_gen_id": image_gen_id}


@router.post("/configs", response_model=LLMConfigRead, status_code=status.HTTP_201_CREATED)
def create_config(payload: LLMConfigCreate, db: Session = Depends(get_db)) -> LLMConfig:
    preset = _PRESET_MAP.get(payload.provider)
    if preset is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider: {payload.provider}")

    model_ids = {m.id for m in preset.models}
    if payload.model not in model_ids and payload.provider not in ("local", "ollama", "openrouter", "xiaomi-mimo"):
        # Allow any model ID but warn - new releases / OpenRouter custom model names / Xiaomi Mimo [1m] suffixes
        logger.warning("Accepting unknown model '%s' for provider '%s'", payload.model, payload.provider)

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
        is_vision=False,
        is_image_gen=False,
        created_at=now,
        updated_at=now,
    )
    db.add(config)

    # Role flags are exclusive: creating with one set clears it on others.
    if payload.is_vision:
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_vision == True)).all():
            cfg.is_vision = False
        config.is_vision = True
    if payload.is_image_gen:
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_image_gen == True)).all():
            cfg.is_image_gen = False
        config.is_image_gen = True

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

    # Never clobber the stored key with the masked display value or an empty
    # string echoed back from the edit form.
    if "api_key" in changes and _is_unchanged_api_key(changes.get("api_key")):
        del changes["api_key"]

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
            if changes["model"] not in model_ids and provider_id not in ("local", "ollama", "openrouter", "xiaomi-mimo"):
                # Allow any model ID but warn - new releases / OpenRouter custom model names / Xiaomi Mimo [1m] suffixes
                logger.warning("Accepting unknown model '%s' for provider '%s'", changes["model"], provider_id)

    # The vision role is exclusive: at most one config carries it.
    if changes.get("is_vision"):
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_vision == True)).all():
            if cfg.id != config.id:
                cfg.is_vision = False

    # The image-generation role is exclusive too.
    if changes.get("is_image_gen"):
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_image_gen == True)).all():
            if cfg.id != config.id:
                cfg.is_image_gen = False

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


@router.post("/configs/{config_id}/vision", response_model=LLMConfigRead)
def designate_vision_config(config_id: str, db: Session = Depends(get_db)) -> LLMConfig:
    """Toggle the vision-model role on a config (exclusive).

    The designated vision config handles image-input calls (e.g. figure
    tagging) so the main writing model does not need multimodal support.
    Calling this on the current vision config removes the role entirely,
    falling back to the active config for image calls.
    """
    target = _get_config_or_404(config_id, db)
    now = datetime.now(timezone.utc)

    if target.is_vision:
        target.is_vision = False
    else:
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_vision == True)).all():
            cfg.is_vision = False
            cfg.updated_at = now
        target.is_vision = True

    target.updated_at = now
    db.commit()
    db.refresh(target)
    return target


@router.post("/configs/{config_id}/image-gen", response_model=LLMConfigRead)
def designate_image_gen_config(config_id: str, db: Session = Depends(get_db)) -> LLMConfig:
    """Toggle the image-generation role on a config (exclusive).

    The designated config is used by the figure pipeline to generate topical
    illustrations (text-to-image) when no suitable paper figure exists.
    Calling this on the current image-gen config removes the role, falling
    back to Pollinations/SVG.
    """
    target = _get_config_or_404(config_id, db)
    now = datetime.now(timezone.utc)

    if target.is_image_gen:
        target.is_image_gen = False
    else:
        for cfg in db.scalars(select(LLMConfig).where(LLMConfig.is_image_gen == True)).all():
            cfg.is_image_gen = False
            cfg.updated_at = now
        target.is_image_gen = True

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
