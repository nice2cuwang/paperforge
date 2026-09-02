"""Tests for the single-source LLM provider registry.

These guard the CC Switch-style invariant: every provider is one declarative
record, and all derived views (catalog, base URLs, strategy keys, capability
matrix, protocol classification, endpoint/header resolution) stay consistent
with it. They also pin the quirky per-provider behaviors that must be
preserved (claude-cn uses Bearer for /models, xiaomi-mimo needs a custom base
to list models, OpenRouter adds listing headers, etc.).
"""

from app.services import llm_providers as P
from app.services.llm_service import (
    DefaultStrategy,
    DeepSeekR1Strategy,
    DeepSeekV3Strategy,
    AnthropicStrategy,
    KimiK2Strategy,
    OpenAIStrategy,
    ProviderCapability,
    get_capability,
    get_strategy,
)


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------

def test_no_duplicate_provider_ids():
    ids = [p.id for p in P.PROVIDER_LIST]
    assert len(ids) == len(set(ids))


def test_every_provider_has_base_url():
    # Every shipped provider must carry a runtime base URL (replaces the old
    # _DEFAULT_BASE_URLS dict which had to cover them all).
    missing = [p.id for p in P.PROVIDER_LIST if not p.base_url]
    assert missing == []


def test_every_strategy_key_resolves():
    valid = {
        P.STRATEGY_OPENAI, P.STRATEGY_ANTHROPIC, P.STRATEGY_KIMI_K2,
        P.STRATEGY_DEEPSEEK_V3, P.STRATEGY_DEEPSEEK_R1, P.STRATEGY_DEFAULT,
    }
    bad = [p.id for p in P.PROVIDER_LIST if p.strategy_key not in valid]
    assert bad == []


def test_categories_valid():
    valid = {P.CATEGORY_MAJOR, P.CATEGORY_MARKETPLACE, P.CATEGORY_LOCAL}
    bad = [p.id for p in P.PROVIDER_LIST if p.category not in valid]
    assert bad == []


def test_known_provider_count_preserved():
    # 30 major + 28 marketplace + 2 local (local generic / ollama). Update if
    # providers are added/dropped.
    from collections import Counter
    counts = Counter(p.category for p in P.PROVIDER_LIST)
    assert counts == {"major": 30, "marketplace": 28, "local": 2}
    assert len(P.PROVIDER_LIST) == 60


# ---------------------------------------------------------------------------
# Derived views match the registry
# ---------------------------------------------------------------------------

def test_provider_map_matches_list():
    assert set(P.PROVIDER_MAP) == {p.id for p in P.PROVIDER_LIST}


def test_default_base_urls_match_registry():
    assert P.DEFAULT_BASE_URLS == {p.id: p.base_url for p in P.PROVIDER_LIST if p.base_url}


def test_strategy_keys_match_registry():
    assert P.STRATEGY_KEYS == {p.id: p.strategy_key for p in P.PROVIDER_LIST}


def test_capability_map_matches_registry():
    assert set(P.CAPABILITY_MAP) == {p.id for p in P.PROVIDER_LIST}
    kimi = P.CAPABILITY_MAP["kimi"]
    assert (kimi.json_mode, kimi.reasoning, kimi.tool_call, kimi.max_context) == (True, True, True, 256000)


# ---------------------------------------------------------------------------
# Strategy resolution
# ---------------------------------------------------------------------------

def test_get_strategy_by_provider():
    assert isinstance(get_strategy("deepseek"), DeepSeekV3Strategy)
    assert isinstance(get_strategy("kimi"), KimiK2Strategy)
    assert isinstance(get_strategy("stepfun"), KimiK2Strategy)
    assert isinstance(get_strategy("anthropic"), AnthropicStrategy)
    assert isinstance(get_strategy("claude-cn"), AnthropicStrategy)
    assert isinstance(get_strategy("dmxapi"), OpenAIStrategy)
    assert isinstance(get_strategy("openai"), OpenAIStrategy)


def test_get_strategy_model_override():
    assert isinstance(get_strategy("deepseek", "deepseek-reasoner"), DeepSeekR1Strategy)
    assert isinstance(get_strategy("deepseek", "deepseek-chat"), DeepSeekV3Strategy)


def test_get_strategy_unknown_falls_back():
    assert isinstance(get_strategy("does-not-exist"), DefaultStrategy)


def test_get_capability_defaults():
    assert isinstance(get_capability("does-not-exist"), ProviderCapability)
    assert get_capability("local").tool_call is False
    assert get_capability("local").max_context == 32768


# ---------------------------------------------------------------------------
# Endpoint / header resolution
# ---------------------------------------------------------------------------

def test_chat_endpoint_by_protocol():
    assert P.chat_endpoint(P.get_provider("openai"), None) == "https://api.openai.com/v1/chat/completions"
    assert P.chat_endpoint(P.get_provider("anthropic"), None, "claude-x") == "https://api.anthropic.com/v1/messages"
    assert P.chat_endpoint(P.get_provider("local"), None) == "http://localhost:8000/v1/chat/completions"
    assert P.chat_endpoint(P.get_provider("openrouter"), None) == "https://openrouter.ai/api/v1/chat/completions"
    assert P.chat_endpoint(P.get_provider("aws-bedrock"), None, "claude") == "https://bedrock-runtime.us-east-1.amazonaws.com/model/claude/converse"
    # custom api_base overrides the registry default
    assert P.chat_endpoint(P.get_provider("openai"), "https://proxy.example.com/v1") == "https://proxy.example.com/v1/chat/completions"


def test_chat_headers_by_protocol():
    h = P.chat_headers(P.get_provider("anthropic"), "sk-x")
    assert h["x-api-key"] == "sk-x" and h["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in h
    assert P.chat_headers(P.get_provider("azure_openai"), "k")["api-key"] == "k"
    assert P.chat_headers(P.get_provider("openai"), "sk-x")["Authorization"] == "Bearer sk-x"
    # chat headers do NOT include OpenRouter listing headers (only test/models do)
    assert "HTTP-Referer" not in P.chat_headers(P.get_provider("openrouter"), "sk-x")


def test_models_endpoint_disabled_providers():
    assert P.models_endpoint(P.get_provider("aws-bedrock"), None) == ""
    # xiaomi-mimo requires a user-set custom base to reach /models
    assert P.models_endpoint(P.get_provider("xiaomi-mimo"), None) == ""
    assert P.models_endpoint(P.get_provider("xiaomi-mimo"), "https://x.example.com/v1") == "https://x.example.com/v1/models"


def test_models_endpoint_normal():
    assert P.models_endpoint(P.get_provider("openai"), None) == "https://api.openai.com/v1/models"
    assert P.models_endpoint(P.get_provider("openrouter"), None) == "https://openrouter.ai/api/v1/models"


def test_models_headers_auth_variants():
    # anthropic -> x-api-key
    h = P.models_headers(P.get_provider("anthropic"), "sk-x")
    assert h["x-api-key"] == "sk-x"
    # claude-cn / claudeapi use Bearer for /models even though chat is anthropic
    h2 = P.models_headers(P.get_provider("claude-cn"), "sk-x")
    assert h2.get("Authorization") == "Bearer sk-x"
    assert "x-api-key" not in h2
    # azure -> api-key
    assert P.models_headers(P.get_provider("azure_openai"), "k")["api-key"] == "k"
    # openrouter -> Bearer + listing headers
    h3 = P.models_headers(P.get_provider("openrouter"), "sk-x")
    assert h3.get("Authorization") == "Bearer sk-x"
    assert h3["HTTP-Referer"] == "https://paperforge.local"
    assert h3["X-Title"] == "PaperForge"
    # no key -> no Authorization for bearer providers
    assert "Authorization" not in P.models_headers(P.get_provider("dmxapi"), None)


def test_test_headers_local_uses_no_key_sentinel():
    h = P.test_headers(P.get_provider("local"), None)
    assert h["Authorization"] == "Bearer no-key"
    # ollama shares the local category semantics
    h2 = P.test_headers(P.get_provider("ollama"), None)
    assert h2["Authorization"] == "Bearer no-key"


def test_no_models_messages():
    assert "AWS Bedrock" in P.no_models_message(P.get_provider("aws-bedrock"))
    assert "Mimo" in P.no_models_message(P.get_provider("xiaomi-mimo"))
    assert P.no_models_message(P.get_provider("openai")) is None


def test_local_body_force_non_streaming():
    # Local inference servers (Ollama / vLLM / llama.cpp) can hang or misbehave
    # on streamed probe requests -- the connectivity probe forces stream=false.
    assert P.test_body(P.get_provider("local"), "x")["stream"] is False
    assert P.test_body(P.get_provider("ollama"), "x")["stream"] is False


def test_parse_models_response_normalizes():
    data = {"data": [
        {"id": "b-model", "owned_by": "org", "created": 2},
        {"id": "a-model", "created": 1},
        {"name": "named-only"},
        {"foo": "no-id"},
    ]}
    out = P.parse_models_response(data)
    # newest-first sort, no-id entries dropped
    assert [m["id"] for m in out] == ["b-model", "a-model", "named-only"]
    assert out[0]["owned_by"] == "org"


# ---------------------------------------------------------------------------
# API catalog/preset projection (llm_config routes)
# ---------------------------------------------------------------------------

def test_catalog_and_presets_built_from_registry():
    from app.api.routes import llm_config as R
    assert len(R._PROVIDER_CATALOG) == len(P.PROVIDER_LIST)
    assert set(R._PROVIDER_MAP) == set(P.PROVIDER_MAP)
    assert len(R._PRESETS) == len(P.PROVIDER_LIST)
    assert set(R._PRESET_MAP) == set(P.PROVIDER_MAP)


def test_catalog_default_base_url_preserved():
    from app.api.routes import llm_config as R
    # openai intentionally exposes None (no custom base needed)
    assert R._PROVIDER_MAP["openai"].default_base_url is None
    # marketplaces expose their base URL
    assert R._PROVIDER_MAP["dmxapi"].default_base_url == "https://www.dmxapi.com/v1"
    # anthropic also None
    assert R._PROVIDER_MAP["anthropic"].default_base_url is None


def test_presets_carry_category():
    from app.api.routes import llm_config as R
    from collections import Counter
    cats = Counter(p.category for p in R._PRESETS)
    assert cats["major"] == 30
    assert cats["marketplace"] == 28
    assert cats["local"] == 2
    # local preset is the local category
    assert R._PRESET_MAP["local"].category == "local"
    assert R._PRESET_MAP["dmxapi"].category == "marketplace"
