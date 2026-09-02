"""Single source of truth for LLM provider definitions.

CC Switch-style: every provider is one declarative ``ProviderDef`` record.
Everything else (catalog for the API, default base URLs, strategy keys,
capability matrix, protocol classification, endpoint/header resolution) is
*derived* from this list -- no more 5-place duplication.

Layering: this module depends only on the stdlib. Both the API routes
(``app.api.routes.llm_config``) and the service layer
(``app.services.llm_service``) consume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelDef:
    """A model offered by a provider (catalog metadata only)."""

    id: str
    name: str
    context_length: int
    supports_chinese: bool
    supports_vision: bool
    supports_tools: bool
    description: str | None = None


@dataclass
class CapabilityDef:
    """Provider capability matrix entry."""

    json_mode: bool = True
    reasoning: bool = False
    tool_call: bool = False
    max_context: int = 128000


# Chat protocol drives endpoint shape + auth header style.
#   openai    -> {base}/chat/completions, Authorization: Bearer
#   anthropic -> {base}/messages,          x-api-key + anthropic-version
#   azure     -> {base}/chat/completions,  api-key
#   bedrock   -> {base}/model/{model}/converse
PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_AZURE = "azure"
PROTOCOL_BEDROCK = "bedrock"

# Strategy key resolves to a ProviderStrategy class in llm_service.
STRATEGY_OPENAI = "openai"
STRATEGY_ANTHROPIC = "anthropic"
STRATEGY_KIMI_K2 = "kimi_k2"
STRATEGY_DEEPSEEK_V3 = "deepseek_v3"
STRATEGY_DEEPSEEK_R1 = "deepseek_r1"
STRATEGY_DEFAULT = "default"

CATEGORY_MAJOR = "major"
CATEGORY_MARKETPLACE = "marketplace"
CATEGORY_LOCAL = "local"


@dataclass
class ProviderDef:
    """One provider. The single declarative unit of the registry."""

    id: str
    name: str
    logo_svg: str
    description: str
    # Runtime default base URL (always set for real providers; supersedes the
    # old ``_DEFAULT_BASE_URLS`` dict).
    base_url: str | None = None
    # Catalog/UI default base URL (returned by /providers). ``None`` for major
    # providers that "don't need a custom base"; equals ``base_url`` otherwise.
    default_base_url: str | None = None
    protocol: str = PROTOCOL_OPENAI
    strategy_key: str = STRATEGY_OPENAI
    capability: CapabilityDef = field(default_factory=CapabilityDef)
    models: list[ModelDef] = field(default_factory=list)
    requires_api_key: bool = True
    supports_custom_base: bool = False
    category: str = CATEGORY_MAJOR
    # ---- live model-list fetching (/models endpoint) ----
    fetch_models: bool = True
    # Override the models-fetch auth derived from ``protocol``.
    # ``None`` -> derive (anthropic=x-api-key, azure=azure, else bearer).
    models_auth: str | None = None
    # If True, /models is only reachable when the user set a custom base.
    models_require_custom_base: bool = False
    # Extra headers added when *listing models* or *testing* (not normal chat).
    # e.g. OpenRouter wants HTTP-Referer / X-Title.
    listing_headers: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Reusable model definitions
# ---------------------------------------------------------------------------

GPT41 = ModelDef("gpt-4.1", "GPT-4.1", 1047576, True, True, True, "最新旗舰，1M 上下文")
GPT41_MINI = ModelDef("gpt-4.1-mini", "GPT-4.1 Mini", 1047576, True, True, True, "高性价比 1M 上下文")
GPT41_NANO = ModelDef("gpt-4.1-nano", "GPT-4.1 Nano", 1047576, True, True, True, "极速轻量")
GPT4O_FULL = ModelDef("gpt-4o", "GPT-4o", 128000, True, True, True, "上一代旗舰多模态")
GPT4O_MINI = ModelDef("gpt-4o-mini", "GPT-4o Mini", 128000, True, True, True, "高性价比")
O3 = ModelDef("o3", "o3", 200000, True, True, True, "推理模型，强分析")
O4_MINI = ModelDef("o4-mini", "o4-mini", 200000, True, True, True, "轻量推理模型")

CLAUDE_SONNET_4 = ModelDef("claude-sonnet-4-20250514", "Claude Sonnet 4", 200000, True, True, True, "最新旗舰，综合最佳")
CLAUDE_37_SONNET = ModelDef("claude-3-7-sonnet-20250219", "Claude 3.7 Sonnet", 200000, True, True, True, "支持扩展思考")
CLAUDE_35_SONNET = ModelDef("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", 200000, True, True, True, "上一代旗舰")
CLAUDE_35_HAIKU = ModelDef("claude-3-5-haiku-20241022", "Claude 3.5 Haiku", 200000, True, False, True, "极速响应")

DEEPSEEK_CHAT = ModelDef("deepseek-chat", "DeepSeek-V3", 128000, True, False, True, "通用对话，性价比高")
DEEPSEEK_REASONER = ModelDef("deepseek-reasoner", "DeepSeek-R1", 128000, True, False, True, "推理模型，适合复杂分析")

KIMI_K26 = ModelDef("kimi-k2.6", "Kimi K2.6", 256000, True, True, True, "当前旗舰模型，多模态，支持推理")
KIMI_K25 = ModelDef("kimi-k2.5", "Kimi K2.5", 256000, True, True, True, "上一代旗舰模型")
KIMI_FOR_CODING = ModelDef("kimi-for-coding", "Kimi for Coding", 262144, True, False, True, "编程专用模型，支持长上下文和工具调用")

GLM4_PLUS = ModelDef("glm-4-plus", "GLM-4-Plus", 128000, True, True, True, "旗舰模型")
GLM4_AIR = ModelDef("glm-4-air", "GLM-4-Air", 128000, True, False, True, "高性价比")

QWEN3_235B = ModelDef("qwen3-235b-a22b", "Qwen3-235B-A22B", 131072, True, True, True, "MoE 旗舰模型，235B 总参/22B 激活")
QWEN_MAX = ModelDef("qwen-max", "Qwen-Max", 32768, True, True, True, "Qwen 2.5 旗舰")
QWEN_MAX_LATEST = ModelDef("qwen-max-latest", "Qwen-Max-Latest", 131072, True, True, True, "最新 Qwen-Max 版本")
QWEN_PLUS = ModelDef("qwen-plus", "Qwen-Plus", 131072, True, False, True, "均衡选择")
QWEN_TURBO = ModelDef("qwen-turbo", "Qwen-Turbo", 131072, True, False, True, "极速响应，低成本")

GEMINI_25_PRO = ModelDef("gemini-2.5-pro", "Gemini 2.5 Pro", 1000000, True, True, True, "思考模型，复杂任务最强")
GEMINI_25_FLASH = ModelDef("gemini-2.5-flash", "Gemini 2.5 Flash", 1000000, True, True, True, "1M 上下文，极速多模态")
GEMINI_20_FLASH = ModelDef("gemini-2.0-flash", "Gemini 2.0 Flash", 1048576, True, True, True, "高性价比 1M 上下文")

GPT4O_AZURE = ModelDef("gpt-4o", "GPT-4o (Azure)", 128000, True, True, True)
GPT4_AZURE = ModelDef("gpt-4", "GPT-4 (Azure)", 128000, True, False, True)

ASTRON_CODE = ModelDef("astron-code-latest", "Astron Code", 98304, True, False, True, "统一模型名，后台可切换 DeepSeek-V3.2/GLM-5 等底层模型")
BAILIAN_CODE = ModelDef("bailian-code-latest", "Bailian Code", 128000, True, False, True, "编程专用模型")
LOCAL_MODEL = ModelDef("local-model", "自定义模型", 32768, True, False, False, "请填写自定义模型 ID")

MIMO_V25_PRO = ModelDef("mimo-v2.5-pro", "Mimo v2.5 Pro", 200000, True, False, True, "推理旗舰，擅长数学/代码/逻辑")
MIMO_V25_PRO_1M = ModelDef("mimo-v2.5-pro[1m]", "Mimo v2.5 Pro (1M)", 1000000, True, False, True, "百万级长上下文版本")

STEP_2_16K = ModelDef("step-2-16k", "Step 2 16K", 16000, True, False, True, "快速响应")
STEP_1_256K = ModelDef("step-1-256k", "Step 1 256K", 256000, True, True, True, "超长上下文")
STEP_1O_MEDIUM = ModelDef("step-1o-medium", "Step 1o Medium", 128000, True, True, True, "均衡模型")
STEP_1O_MINI = ModelDef("step-1o-mini", "Step 1o Mini", 128000, True, True, True, "轻量模型")

ERNIE_40 = ModelDef("ernie-4.0", "ERNIE 4.0", 8192, True, False, True, "旗舰模型")
ERNIE_35_8K = ModelDef("ernie-3.5-8k", "ERNIE 3.5 8K", 8192, True, False, True, "高性价比")

SF_QWEN25_72B = ModelDef("Qwen/Qwen2.5-72B-Instruct", "Qwen2.5 72B", 128000, True, False, True)
SF_DEEPSEEK_V3 = ModelDef("deepseek-ai/DeepSeek-V3", "DeepSeek V3", 128000, True, False, True)
SF_LLAMA33_70B = ModelDef("meta-llama/Llama-3.3-70B-Instruct", "Llama 3.3 70B", 128000, False, False, True)

MINIMAX_M1_80K = ModelDef("minimax/MiniMax-M1-80k", "MiniMax M1 80k", 8192, True, False, True)
MINIMAX_M1_80K_EN = ModelDef("minimax/MiniMax-M1-80k", "MiniMax M1 80k", 8192, False, False, True)

NOVITA_LLAMA33 = ModelDef("meta-llama/Llama-3.3-70B-Instruct", "Llama 3.3 70B", 128000, True, False, True)
NVIDIA_LLAMA33 = ModelDef("meta/llama-3.3-70b-instruct", "Llama 3.3 70B", 128000, True, False, True)
NVIDIA_NEMOTRON = ModelDef("nvidia/llama-3.1-nemotron-70b-instruct", "Nemotron 70B", 128000, True, False, True)
MODELSCOPE_QWEN = ModelDef("Qwen/Qwen2.5-72B-Instruct", "Qwen2.5 72B", 128000, True, False, True)

DOUBAO_PRO_32K = ModelDef("doubao-pro-32k", "Doubao Pro 32K", 128000, True, False, True, "字节旗舰模型")
DOUBAO_SEED_18 = ModelDef("doubao-seed-1.8", "Doubao Seed 1.8", 128000, True, False, True, "推理模型")

COPILOT_GPT4O = ModelDef("gpt-4o", "GPT-4o", 128000, True, True, True)
COPILOT_O3 = ModelDef("o3", "o3", 200000, True, True, True)

OR_CLAUDE_SONNET_4 = ModelDef("anthropic/claude-sonnet-4", "Claude Sonnet 4", 200000, True, True, True, "Anthropic 最新旗舰")
OR_GPT41 = ModelDef("openai/gpt-4.1", "GPT-4.1", 1047576, True, True, True, "OpenAI 最新 1M 旗舰")
OR_GEMINI_25_PRO = ModelDef("google/gemini-2.5-pro", "Gemini 2.5 Pro", 1000000, True, True, True, "Google 思考模型")
OR_DEEPSEEK_R1 = ModelDef("deepseek/deepseek-r1", "DeepSeek R1", 128000, True, False, True, "DeepSeek 推理模型")
OR_LLAMA33_70B = ModelDef("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B", 128000, True, False, True, "Meta 开源旗舰")

AWS_CLAUDE_35 = ModelDef("anthropic.claude-3-5-sonnet-20241022-v1:0", "Claude 3.5 Sonnet", 200000, True, True, True)

# Generic marketplace model (no description).
GPT4O = ModelDef("gpt-4o", "GPT-4o", 128000, True, True, True)
CLAUDE_35_SONNET_PLAIN = ModelDef("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet", 200000, True, True, True)


# ---------------------------------------------------------------------------
# Reusable logo SVGs
# ---------------------------------------------------------------------------

LOGO_OPENAI = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M22.282 9.821a5.985 5.985 0 0 0-.516-4.91 6.046 6.046 0 0 0-6.51-2.9A6.065 6.065 0 0 0 4.981 4.18a5.985 5.985 0 0 0-3.998 2.9 6.046 6.046 0 0 0 .743 7.097 5.98 5.98 0 0 0 .51 4.911 6.051 6.051 0 0 0 6.515 2.9A5.985 5.985 0 0 0 13.26 24a6.056 6.056 0 0 0 5.772-4.206 5.99 5.99 0 0 0 3.997-2.9 6.056 6.056 0 0 0-.747-7.073zM13.26 22.43a4.476 4.476 0 0 1-2.876-1.04l.141-.081 4.779-2.758a.795.795 0 0 0 .392-.681v-6.737l2.02 1.168a.071.071 0 0 1 .038.052v5.583a4.504 4.504 0 0 1-4.494 4.494zM3.6 18.304a4.47 4.47 0 0 1-.535-3.014l.142.085 4.783 2.759a.771.771 0 0 0 .78 0l5.843-3.369v2.332a.08.08 0 0 1-.033.062L9.74 19.95a4.5 4.5 0 0 1-6.14-1.646zM2.34 7.896a4.485 4.485 0 0 1 2.366-1.973V11.6a.766.766 0 0 0 .388.676l5.815 3.355-2.02 1.168a.076.076 0 0 1-.071 0l-4.83-2.786A4.504 4.504 0 0 1 2.34 7.896zm16.597 3.855l-5.833-3.387L15.119 7.2a.076.076 0 0 1 .071 0l4.83 2.791a4.494 4.494 0 0 1-.676 8.105v-5.678a.79.79 0 0 0-.407-.667zm2.01-3.023l-.141-.085-4.774-2.782a.776.776 0 0 0-.785 0L9.409 9.23V6.897a.066.066 0 0 1 .028-.061l4.83-2.787a4.5 4.5 0 0 1 6.68 4.66zm-12.64 4.135l-2.02-1.164a.08.08 0 0 1-.038-.057V6.075a4.5 4.5 0 0 1 7.375-3.453l-.142.08L8.704 5.46a.795.795 0 0 0-.393.681zm1.097-2.365l2.602-1.5 2.607-1.5v2.999l-2.597 1.5-2.607-1.5z'/></svg>"
LOGO_ANTHROPIC = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M17.304 3.541h-3.672l6.696 16.918h3.672zm-10.608 0L0 20.459h3.744l1.368-3.6h6.672l1.368 3.6h3.744L9.696 3.541zm-.264 10.656L7.2 8.893l2.832 5.304z'/></svg>"
LOGO_AZURE = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M5.483 21.3H24L14.025 4.013l-3.038 8.347 5.836 6.938L5.483 21.3zM13.23 2.7L6.105 8.677 0 19.253h5.505l7.961-13.518-.237-.036z'/></svg>"
LOGO_DEEPSEEK = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5'/></svg>"
LOGO_KIMI = "<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='10'/></svg>"
LOGO_ZHIPU = "<svg viewBox='0 0 24 24' fill='currentColor'><rect x='4' y='4' width='16' height='16' rx='2'/></svg>"
LOGO_QWEN = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 15h-2v-2h2v2zm0-4h-2V7h2v6zm4 4h-2v-2h2v2zm0-4h-2V7h2v6z'/></svg>"
LOGO_KIMI_CODING = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/></svg>"
LOGO_ASTRON = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2l2.4 7.2h7.6l-6 4.8 2.4 7.2-6-4.8-6 4.8 2.4-7.2-6-4.8h7.6z'/></svg>"
LOGO_GOOGLE = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8z'/><path d='M12 7v5l4.3 2.5.8-1.3-3.5-2V7z'/></svg>"
LOGO_OPENROUTER = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2a7.2 7.2 0 0 1-6-3.22c.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08a7.2 7.2 0 0 1-6 3.22z'/></svg>"
LOGO_MIMO = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm4.5 14h-2l-2.5-5-2.5 5h-2L12 7z'/></svg>"
LOGO_LOCAL = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M20 3H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h7v2H8v2h8v-2h-3v-2h7a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2zm0 12H4V5h16z'/></svg>"
LOGO_STEPFUN = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2l3 7h7l-5.5 4 2 7L12 16l-6.5 4 2-7L2 9h7z'/></svg>"
LOGO_SILICONFLOW = "<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='3'/><path d='M12 2v4m0 12v4m10-10h-4M6 12H2m15.5-6.5l-2.8 2.8M9.3 14.7l-2.8 2.8m12.6 0l-2.8-2.8M9.3 9.3L6.5 6.5'/></svg>"
LOGO_MINIMAX = "<svg viewBox='0 0 24 24' fill='currentColor'><rect x='3' y='3' width='18' height='18' rx='3'/></svg>"
LOGO_NOVITA = LOGO_STEPFUN
LOGO_CUBE = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2L2 7v10l10 5 10-5V7L12 2zm0 2.5L19 8l-7 3.5L5 8l7-3.5zM4 9.5l7 3.5v7l-7-3.5v-7zm16 0v7l-7 3.5v-7l7-3.5z'/></svg>"
LOGO_NVIDIA = LOGO_CUBE
LOGO_MODELSCOPE = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2L2 19h20L12 2z'/></svg>"
LOGO_VOLCANO = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2c5.5 0 10 4.5 10 10s-4.5 10-10 10S2 17.5 2 12 6.5 2 12 2zm0 2c-4.4 0-8 3.6-8 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8z'/></svg>"
LOGO_COPILOT = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.477 2 2 6.477 2 12c0 4.42 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.008-.866-.013-1.7-2.782.604-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.462-1.11-1.462-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.092-.646.35-1.086.636-1.336-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0112 6.836c.85.004 1.705.114 2.504.336 1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.578.688.48C19.138 20.16 22 16.416 22 12c0-5.523-4.477-10-10-10z'/></svg>"

# Generic marketplace logos.
LOGO_MK_RECT_R4 = "<svg viewBox='0 0 24 24' fill='currentColor'><rect x='4' y='4' width='16' height='16' rx='4'/></svg>"
LOGO_MK_RECT_R2 = "<svg viewBox='0 0 24 24' fill='currentColor'><rect x='3' y='3' width='18' height='18' rx='2'/></svg>"
LOGO_MK_CIRCLE_R10 = "<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='10'/></svg>"
LOGO_MK_CIRCLE_R8 = "<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='8'/></svg>"
LOGO_MK_CIRCLE_OUTLINE = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z'/></svg>"
LOGO_MK_LAYERS = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12 2L2 7l10 5 10-5-10-5z'/></svg>"
LOGO_MK_DOUBLE_CHEVRON = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0L10 12l4.6-4.6L13.2 6l-6 6 6 6 1.4-1.4z'/></svg>"
LOGO_MK_KEY = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M12.65 10C11.83 7.67 9.61 6 7 6c-3.31 0-6 2.69-6 6s2.69 6 6 6c2.61 0 4.83-1.67 5.65-4H17v4h4v-4h2v-4H12.65zM7 14c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z'/></svg>"
LOGO_MK_CLOUD = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z'/></svg>"
LOGO_MK_CIRCLE_CROSS = "<svg viewBox='0 0 24 24' fill='currentColor'><circle cx='12' cy='12' r='2'/><path d='M12 2v4m0 12v4m10-10h-4M6 12H2'/></svg>"
LOGO_MK_CIRCLE_ARROW = "<svg viewBox='0 0 24 24' fill='currentColor'><path d='M13 2.05v2.02c3.95.49 7 3.85 7 7.93 0 4.42-3.58 8-8 8s-8-3.58-8-8c0-4.08 3.05-7.44 7-7.93V2.05C6.27 2.56 2 7.25 2 12c0 5.52 4.48 10 10 10s10-4.48 10-10c0-4.75-4.27-9.44-7-9.95z'/></svg>"

OPENROUTER_LISTING_HEADERS = {"HTTP-Referer": "https://paperforge.local", "X-Title": "PaperForge"}


# ---------------------------------------------------------------------------
# Major / special providers (explicit)
# ---------------------------------------------------------------------------

_PROVIDERS: list[ProviderDef] = [
    ProviderDef(
        id="openai", name="OpenAI", logo_svg=LOGO_OPENAI,
        description="OpenAI GPT 系列模型，支持函数调用和视觉理解。",
        base_url="https://api.openai.com/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[GPT41, GPT41_MINI, GPT41_NANO, GPT4O_FULL, GPT4O_MINI, O3, O4_MINI],
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="anthropic", name="Anthropic", logo_svg=LOGO_ANTHROPIC,
        description="Anthropic Claude 系列，以长上下文和安全性著称。",
        base_url="https://api.anthropic.com/v1", default_base_url=None,
        protocol=PROTOCOL_ANTHROPIC, strategy_key=STRATEGY_ANTHROPIC,
        capability=CapabilityDef(True, True, True, 200000),
        models=[CLAUDE_SONNET_4, CLAUDE_37_SONNET, CLAUDE_35_SONNET, CLAUDE_35_HAIKU],
        models_auth="x-api-key", category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="azure_openai", name="Azure OpenAI", logo_svg=LOGO_AZURE,
        description="Microsoft Azure 托管的 OpenAI 服务，适合企业部署。",
        base_url="https://your-resource.openai.azure.com/",
        default_base_url="https://your-resource.openai.azure.com/",
        protocol=PROTOCOL_AZURE, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[GPT4O_AZURE, GPT4_AZURE], models_auth="azure",
        supports_custom_base=True, category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="deepseek", name="DeepSeek", logo_svg=LOGO_DEEPSEEK,
        description="DeepSeek 大模型，推理能力强，性价比高。",
        base_url="https://api.deepseek.com/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_DEEPSEEK_V3,
        capability=CapabilityDef(True, True, True, 128000),
        models=[DEEPSEEK_CHAT, DEEPSEEK_REASONER], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="kimi", name="月之暗面 Kimi", logo_svg=LOGO_KIMI,
        description="Kimi 大模型，支持超长上下文。",
        base_url="https://api.moonshot.cn/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_KIMI_K2,
        capability=CapabilityDef(True, True, True, 256000),
        models=[KIMI_K26, KIMI_K25], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="zhipu", name="智谱 GLM", logo_svg=LOGO_ZHIPU,
        description="智谱 AI GLM 系列大模型。",
        base_url="https://open.bigmodel.cn/api/paas/v4", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 128000),
        models=[GLM4_PLUS, GLM4_AIR], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="qwen", name="通义千问", logo_svg=LOGO_QWEN,
        description="阿里云通义千问大模型。",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 131072),
        models=[QWEN3_235B, QWEN_MAX, QWEN_MAX_LATEST, QWEN_PLUS, QWEN_TURBO],
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="kimi-coding", name="Kimi Coding", logo_svg=LOGO_KIMI_CODING,
        description="Kimi 编程专用计划，针对代码生成和 Agent 场景优化。",
        base_url="https://api.kimi.com/coding/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_KIMI_K2,
        capability=CapabilityDef(True, False, True, 262144),
        models=[KIMI_FOR_CODING], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="astron-coding", name="讯飞星辰 Coding", logo_svg=LOGO_ASTRON,
        description="讯飞星辰 MaaS Coding Plan，按月订阅的 AI 编码服务，底层可切换多款旗舰模型。",
        base_url="https://maas-coding-api.cn-huabei-1.xf-yun.com/v2", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 98304),
        models=[ASTRON_CODE], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="google", name="Google Gemini", logo_svg=LOGO_GOOGLE,
        description="Google Gemini 系列模型，超长上下文、多模态能力强。",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 1000000),
        models=[GEMINI_25_PRO, GEMINI_25_FLASH, GEMINI_20_FLASH], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="openrouter", name="OpenRouter", logo_svg=LOGO_OPENROUTER,
        description="OpenRouter 统一 API，一键访问 GPT、Claude、Gemini、Llama 等数百款模型。",
        base_url="https://openrouter.ai/api/v1", default_base_url=None,
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 200000),
        models=[OR_CLAUDE_SONNET_4, OR_GPT41, OR_GEMINI_25_PRO, OR_DEEPSEEK_R1, OR_LLAMA33_70B],
        supports_custom_base=True, listing_headers=OPENROUTER_LISTING_HEADERS,
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="xiaomi-mimo", name="小米 Mimo", logo_svg=LOGO_MIMO,
        description="小米 Mimo 推理模型，采用 Anthropic 兼容协议，擅长复杂推理、代码和数学任务。支持订阅套餐（tp-xxx）与按量付费（sk-xxx）。",
        base_url="https://token-plan-cn.xiaomimimo.com/anthropic/v1", default_base_url=None,
        protocol=PROTOCOL_ANTHROPIC, strategy_key=STRATEGY_ANTHROPIC,
        capability=CapabilityDef(True, True, True, 1000000),
        models=[MIMO_V25_PRO, MIMO_V25_PRO_1M], models_auth="x-api-key",
        supports_custom_base=True, models_require_custom_base=True,
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="local", name="本地 / 自定义", logo_svg=LOGO_LOCAL,
        description="兼容 OpenAI API 格式的本地模型或第三方服务。",
        base_url="http://localhost:8000/v1", default_base_url="http://localhost:8000/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, False, 32768),
        models=[LOCAL_MODEL], requires_api_key=False, supports_custom_base=True,
        category=CATEGORY_LOCAL,
    ),
    ProviderDef(
        id="claude-cn", name="Claude CN", logo_svg=LOGO_ANTHROPIC,
        description="Claude 国内代理，Anthropic 兼容协议。",
        base_url="https://claude-cn.com/anthropic/v1", default_base_url="https://claude-cn.com/anthropic/v1",
        protocol=PROTOCOL_ANTHROPIC, strategy_key=STRATEGY_ANTHROPIC,
        capability=CapabilityDef(True, True, True, 200000),
        models=[CLAUDE_SONNET_4, CLAUDE_37_SONNET], models_auth="bearer",
        supports_custom_base=True, category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="claudeapi", name="Claude API 国内代理", logo_svg=LOGO_ANTHROPIC,
        description="另一家 Claude 国内代理，Anthropic 兼容协议。",
        base_url="https://claude-api.com/v1", default_base_url="https://claude-api.com/v1",
        protocol=PROTOCOL_ANTHROPIC, strategy_key=STRATEGY_ANTHROPIC,
        capability=CapabilityDef(True, True, True, 200000),
        models=[CLAUDE_SONNET_4, CLAUDE_35_SONNET], models_auth="bearer",
        supports_custom_base=True, category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="stepfun", name="阶跃星辰 Step", logo_svg=LOGO_STEPFUN,
        description="阶跃星辰 Step 系列模型，支持推理和多模态。",
        base_url="https://api.stepfun.com/v1", default_base_url="https://api.stepfun.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_KIMI_K2,
        capability=CapabilityDef(True, True, True, 128000),
        models=[STEP_2_16K, STEP_1_256K, STEP_1O_MEDIUM, STEP_1O_MINI],
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="stepfun-en", name="StepFun English", logo_svg=LOGO_STEPFUN,
        description="阶跃星辰 Step 英文优化版。",
        base_url="https://api.stepfun.com/v1", default_base_url="https://api.stepfun.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_KIMI_K2,
        capability=CapabilityDef(True, True, True, 128000),
        models=[STEP_2_16K, STEP_1_256K], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="baidu-qianfan", name="百度千帆", logo_svg=LOGO_QWEN,
        description="百度千帆大模型平台。",
        base_url="https://qianfan.baidubce.com/v2", default_base_url="https://qianfan.baidubce.com/v2",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 8192),
        models=[ERNIE_40, ERNIE_35_8K], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="siliconflow", name="硅基流动", logo_svg=LOGO_SILICONFLOW,
        description="硅基流动 SiliconFlow，一站式开源大模型 API 平台。",
        base_url="https://api.siliconflow.cn/v1", default_base_url="https://api.siliconflow.cn/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[SF_QWEN25_72B, SF_DEEPSEEK_V3], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="siliconflow-en", name="SiliconFlow EN", logo_svg=LOGO_SILICONFLOW,
        description="SiliconFlow 英文优化版。",
        base_url="https://api.siliconflow.cn/v1", default_base_url="https://api.siliconflow.cn/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[SF_LLAMA33_70B], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="minimax", name="MiniMax", logo_svg=LOGO_MINIMAX,
        description="MiniMax 大模型平台。",
        base_url="https://api.minimaxi.com/v1", default_base_url="https://api.minimaxi.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 8192),
        models=[MINIMAX_M1_80K], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="minimax-en", name="MiniMax EN", logo_svg=LOGO_MINIMAX,
        description="MiniMax 英文优化版。",
        base_url="https://api.minimaxi.com/v1", default_base_url="https://api.minimaxi.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 8192),
        models=[MINIMAX_M1_80K_EN], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="novita-ai", name="Novita AI", logo_svg=LOGO_NOVITA,
        description="Novita AI 开源模型平台。",
        base_url="https://api.novita.ai/v1", default_base_url="https://api.novita.ai/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[NOVITA_LLAMA33], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="nvidia", name="Nvidia NIM", logo_svg=LOGO_NVIDIA,
        description="Nvidia NIM 推理平台，支持 Llama、Mistral 等模型。",
        base_url="https://integrate.api.nvidia.com/v1", default_base_url="https://integrate.api.nvidia.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[NVIDIA_LLAMA33, NVIDIA_NEMOTRON], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="modelscope", name="ModelScope 魔搭", logo_svg=LOGO_MODELSCOPE,
        description="ModelScope 魔搭模型平台。",
        base_url="https://api-inference.modelscope.cn/v1", default_base_url="https://api-inference.modelscope.cn/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 128000),
        models=[MODELSCOPE_QWEN], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="volcano-engine", name="火山引擎", logo_svg=LOGO_VOLCANO,
        description="字节跳动火山引擎方舟平台。",
        base_url="https://ark.cn-beijing.volces.com/api/v3", default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 128000),
        models=[DOUBAO_PRO_32K], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="doubaoseed", name="豆包 Seed", logo_svg=LOGO_VOLCANO,
        description="字节跳动豆包 Seed 系列模型。",
        base_url="https://ark.cn-beijing.volces.com/api/v3", default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 128000),
        models=[DOUBAO_SEED_18], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="github-copilot", name="GitHub Copilot", logo_svg=LOGO_COPILOT,
        description="GitHub Copilot 模型 API。",
        base_url="https://api.githubcopilot.com/v1", default_base_url="https://api.githubcopilot.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 128000),
        models=[COPILOT_GPT4O, COPILOT_O3], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="gemini-native", name="Gemini Native", logo_svg=LOGO_GOOGLE,
        description="Google Gemini 原生 OpenAI 兼容接口。",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 1000000),
        models=[GEMINI_25_PRO, GEMINI_25_FLASH], category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="aws-bedrock", name="AWS Bedrock", logo_svg=LOGO_CUBE,
        description="AWS Bedrock 托管的 Claude、Llama 等模型。",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        default_base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        protocol=PROTOCOL_BEDROCK, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, True, True, 200000),
        models=[AWS_CLAUDE_35], supports_custom_base=True, fetch_models=False,
        category=CATEGORY_MAJOR,
    ),
    ProviderDef(
        id="bailian-code", name="阿里百炼 Code", logo_svg=LOGO_STEPFUN,
        description="阿里云百炼平台编程专用模型。",
        base_url="https://bailian-for-code.aliyuncs.com/v1",
        default_base_url="https://bailian-for-code.aliyuncs.com/v1",
        protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
        capability=CapabilityDef(True, False, True, 128000),
        models=[BAILIAN_CODE], category=CATEGORY_MAJOR,
    ),
]


# ---------------------------------------------------------------------------
# Generic OpenAI-compatible marketplaces (compact table -> ProviderDef)
# ---------------------------------------------------------------------------

# (id, name, base_url, models, reasoning, logo_svg, description)
_MARKETPLACE_DEFS: list[tuple[str, str, str, list[ModelDef], bool, str, str]] = [
    ("dmxapi", "DMX API", "https://www.dmxapi.com/v1", [GPT4O, CLAUDE_35_SONNET_PLAIN], True, LOGO_MK_RECT_R4, "DMX API 国内中转，OpenAI 兼容。"),
    ("aihubmix", "AI Hub Mix", "https://aihubmix.com/v1", [GPT4O, CLAUDE_35_SONNET_PLAIN], True, LOGO_MK_CIRCLE_R10, "AI Hub Mix 中转平台。"),
    ("cherryin", "CherryIn", "https://cherryai.cn/v1", [GPT4O], False, LOGO_MK_CIRCLE_R8, "CherryIn 中转平台。"),
    ("longcat", "LongCat", "https://longcat.ai/v1", [GPT4O], False, LOGO_QWEN, "LongCat AI 中转平台。"),
    ("kat-coder", "Kat Coder", "https://katcoder.com/v1", [GPT4O], False, LOGO_MK_LAYERS, "Kat Coder 编程专用中转。"),
    ("apikeyfun", "API Key Fun", "https://apikey.fun/v1", [GPT4O], True, LOGO_MK_KEY, "API Key Fun 中转平台。"),
    ("apinebula", "API Nebula", "https://apinebula.com/v1", [GPT4O], True, LOGO_MK_CIRCLE_R10, "API Nebula 中转平台。"),
    ("atlascloud", "AtlasCloud", "https://atlascloud.cn/v1", [GPT4O], True, LOGO_MK_CLOUD, "AtlasCloud 中转平台。"),
    ("sudocode", "SudoCode", "https://sudocode.cn/v1", [GPT4O], False, LOGO_MK_DOUBLE_CHEVRON, "SudoCode 编程专用中转。"),
    ("runapi", "RunAPI", "https://runapi.com/v1", [GPT4O], True, LOGO_MK_CIRCLE_ARROW, "RunAPI 中转平台。"),
    ("relaxycode", "RelaxyCode", "https://relaxycode.com/v1", [GPT4O], False, LOGO_MK_CIRCLE_OUTLINE, "RelaxyCode 编程中转。"),
    ("cubence", "Cubence", "https://cubence.com/v1", [GPT4O], False, LOGO_MK_RECT_R2, "Cubence 中转平台。"),
    ("aigocode", "AIGO Code", "https://aigocode.com/v1", [GPT4O], False, LOGO_MK_DOUBLE_CHEVRON, "AIGO Code 编程中转。"),
    ("rightcode", "RightCode", "https://rightcode.com/v1", [GPT4O], False, LOGO_MK_LAYERS, "RightCode 编程中转。"),
    ("aicodemirror", "AI Code Mirror", "https://aicodemirror.com/v1", [GPT4O], False, LOGO_MK_RECT_R2, "AI Code Mirror 中转。"),
    ("crazyrouter", "CrazyRouter", "https://crazyrouter.com/v1", [GPT4O], True, LOGO_MK_CIRCLE_CROSS, "CrazyRouter 中转。"),
    ("sssaicode", "SSS AI Code", "https://sssaicode.com/v1", [GPT4O], False, LOGO_MK_LAYERS, "SSS AI Code 中转。"),
    ("youyuncn", "优云知数", "https://uyunzhishui.com/v1", [GPT4O], False, LOGO_MK_CLOUD, "优云知数中转平台。"),
    ("micu", "MiCu", "https://micu.ai/v1", [GPT4O], False, LOGO_MK_CIRCLE_R10, "MiCu 中转平台。"),
    ("ctok", "CTOK", "https://ctok.ai/v1", [GPT4O], False, LOGO_MK_CIRCLE_OUTLINE, "CTOK 中转平台。"),
    ("eflowcode", "EFlow Code", "https://eflowcode.com/v1", [GPT4O], False, LOGO_MK_DOUBLE_CHEVRON, "EFlow Code 编程中转。"),
    ("therouter", "The Router", "https://therouter.com/v1", [GPT4O], False, LOGO_MK_CIRCLE_CROSS, "The Router 中转平台。"),
    ("pipelm", "PipeLM", "https://pipelm.com/v1", [GPT4O], False, LOGO_MK_LAYERS, "PipeLM 中转平台。"),
    ("ccsub", "CCSub", "https://ccsub.com/v1", [GPT4O], False, LOGO_MK_RECT_R4, "CCSub 中转平台。"),
    ("unity2ai", "Unity2AI", "https://unity2.ai/v1", [GPT4O], False, LOGO_MK_CIRCLE_R10, "Unity2AI 中转平台。"),
    ("patewayai", "Pateway AI", "https://patewayai.com/v1", [GPT4O], False, LOGO_QWEN, "Pateway AI 中转平台。"),
    ("opencode-go", "OpenCode", "https://opencode.ai/v1", [GPT4O], False, LOGO_MK_DOUBLE_CHEVRON, "OpenCode 编程中转。"),
    ("packycode", "PackyCode", "https://packycode.com/v1", [GPT4O], False, LOGO_MK_LAYERS, "PackyCode 编程中转。"),
]

for _id, _name, _base, _models, _reasoning, _logo, _desc in _MARKETPLACE_DEFS:
    _PROVIDERS.append(
        ProviderDef(
            id=_id, name=_name, logo_svg=_logo, description=_desc,
            base_url=_base, default_base_url=_base,
            protocol=PROTOCOL_OPENAI, strategy_key=STRATEGY_OPENAI,
            capability=CapabilityDef(True, _reasoning, True, 128000),
            models=_models, category=CATEGORY_MARKETPLACE,
        )
    )


# ---------------------------------------------------------------------------
# Derived views (single source -> many read-only projections)
# ---------------------------------------------------------------------------

PROVIDER_MAP: dict[str, ProviderDef] = {p.id: p for p in _PROVIDERS}
PROVIDER_LIST: list[ProviderDef] = list(_PROVIDERS)
PROVIDER_IDS: set[str] = set(PROVIDER_MAP)

#: Runtime default base URLs (replaces the old ``_DEFAULT_BASE_URLS`` dict).
DEFAULT_BASE_URLS: dict[str, str] = {p.id: p.base_url for p in _PROVIDERS if p.base_url}

#: Provider -> strategy key (replaces the old ``_STRATEGIES`` mapping).
STRATEGY_KEYS: dict[str, str] = {p.id: p.strategy_key for p in _PROVIDERS}

#: Provider -> capability (replaces the old ``_CAPABILITIES`` dict).
CAPABILITY_MAP: dict[str, CapabilityDef] = {p.id: p.capability for p in _PROVIDERS}

#: Anthropic-protocol chat providers (replaces ``_ANTHROPIC_*`` sets).
ANTHROPIC_PROTOCOL_IDS: set[str] = {p.id for p in _PROVIDERS if p.protocol == PROTOCOL_ANTHROPIC}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_provider(provider_id: str) -> ProviderDef | None:
    return PROVIDER_MAP.get(provider_id)


def provider_or_default(provider_id: str) -> ProviderDef:
    """Return the provider def, falling back to a generic OpenAI def."""
    return PROVIDER_MAP.get(provider_id) or ProviderDef(
        id=provider_id, name=provider_id, logo_svg="", description="",
        base_url=None, strategy_key=STRATEGY_DEFAULT,
    )


def _default_models_auth(protocol: str) -> str:
    if protocol == PROTOCOL_ANTHROPIC:
        return "x-api-key"
    if protocol == PROTOCOL_AZURE:
        return "azure"
    return "bearer"


def models_auth_for(p: ProviderDef) -> str:
    return p.models_auth or _default_models_auth(p.protocol)


# ---------------------------------------------------------------------------
# Endpoint / header resolution (shared by routes + service)
# ---------------------------------------------------------------------------

def chat_endpoint(p: ProviderDef, api_base: str | None, model: str = "") -> str:
    """Resolve the chat-completion URL for a provider + optional custom base."""
    base = (api_base or p.base_url or "").rstrip("/")
    if p.protocol == PROTOCOL_BEDROCK:
        return f"{base}/model/{model}/converse"
    if p.protocol == PROTOCOL_ANTHROPIC:
        return f"{base or 'https://api.anthropic.com/v1'}/messages"
    # openai / azure
    return f"{base}/chat/completions"


def chat_headers(p: ProviderDef, api_key: str | None, extra: dict | None = None) -> dict[str, str]:
    """Headers for a normal chat completion (no listing_headers)."""
    if p.protocol == PROTOCOL_ANTHROPIC:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
    elif p.protocol == PROTOCOL_AZURE:
        h = {"Content-Type": "application/json", "api-key": api_key or ""}
    else:  # openai / bedrock
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key or ''}"}
    if extra:
        for k, v in extra.items():
            if isinstance(v, str):
                h[str(k)] = v
    return h


def models_endpoint(p: ProviderDef, api_base: str | None) -> str:
    """Resolve the /models URL, or ``""`` if the provider doesn't expose one."""
    if not p.fetch_models:
        return ""
    if p.models_require_custom_base and not api_base:
        return ""
    base = api_base or p.base_url
    if not base:
        return ""
    return base.rstrip("/") + "/models"


def models_headers(p: ProviderDef, api_key: str | None) -> dict[str, str]:
    """Headers for the /models listing endpoint."""
    auth = models_auth_for(p)
    key = (api_key or "").strip()
    h: dict[str, str] = {"Content-Type": "application/json"}
    if auth == "x-api-key":
        h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
    elif auth == "azure":
        h["api-key"] = key
    else:  # bearer
        if key:
            h["Authorization"] = f"Bearer {key}"
    for k, v in p.listing_headers.items():
        h[k] = v
    return h


def test_headers(p: ProviderDef, api_key: str | None) -> dict[str, str]:
    """Headers for the connectivity test. Like models_headers but ``local`` uses a no-key sentinel."""
    if p.protocol == PROTOCOL_ANTHROPIC:
        h: dict[str, str] = {
            "Content-Type": "application/json",
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
    elif p.protocol == PROTOCOL_AZURE:
        h = {"Content-Type": "application/json", "api-key": api_key or ""}
    else:
        key = api_key or ("no-key" if p.id == "local" else "")
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    for k, v in p.listing_headers.items():
        h[k] = v
    return h


def test_body(p: ProviderDef, model: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok' only."}],
        "max_tokens": 5,
    }
    if p.id == "local":
        body["stream"] = False
    return body


def no_models_message(p: ProviderDef) -> str | None:
    """Friendly message when /models isn't available, else ``None``."""
    if p.id == "aws-bedrock":
        return "AWS Bedrock 需通过 AWS 凭证访问，暂不支持自动拉取模型列表"
    if p.id == "xiaomi-mimo":
        return "小米 Mimo 的 Anthropic 兼容 API 暂未提供 /models 端点，请使用 mimo-v2.5-pro 或 mimo-v2.5-pro[1m]"
    return None


def parse_models_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize a provider /models response to ``[{id, owned_by, created}]``."""
    items = data.get("data", []) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = it.get("id") or it.get("name") or it.get("model")
        if not mid:
            continue
        result.append({
            "id": str(mid),
            "owned_by": it.get("owned_by") or it.get("organization") or None,
            "created": it.get("created"),
        })
    result.sort(key=lambda m: (-(m.get("created") or 0), m["id"]))
    return result
