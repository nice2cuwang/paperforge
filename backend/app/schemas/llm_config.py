from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class LLMConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str = "Default"
    provider: str
    model: str
    api_key: str | None = None
    api_base: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 60
    proxy_url: str | None = None
    use_system_proxy: bool = False
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    strategy_mode: str = "balanced"
    enable_reasoning: bool = True
    preferred_max_tokens: int | None = None
    is_active: bool = True
    is_vision: bool = False
    is_image_gen: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("api_key")
    def mask_api_key(self, value: str | None) -> str | None:
        if value and len(value) > 8:
            return value[:4] + "****" + value[-4:]
        return value

    @field_serializer("created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None


class LLMConfigCreate(BaseModel):
    name: str = Field(default="New Config", min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    api_key: str | None = None
    api_base: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 60
    proxy_url: str | None = None
    use_system_proxy: bool = False
    extra_headers: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    strategy_mode: str = "balanced"
    enable_reasoning: bool = True
    preferred_max_tokens: int | None = None
    is_vision: bool = False
    is_image_gen: bool = False

    @field_validator("temperature")
    @classmethod
    def clamp_temperature(cls, v: float) -> float:
        return max(0.0, min(2.0, v))

    @field_validator("max_tokens")
    @classmethod
    def clamp_max_tokens(cls, v: int) -> int:
        return max(1, min(128000, v))

    @field_validator("timeout")
    @classmethod
    def clamp_timeout(cls, v: int) -> int:
        return max(5, min(600, v))

    @field_validator("preferred_max_tokens")
    @classmethod
    def clamp_preferred_max_tokens(cls, v: int | None) -> int | None:
        if v is not None:
            return max(1, min(128000, v))
        return v


class LLMConfigUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    proxy_url: str | None = None
    use_system_proxy: bool | None = None
    extra_headers: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    strategy_mode: str | None = None
    enable_reasoning: bool | None = None
    preferred_max_tokens: int | None = None
    is_active: bool | None = None
    is_vision: bool | None = None
    is_image_gen: bool | None = None

    @field_validator("temperature")
    @classmethod
    def clamp_temperature(cls, v: float | None) -> float | None:
        if v is not None:
            return max(0.0, min(2.0, v))
        return v

    @field_validator("max_tokens")
    @classmethod
    def clamp_max_tokens(cls, v: int | None) -> int | None:
        if v is not None:
            return max(1, min(128000, v))
        return v

    @field_validator("timeout")
    @classmethod
    def clamp_timeout(cls, v: int | None) -> int | None:
        if v is not None:
            return max(5, min(600, v))
        return v

    @field_validator("preferred_max_tokens")
    @classmethod
    def clamp_preferred_max_tokens(cls, v: int | None) -> int | None:
        if v is not None:
            return max(1, min(128000, v))
        return v


class LLMTestRequest(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str | None = None
    api_base: str | None = None
    proxy_url: str | None = None
    use_system_proxy: bool = False
    timeout: int = 30


class LLMTestResponse(BaseModel):
    success: bool
    latency_ms: int
    message: str
    model: str | None = None
    usage: dict[str, Any] | None = None


class LLMModelsFetchRequest(BaseModel):
    """Request body for fetching live model list from a provider's API."""
    provider: str = Field(min_length=1, max_length=64)
    api_key: str | None = None
    api_base: str | None = None
    proxy_url: str | None = None
    use_system_proxy: bool = False
    timeout: int = 15
    config_id: str | None = Field(
        default=None,
        description="If provided, load provider/api_key/api_base/proxy from this saved config",
    )


class LLMFetchedModel(BaseModel):
    """A single model returned by a provider's /models endpoint."""
    id: str
    owned_by: str | None = None
    created: int | None = None


class LLMModelsFetchResponse(BaseModel):
    success: bool
    models: list[LLMFetchedModel] = Field(default_factory=list)
    count: int = 0
    message: str
    cached: bool = False
    latency_ms: int = 0


class LLMProviderModel(BaseModel):
    id: str
    name: str
    context_length: int
    supports_chinese: bool
    supports_vision: bool
    supports_tools: bool
    description: str | None = None


class LLMProvider(BaseModel):
    id: str
    name: str
    logo_svg: str
    description: str
    requires_api_key: bool
    supports_custom_base: bool
    default_base_url: str | None = None
    models: list[LLMProviderModel]


class LLMProvidersResponse(BaseModel):
    providers: list[LLMProvider]
    default_provider: str = "openai"
    default_model: str = "gpt-4o-mini"


class LLMPresetModel(BaseModel):
    id: str
    name: str
    context_length: int
    supports_chinese: bool
    supports_vision: bool
    supports_tools: bool
    description: str | None = None


class LLMPreset(BaseModel):
    id: str
    name: str
    logo_svg: str
    description: str
    requires_api_key: bool
    supports_custom_base: bool
    default_base_url: str | None = None
    category: str = "major"
    models: list[LLMPresetModel]


class LLMPresetsResponse(BaseModel):
    presets: list[LLMPreset]


class LLMConfigListResponse(BaseModel):
    configs: list[LLMConfigRead]
    active_id: str | None = None
    vision_id: str | None = None
    image_gen_id: str | None = None
