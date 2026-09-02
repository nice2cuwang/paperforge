# 本地模型接入（Ollama / vLLM / llama.cpp）

PaperForge 的全部生成任务（大纲、初稿、辩论式审稿、修订、配图文案）都通过 **LLM 配置**驱动。除云端 API 外，内置了两条**免 API Key** 的本地模型路径——只要本机有可用的 OpenAI 兼容推理端点，把配置指向它即可，不需要任何云端凭证。

## 内置提供商预设

LLM 配置页（`/llm-settings`）→ 添加配置，在「本地 / 自定义」分类下有两个预设：

| 预设 ID | 名称 | 默认地址 | 适用 |
|---|---|---|---|
| `local` | 本地 / 自定义 | `http://localhost:8000/v1` | 任意 OpenAI 兼容服务（vLLM、llama.cpp、LM Studio 等），自定义 `api_base` |
| `ollama` | Ollama（本地） | `http://localhost:11434/v1` | Ollama 官方 OpenAI 兼容端点 |

两者都：**不需要填写 API Key**（留空即可）；**模型 ID 任意填写**（不再校验预设清单——以你实际拉取/加载的模型名为准）；可覆盖 `api_base` 指向任意地址。

## 场景一：Ollama

```bash
# 安装并启动后，拉取一个模型（以 Qwen2.5 7B 为例）
ollama pull qwen2.5:7b
ollama serve        # 默认监听 11434，暴露 OpenAI 兼容端点 /v1/chat/completions
```

然后在前端完成配置：

1. 打开 **LLM 设置** → **添加配置**；
2. 分类选「本地 / 自定义」，提供商选 **Ollama（本地）**；
3. 模型填 **`qwen2.5:7b`**（与你 `ollama pull` 的 tag 完全一致）；
4. API Key **留空**，保存并「测试连通性」；
5. 在配置列表中把该配置设为**激活**。

等效的 API 调用（一个 LLMConfig 行）：

```bash
curl -X POST "http://127.0.0.1:8010/api/llm/configs" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "本地 Ollama",
    "provider": "ollama",
    "model": "qwen2.5:7b",
    "api_base": "http://127.0.0.1:11434/v1"
  }'

# 激活（替换为上面返回的 id）
curl -X PATCH "http://127.0.0.1:8010/api/llm/configs/{config_id}" \
  -H "Content-Type: application/json" -d '{"is_active": true}'
```

## 场景二：vLLM / llama.cpp（OpenAI 兼容服务）

用 `local` 预设 + 自定义 `api_base`：

```bash
# vLLM 示例（默认端口 8000，恰好与 local 预设默认一致，可省 api_base）
vllm serve Qwen/Qwen2.5-7B-Instruct

# llama.cpp 示例
llama-server -m qwen2.5-7b-instruct-q4_k_m.gguf --port 8080
```

| 服务 | provider | model | api_base |
|---|---|---|---|
| vLLM（默认 8000） | `local` | 服务端模型 ID，如 `Qwen/Qwen2.5-7B-Instruct` | 可留空 |
| llama.cpp :8080 | `local` | GGUF 名称（如 `qwen2.5-7b-instruct`） | `http://127.0.0.1:8080/v1` |

## 场景三：PaperForge 跑在 Docker、模型跑在宿主机

后端在容器内时，`localhost` 指向容器自身。把 `api_base` 指向宿主机的网关地址：

- Ollama → `http://host.docker.internal:11434/v1`
- vLLM → `http://host.docker.internal:8000/v1`

本仓库的 `docker-compose.yml` 还提供了一个 **`local-llm` profile** 的可选 Ollama 服务（默认不启动，不影响 CI 与常规启动）：

```bash
docker compose --profile local-llm up -d
docker compose --profile local-llm exec ollama ollama pull qwen2.5:7b
```

此时后端容器内的配置填 `api_base: http://ollama:11434/v1`（compose 服务名），前端（宿主机）浏览器不受影响。

## 能力边界与建议

- **写作质量跟随模型能力**：辩论式审稿与修订环节对指令遵循要求较高，小模型（<7B）可能出现审稿意见空泛或修订不到位。建议本地主力模型 ≥ 32B 量化版；仅为跑通全流程可用 7B 级。
- **上下文窗口**：`ollama` 预设按 128K、`local` 按 32K 声明。长文生成一次只写一节，不必担心超窗；若你的服务端实际窗口更小，可调低配置里的 `max_tokens`。
- **视觉与配图**：数据图表生成不需要视觉模型；只有「论文配图视觉标注」环节需要多模态能力——本地模型若支持视觉（如 `qwen2.5vl`），在配置里勾选「视觉标注」角色；不支持时该环节自动降级为元数据标注，不阻断主流程。
- **工具调用**：本地预设声明不支持工具调用。当前主流程为线性节点编排，不依赖模型工具调用，可放心使用。
