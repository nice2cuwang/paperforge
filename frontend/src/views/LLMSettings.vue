<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import DOMPurify from "dompurify";
import { apiRequest } from "../api";
import ModelCombobox from "../components/ModelCombobox.vue";
import type {
  LLMConfig,
  LLMConfigListResponse,
  LLMPreset,
  LLMProviderModel,
  LLMTestResult,
  LLMModelsFetchResponse,
  LLMFetchedModel,
} from "../types";

type ModelOption = { id: string; label: string };

const CATEGORY_LABELS: Record<string, string> = {
  major: "主流厂商",
  marketplace: "中转市场",
  local: "本地 / 自定义",
};

const configs = ref<LLMConfig[]>([]);
const activeId = ref<string | null>(null);
const visionId = ref<string | null>(null);
const imageGenId = ref<string | null>(null);
const presets = ref<LLMPreset[]>([]);
const loading = ref(false);
const error = ref("");
const success = ref("");

// Add modal state
const showAddModal = ref(false);
const addStep = ref(1);
const selectedPresetId = ref("");
const presetCategory = ref<string>("major");
const presetSearch = ref("");
const addForm = ref({
  name: "",
  provider: "",
  model: "",
  api_key: "",
  api_base: "",
  temperature: 0.7,
  max_tokens: 4096,
  timeout: 60,
  proxy_url: "",
  use_system_proxy: false,
  strategy_mode: "balanced",
  enable_reasoning: true,
  preferred_max_tokens: null as number | null,
});
const addFetchedModels = ref<LLMFetchedModel[]>([]);
const addFetchingModels = ref(false);
// After a successful "刷新在线模型列表" the dropdown must open by itself -
// otherwise nothing visibly changes and the fetched list looks missing.
const addCombobox = ref<InstanceType<typeof ModelCombobox> | null>(null);

// Edit modal state
const showEditModal = ref(false);
const editForm = ref<Partial<LLMConfig>>({});
const editingId = ref("");
const editFetchedModels = ref<LLMFetchedModel[]>([]);
const editFetchingModels = ref(false);
const editCombobox = ref<InstanceType<typeof ModelCombobox> | null>(null);

// Test states per config
const testStates = ref<Record<string, { loading: boolean; result?: LLMTestResult }>>({});

const selectedPreset = computed<LLMPreset | undefined>(() =>
  presets.value.find((p) => p.id === selectedPresetId.value)
);

const availableModels = computed<LLMProviderModel[]>(() => {
  return selectedPreset.value?.models ?? [];
});

const existingProviderIds = computed(() => new Set(configs.value.map((c) => c.provider)));

const categoryCounts = computed(() => {
  const counts: Record<string, number> = { major: 0, marketplace: 0, local: 0 };
  for (const p of presets.value) counts[p.category] = (counts[p.category] || 0) + 1;
  return counts;
});

const filteredPresets = computed<LLMPreset[]>(() => {
  const q = presetSearch.value.trim().toLowerCase();
  return presets.value.filter((p) => {
    if (p.category !== presetCategory.value) return false;
    if (!q) return true;
    return (
      p.name.toLowerCase().includes(q) ||
      p.id.toLowerCase().includes(q) ||
      (p.description || "").toLowerCase().includes(q)
    );
  });
});

function buildModelOptions(
  known: LLMProviderModel[],
  fetched: LLMFetchedModel[],
  current: string
): ModelOption[] {
  const opts: ModelOption[] = [];
  const seen = new Set<string>();
  const knownIds = new Set(known.map((m) => m.id));
  // Newly fetched models that aren't in the preset list go first, so a refresh
  // visibly changes the dropdown even before the user expands it.
  for (const m of fetched) {
    if (!knownIds.has(m.id) && !seen.has(m.id)) {
      seen.add(m.id);
      opts.push({ id: m.id, label: `${m.id}（在线）` });
    }
  }
  for (const m of known) {
    if (!seen.has(m.id)) {
      seen.add(m.id);
      opts.push({ id: m.id, label: m.description ? `${m.name} - ${m.description}` : m.name });
    }
  }
  if (current && !seen.has(current)) {
    opts.push({ id: current, label: `${current}（自定义）` });
  }
  return opts;
}

// Deduped fetched models feed straight into the combobox options.
const addModelText = computed({
  get: () => addForm.value.model,
  set: (v: string) => {
    addForm.value.model = v;
  },
});
const editModelText = computed({
  get: () => (editForm.value.model as string) || "",
  set: (v: string) => {
    editForm.value.model = v;
  },
});

const addModelOptions = computed(() =>
  buildModelOptions(availableModels.value, addFetchedModels.value, addForm.value.model)
);

const editPreset = computed<LLMPreset | undefined>(() =>
  presets.value.find((p) => p.id === editForm.value.provider)
);

const editModelOptions = computed(() =>
  buildModelOptions(
    editPreset.value?.models ?? [],
    editFetchedModels.value,
    (editForm.value.model as string) || ""
  )
);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const data = await apiRequest<LLMConfigListResponse>("/api/llm/configs");
    configs.value = data.configs;
    activeId.value = data.active_id;
    visionId.value = data.vision_id;
    imageGenId.value = data.image_gen_id;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

async function loadPresets() {
  try {
    const data = await apiRequest<{ presets: LLMPreset[] }>("/api/llm/presets");
    presets.value = data.presets;
  } catch (err) {
    console.error("Failed to load presets", err);
  }
}

function openAddModal() {
  addStep.value = 1;
  selectedPresetId.value = "";
  presetCategory.value = "major";
  presetSearch.value = "";
  addFetchedModels.value = [];
  addForm.value = {
    name: "",
    provider: "",
    model: "",
    api_key: "",
    api_base: "",
    temperature: 0.7,
    max_tokens: 4096,
    timeout: 60,
    proxy_url: "",
    use_system_proxy: false,
    strategy_mode: "balanced",
    enable_reasoning: true,
    preferred_max_tokens: null,
  };
  showAddModal.value = true;
}

function selectPreset(presetId: string) {
  selectedPresetId.value = presetId;
  const preset = presets.value.find((p) => p.id === presetId);
  if (preset) {
    addForm.value.provider = preset.id;
    addForm.value.name = preset.name;
    addForm.value.model = preset.models[0]?.id ?? "";
    addForm.value.api_base = preset.default_base_url ?? "";
  }
  addFetchedModels.value = [];
  addStep.value = 2;
}

function goBackToPresetSelect() {
  addStep.value = 1;
}

async function fetchModelsForAdd() {
  if (!addForm.value.provider) return;
  addFetchingModels.value = true;
  error.value = "";
  try {
    const data = await apiRequest<LLMModelsFetchResponse>("/api/llm/models-fetch", {
      method: "POST",
      body: JSON.stringify({
        provider: addForm.value.provider,
        api_key: addForm.value.api_key || null,
        api_base: addForm.value.api_base || null,
        proxy_url: addForm.value.proxy_url || null,
        timeout: 15,
      }),
    });
    if (data.success && data.models.length) {
      addFetchedModels.value = data.models;
      success.value = data.message;
      // options are computed - wait a tick, then open the panel with the
      // text filter cleared so the online list is immediately visible.
      await nextTick();
      addCombobox.value?.openPanel(true);
    } else {
      error.value = data.message || "拉取模型列表失败";
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "拉取失败";
  } finally {
    addFetchingModels.value = false;
  }
}

async function saveNewConfig() {
  error.value = "";
  success.value = "";
  try {
    await apiRequest<LLMConfig>("/api/llm/configs", {
      method: "POST",
      body: JSON.stringify(addForm.value),
    });
    showAddModal.value = false;
    success.value = "配置已添加";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "添加失败";
  }
}

async function activateConfig(configId: string) {
  error.value = "";
  try {
    await apiRequest<LLMConfig>(`/api/llm/configs/${configId}/activate`, { method: "POST" });
    activeId.value = configId;
    success.value = "已切换生效配置";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "切换失败";
  }
}

async function toggleVisionConfig(configId: string) {
  error.value = "";
  try {
    const data = await apiRequest<LLMConfig>(`/api/llm/configs/${configId}/vision`, { method: "POST" });
    visionId.value = data.is_vision ? configId : null;
    success.value = data.is_vision ? "已设为视觉模型（用于配图识别）" : "已取消视觉模型";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "设置失败";
  }
}

async function toggleImageGenConfig(configId: string) {
  error.value = "";
  try {
    const data = await apiRequest<LLMConfig>(`/api/llm/configs/${configId}/image-gen`, { method: "POST" });
    imageGenId.value = data.is_image_gen ? configId : null;
    success.value = data.is_image_gen ? "已设为生图模型（找不到合适配图时自动生成）" : "已取消生图模型";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "设置失败";
  }
}

async function deleteConfig(configId: string) {
  if (!confirm("确定删除此配置？")) return;
  error.value = "";
  try {
    await apiRequest<undefined>(`/api/llm/configs/${configId}`, { method: "DELETE" });
    success.value = "配置已删除";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除失败";
  }
}

function openEditModal(config: LLMConfig) {
  editingId.value = config.id;
  // The list endpoint returns a MASKED api_key (sk-1****abcd). Don't feed that
  // back into the form - show an empty field with the "留空则保持原值"
  // placeholder instead, so saving without retyping keeps the stored key
  // (the backend also rejects masked/empty values defensively).
  editForm.value = { ...config, api_key: "" };
  editFetchedModels.value = [];
  showEditModal.value = true;
}

async function fetchModelsForEdit() {
  if (!editingId.value) return;
  editFetchingModels.value = true;
  error.value = "";
  try {
    // Send config_id so the backend fills in provider/base/proxy from the
    // saved config, but pass the typed api_key/api_base when present so the
    // user can verify a freshly entered key WITHOUT saving first (the saved
    // key may be the masked/invalid one). An empty api_key falls back to the
    // stored key server-side.
    const data = await apiRequest<LLMModelsFetchResponse>("/api/llm/models-fetch", {
      method: "POST",
      body: JSON.stringify({
        provider: editForm.value.provider,
        config_id: editingId.value,
        api_key: editForm.value.api_key || null,
        api_base: editForm.value.api_base || null,
        proxy_url: editForm.value.proxy_url || null,
        timeout: 15,
      }),
    });
    if (data.success && data.models.length) {
      editFetchedModels.value = data.models;
      success.value = data.message;
      await nextTick();
      editCombobox.value?.openPanel(true);
    } else {
      error.value = data.message || "拉取模型列表失败";
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "拉取失败";
  } finally {
    editFetchingModels.value = false;
  }
}

async function saveEdit() {
  error.value = "";
  success.value = "";
  try {
    await apiRequest<LLMConfig>(`/api/llm/configs/${editingId.value}`, {
      method: "PATCH",
      body: JSON.stringify(editForm.value),
    });
    showEditModal.value = false;
    success.value = "配置已更新";
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "更新失败";
  }
}

async function testConfig(config: LLMConfig) {
  error.value = "";
  testStates.value[config.id] = { loading: true };
  try {
    const result = await apiRequest<LLMTestResult>(`/api/llm/configs/${config.id}/test`, { method: "POST" });
    testStates.value[config.id] = { loading: false, result };
  } catch (err) {
    testStates.value[config.id] = {
      loading: false,
      result: {
        success: false,
        latency_ms: 0,
        message: err instanceof Error ? err.message : "测试失败",
        model: null,
        usage: null,
      },
    };
  }
}

function latencyClass(ms: number): string {
  if (ms < 100) return "latency-good";
  if (ms < 500) return "latency-mid";
  return "latency-bad";
}

function latencyLabel(ms: number): string {
  if (ms < 100) return "极佳";
  if (ms < 500) return "一般";
  return "较慢";
}

function baseSummary(preset: LLMPreset): string {
  return preset.default_base_url || (preset.supports_custom_base ? "自定义 base URL" : "—");
}

onMounted(() => {
  load();
  loadPresets();
});
</script>

<template>
  <section class="page">
    <header class="hero card">
      <div>
        <h1>模型配置</h1>
        <p>管理多个 LLM 提供商，一键切换、实时测速、在线拉取模型列表。</p>
      </div>
      <button class="primary" type="button" @click="openAddModal">+ 添加提供商</button>
    </header>

    <div v-if="loading && configs.length === 0" class="loading-state card">
      <span class="spinner" />
      加载中...
    </div>

    <div v-else-if="configs.length === 0" class="empty-state card">
      <h3>还没有配置任何模型提供商</h3>
      <p>点击右上角"添加提供商"，快速接入 OpenAI、DeepSeek、Kimi 等服务。</p>
      <button class="primary" type="button" @click="openAddModal">添加第一个提供商</button>
    </div>

    <section v-else class="provider-grid">
      <article
        v-for="config in configs"
        :key="config.id"
        class="provider-card card"
        :class="{ active: config.id === activeId }"
      >
        <div class="card-header">
          <div class="status-dot" :class="{ on: config.id === activeId }" :aria-label="config.id === activeId ? '已启用' : '未启用'" />
          <h3>{{ config.name }}</h3>
          <span class="provider-tag">{{ config.provider }}</span>
          <span v-if="config.id === visionId" class="vision-tag" title="用于配图识别等图像理解调用">视觉模型</span>
          <span v-if="config.id === imageGenId" class="vision-tag imggen" title="找不到合适论文图表时用该配置自动生成配图">生图模型</span>
        </div>

        <div class="card-body">
          <p class="model-line">
            <span class="label">模型</span>
            <span class="value">{{ config.model }}</span>
          </p>

          <div v-if="testStates[config.id]?.result" class="test-result">
            <span
              class="latency-badge"
              :class="latencyClass(testStates[config.id].result!.latency_ms)"
            >
              {{ testStates[config.id].result!.success ? `${testStates[config.id].result!.latency_ms}ms · ${latencyLabel(testStates[config.id].result!.latency_ms)}` : "连接失败" }}
            </span>
            <span v-if="!testStates[config.id].result!.success" class="test-error">
              {{ testStates[config.id].result!.message }}
            </span>
          </div>
        </div>

        <div class="card-actions">
          <button
            v-if="config.id !== activeId"
            type="button"
            class="activate-btn"
            @click="activateConfig(config.id)"
          >
            启用
          </button>
          <span v-else class="activated-label">当前生效</span>

          <button
            type="button"
            class="ghost-btn"
            :class="{ 'vision-on': config.id === visionId }"
            :title="config.id === visionId ? '取消后图像调用回退到生效配置' : '配图识别等图像调用走此配置'"
            @click="toggleVisionConfig(config.id)"
          >
            {{ config.id === visionId ? "取消视觉" : "设为视觉" }}
          </button>

          <button
            type="button"
            class="ghost-btn"
            :class="config.id === imageGenId ? 'imggen-on' : ''"
            :title="config.id === imageGenId ? '取消后回退到 Pollinations 免费生图' : '找不到合适配图时用此配置生成插图（需为文生图模型，如 SiliconFlow/Kwai Kolors、智谱 CogView 等）'"
            @click="toggleImageGenConfig(config.id)"
          >
            {{ config.id === imageGenId ? "取消生图" : "设为生图" }}
          </button>

          <button
            type="button"
            class="ghost-btn"
            :disabled="testStates[config.id]?.loading"
            @click="testConfig(config)"
          >
            <span v-if="testStates[config.id]?.loading" class="spinner" />
            {{ testStates[config.id]?.loading ? "测速中..." : "测速" }}
          </button>

          <button type="button" class="ghost-btn" @click="openEditModal(config)">编辑</button>
          <button type="button" class="danger-btn" @click="deleteConfig(config.id)">删除</button>
        </div>
      </article>
    </section>

    <div v-if="success" class="toast success">{{ success }}</div>
    <div v-if="error" class="toast error">{{ error }}</div>

    <!-- Add Modal -->
    <Teleport to="body">
      <div v-if="showAddModal" class="modal-overlay" @click.self="showAddModal = false">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="add-modal-title">
          <header class="modal-header">
            <h2 id="add-modal-title">添加提供商</h2>
            <button type="button" class="close-btn" aria-label="关闭" @click="showAddModal = false">&times;</button>
          </header>

          <div v-if="addStep === 1" class="modal-body">
            <div class="preset-toolbar">
              <div class="preset-tabs">
                <button
                  v-for="cat in ['major', 'marketplace', 'local']"
                  :key="cat"
                  type="button"
                  class="preset-tab"
                  :class="{ active: presetCategory === cat }"
                  @click="presetCategory = cat"
                >
                  {{ CATEGORY_LABELS[cat] }} ({{ categoryCounts[cat] || 0 }})
                </button>
              </div>
              <input v-model="presetSearch" class="preset-search" type="search" placeholder="搜索供应商…" />
            </div>

            <div v-if="filteredPresets.length === 0" class="preset-empty">没有匹配的供应商</div>
            <div v-else class="preset-grid">
              <button
                v-for="preset in filteredPresets"
                :key="preset.id"
                type="button"
                class="preset-card"
                :class="{ added: existingProviderIds.has(preset.id) }"
                @click="selectPreset(preset.id)"
              >
                <span class="preset-top">
                  <span class="preset-icon" v-html="DOMPurify.sanitize(preset.logo_svg)" />
                  <span class="preset-name">{{ preset.name }}</span>
                  <span v-if="existingProviderIds.has(preset.id)" class="preset-added-badge">已添加</span>
                </span>
                <span class="preset-base">{{ baseSummary(preset) }}</span>
                <span class="preset-desc">{{ preset.description }}</span>
              </button>
            </div>
          </div>

          <div v-else class="modal-body">
            <button type="button" class="back-link" @click="goBackToPresetSelect">&larr; 重新选择预设</button>

            <div class="field">
              <label for="add-name">配置名称</label>
              <input id="add-name" v-model="addForm.name" type="text" autocomplete="off" placeholder="例如：我的 DeepSeek" />
            </div>

            <div class="field">
              <label for="add-model">
                模型
                <button
                  type="button"
                  class="inline-link"
                  :disabled="addFetchingModels || !addForm.provider"
                  @click="fetchModelsForAdd"
                >
                  <span v-if="addFetchingModels" class="spinner" />
                  {{ addFetchingModels ? "拉取中…" : "刷新在线模型列表" }}
                </button>
              </label>
              <ModelCombobox id="add-model" ref="addCombobox" v-model="addModelText" :options="addModelOptions" />
            </div>

            <div class="field">
              <label for="add-api-key">
                API Key
                <span v-if="selectedPreset && !selectedPreset.requires_api_key" class="optional">（可选）</span>
              </label>
              <!-- autocomplete="new-password" 阻止 Edge 把"文本框+密码框"当登录表单弹"保存密码"浮层 —— 该原生浮层正好盖住下方的模型下拉面板 -->
              <input id="add-api-key" v-model="addForm.api_key" type="password" autocomplete="new-password" placeholder="sk-..." />
            </div>

            <div v-if="selectedPreset?.supports_custom_base" class="field">
              <label for="add-api-base">API Base URL</label>
              <input id="add-api-base" v-model="addForm.api_base" type="url" placeholder="https://..." />
            </div>

            <div class="field-row">
              <div class="field">
                <label for="add-temperature">Temperature <span class="value">{{ addForm.temperature }}</span></label>
                <input id="add-temperature" v-model.number="addForm.temperature" type="range" min="0" max="2" step="0.1" />
              </div>
              <div class="field">
                <label for="add-max-tokens">Max Tokens</label>
                <input id="add-max-tokens" v-model.number="addForm.max_tokens" type="number" min="1" max="128000" />
              </div>
              <div class="field">
                <label for="add-timeout">超时（秒）</label>
                <input id="add-timeout" v-model.number="addForm.timeout" type="number" min="5" max="600" />
              </div>
            </div>

            <div class="field">
              <label for="add-proxy">代理地址</label>
              <input id="add-proxy" v-model="addForm.proxy_url" type="url" placeholder="http://host.docker.internal:7890" />
            </div>

            <div class="field-row">
              <div class="field">
                <label for="add-strategy">策略模式</label>
                <select id="add-strategy" v-model="addForm.strategy_mode">
                  <option value="fast">快速（Fast）</option>
                  <option value="balanced">均衡（Balanced）</option>
                  <option value="quality">高质量（Quality）</option>
                  <option value="reasoning">深度推理（Reasoning）</option>
                </select>
              </div>
              <div class="field" style="display:flex;align-items:center;gap:0.4rem;padding-top:1.6rem;">
                <input id="add-reasoning" v-model="addForm.enable_reasoning" type="checkbox" />
                <label for="add-reasoning" style="margin:0;font-weight:400;">启用 Reasoning</label>
              </div>
              <div class="field">
                <label for="add-preferred-max-tokens">最大 Token 覆盖</label>
                <input id="add-preferred-max-tokens" v-model.number="addForm.preferred_max_tokens" type="number" min="1" max="128000" placeholder="默认" />
              </div>
            </div>
          </div>

          <footer v-if="addStep === 2" class="modal-footer">
            <button type="button" class="secondary" @click="showAddModal = false">取消</button>
            <button type="button" class="primary" @click="saveNewConfig">保存</button>
          </footer>
        </div>
      </div>
    </Teleport>

    <!-- Edit Modal -->
    <Teleport to="body">
      <div v-if="showEditModal" class="modal-overlay" @click.self="showEditModal = false">
        <div class="modal" role="dialog" aria-modal="true" aria-labelledby="edit-modal-title">
          <header class="modal-header">
            <h2 id="edit-modal-title">编辑配置</h2>
            <button type="button" class="close-btn" aria-label="关闭" @click="showEditModal = false">&times;</button>
          </header>

          <div class="modal-body">
            <div class="field">
              <label for="edit-name">配置名称</label>
              <input id="edit-name" v-model="editForm.name" type="text" autocomplete="off" />
            </div>

            <div class="field">
              <label for="edit-model">
                模型
                <button
                  type="button"
                  class="inline-link"
                  :disabled="editFetchingModels"
                  @click="fetchModelsForEdit"
                >
                  <span v-if="editFetchingModels" class="spinner" />
                  {{ editFetchingModels ? "拉取中…" : "刷新在线模型列表" }}
                </button>
              </label>
              <ModelCombobox id="edit-model" ref="editCombobox" v-model="editModelText" :options="editModelOptions" />
            </div>

            <div class="field">
              <label for="edit-api-key">API Key</label>
              <!-- 同上：防止 Edge 保存密码浮层遮挡模型下拉 -->
              <input id="edit-api-key" v-model="editForm.api_key" type="password" autocomplete="new-password" placeholder="留空则保持原值" />
            </div>

            <div class="field">
              <label for="edit-api-base">API Base URL</label>
              <input id="edit-api-base" v-model="editForm.api_base" type="url" />
            </div>

            <div class="field-row">
              <div class="field">
                <label for="edit-temperature">Temperature <span class="value">{{ editForm.temperature }}</span></label>
                <input id="edit-temperature" v-model.number="editForm.temperature" type="range" min="0" max="2" step="0.1" />
              </div>
              <div class="field">
                <label for="edit-max-tokens">Max Tokens</label>
                <input id="edit-max-tokens" v-model.number="editForm.max_tokens" type="number" min="1" max="128000" />
              </div>
              <div class="field">
                <label for="edit-timeout">超时（秒）</label>
                <input id="edit-timeout" v-model.number="editForm.timeout" type="number" min="5" max="600" />
              </div>
            </div>

            <div class="field">
              <label for="edit-proxy">代理地址</label>
              <input id="edit-proxy" v-model="editForm.proxy_url" type="url" />
            </div>

            <div class="field-row">
              <div class="field">
                <label for="edit-strategy">策略模式</label>
                <select id="edit-strategy" v-model="editForm.strategy_mode">
                  <option value="fast">快速（Fast）</option>
                  <option value="balanced">均衡（Balanced）</option>
                  <option value="quality">高质量（Quality）</option>
                  <option value="reasoning">深度推理（Reasoning）</option>
                </select>
              </div>
              <div class="field" style="display:flex;align-items:center;gap:0.4rem;padding-top:1.6rem;">
                <input id="edit-reasoning" v-model="editForm.enable_reasoning" type="checkbox" />
                <label for="edit-reasoning" style="margin:0;font-weight:400;">启用 Reasoning</label>
              </div>
              <div class="field">
                <label for="edit-preferred-max-tokens">最大 Token 覆盖</label>
                <input id="edit-preferred-max-tokens" v-model.number="editForm.preferred_max_tokens" type="number" min="1" max="128000" placeholder="默认" />
              </div>
            </div>
          </div>

          <footer class="modal-footer">
            <button type="button" class="secondary" @click="showEditModal = false">取消</button>
            <button type="button" class="primary" @click="saveEdit">保存</button>
          </footer>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
  max-width: 1100px;
}

.card {
  border: 1px solid var(--line, #d9dfeb);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  background: var(--surface, #fff);
  padding: 1.2rem;
  animation: rise-in 260ms ease;
}

.hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
  background:
    radial-gradient(260px 130px at 95% -10%, rgba(245, 214, 158, 0.55) 0%, transparent 70%),
    var(--surface, #fff);
}

.hero h1 {
  margin: 0;
  font: 700 1.82rem/1.2 var(--font-display), "Noto Sans SC", sans-serif;
}

.hero p {
  margin: 0.56rem 0 0;
  color: var(--muted);
}

button {
  border: 0;
  border-radius: 11px;
  padding: 0.5rem 0.76rem;
  cursor: pointer;
  font-size: 0.92rem;
  transition: transform 160ms ease, opacity 160ms ease, background 160ms ease;
}

button:hover:not(:disabled) {
  transform: translateY(-1px);
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

button.primary {
  color: #fff;
  background: linear-gradient(135deg, var(--accent) 0%, #a06a18 100%);
}

button.secondary {
  color: var(--accent-strong);
  background: var(--accent-light);
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.9rem;
}

.provider-card {
  display: grid;
  gap: 0.7rem;
  box-shadow: var(--shadow-sm);
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.provider-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.provider-card.active {
  border-color: rgba(13, 124, 117, 0.45);
  background: linear-gradient(180deg, #f4fcf9 0%, #fff 100%);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #d1d5db;
  flex-shrink: 0;
}

.status-dot.on {
  background: var(--success);
  box-shadow: 0 0 0 3px rgba(26, 122, 76, 0.2);
}

.card-header h3 {
  margin: 0;
  font-size: 1rem;
  flex: 1;
}

.provider-tag {
  font-size: 0.72rem;
  color: var(--muted);
  background: rgba(21,29,46,0.04);
  padding: 0.15rem 0.5rem;
  border-radius: 6px;
}

.card-body {
  display: grid;
  gap: 0.4rem;
}

.model-line {
  margin: 0;
  display: flex;
  gap: 0.4rem;
  font-size: 0.9rem;
}

.model-line .label {
  color: #6b7280;
}

.model-line .value {
  color: #1f2937;
  font-weight: 500;
  word-break: break-all;
}

.test-result {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  min-height: 24px;
}

.latency-badge {
  font-size: 0.78rem;
  padding: 0.2rem 0.5rem;
  border-radius: 6px;
  font-weight: 500;
}

.latency-good {
  background: var(--success-light);
  color: var(--success);
}

.latency-mid {
  background: var(--signal-light);
  color: var(--signal);
}

.latency-bad {
  background: var(--danger-light);
  color: var(--danger);
}

.test-error {
  font-size: 0.78rem;
  color: #991b1b;
}

.card-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}

.activate-btn {
  color: #fff;
  background: linear-gradient(135deg, var(--accent) 0%, #a06a18 100%);
}

.activated-label {
  font-size: 0.82rem;
  color: var(--success);
  font-weight: 500;
  padding: 0.4rem 0.6rem;
}

.vision-tag {
  font-size: 0.72rem;
  color: #6d28d9;
  background: #ede9fe;
  border: 1px solid #ddd6fe;
  border-radius: 999px;
  padding: 0.1rem 0.55rem;
  white-space: nowrap;
}

.vision-tag.imggen {
  color: #b45309;
  background: #fef3c7;
  border-color: #fde68a;
}

.ghost-btn.vision-on {
  color: #6d28d9;
  background: #ede9fe;
}

.ghost-btn.imggen-on {
  color: #b45309;
  background: #fef3c7;
}

.ghost-btn {
  color: var(--accent-strong);
  background: var(--accent-light);
}

.danger-btn {
  color: var(--danger);
  background: var(--danger-light);
}

.empty-state {
  text-align: center;
  padding: 2.5rem 1.5rem;
}

.empty-state h3 {
  margin: 0 0 0.5rem;
  color: #1f2937;
}

.empty-state p {
  margin: 0 0 1.2rem;
  color: #576583;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.6rem;
  padding: 2rem;
  color: #576583;
}

.spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: spin 700ms linear infinite;
  vertical-align: middle;
}

.toast {
  position: fixed;
  bottom: 1.2rem;
  right: 1.2rem;
  padding: 0.7rem 1rem;
  border-radius: 12px;
  font-size: 0.92rem;
  z-index: 200;
  animation: rise-in 200ms ease;
}

.toast.success {
  background: var(--success-light);
  color: var(--success);
  border: 1px solid #bbf7d0;
}

.toast.error {
  background: var(--danger-light);
  color: var(--danger);
  border: 1px solid #fecaca;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 37, 0.4);
  backdrop-filter: blur(4px);
  display: grid;
  place-items: center;
  z-index: 150;
  padding: 1rem;
}

.modal {
  background: #fff;
  border-radius: var(--radius-lg);
  width: min(640px, 92vw);
  max-height: 88vh;
  overflow: auto;
  box-shadow: 0 24px 60px rgba(15, 39, 61, 0.18);
  animation: modal-in 220ms ease;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.2rem;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h2 {
  margin: 0;
  font-size: 1.1rem;
}

.close-btn {
  background: transparent;
  color: #6b7280;
  font-size: 1.4rem;
  line-height: 1;
  padding: 0.2rem 0.4rem;
}

.modal-body {
  padding: 1.2rem;
  display: grid;
  gap: 0.9rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.6rem;
  padding: 0.8rem 1.2rem;
  border-top: 1px solid #e5e7eb;
}

.back-link {
  background: transparent;
  color: var(--accent);
  padding: 0;
  font-size: 0.88rem;
  text-align: left;
}

.inline-link {
  margin-left: auto;
  background: transparent;
  color: var(--accent);
  padding: 0;
  font-size: 0.82rem;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
}

.preset-toolbar {
  display: flex;
  gap: 0.8rem;
  align-items: center;
  flex-wrap: wrap;
}

.preset-tabs {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
}

.preset-tab {
  background: #f1f3f8;
  color: #576583;
  padding: 0.35rem 0.7rem;
  font-size: 0.84rem;
  border-radius: 8px;
}

.preset-tab.active {
  background: var(--accent);
  color: #fff;
}

.preset-search {
  margin-left: auto;
  border: 1px solid #d1d5db;
  border-radius: 10px;
  padding: 0.45rem 0.7rem;
  font-size: 0.9rem;
  min-width: 180px;
  background: #fff;
}

.preset-search:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(13, 124, 117, 0.1);
}

.preset-empty {
  text-align: center;
  color: #6b7280;
  padding: 2rem 0;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 0.7rem;
}

.preset-card {
  display: grid;
  gap: 0.3rem;
  text-align: left;
  border: 1px solid #d9dfeb;
  border-radius: var(--radius-md);
  padding: 0.85rem;
  background: #f7f9fc;
  cursor: pointer;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
}

.preset-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-muted);
}

.preset-card.added {
  border-color: rgba(26, 122, 76, 0.4);
  background: #f4fcf9;
}

.preset-top {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.preset-icon {
  width: 24px;
  height: 24px;
  color: var(--accent);
  flex-shrink: 0;
}

.preset-icon :deep(svg) {
  width: 100%;
  height: 100%;
}

.preset-name {
  font-weight: 600;
  font-size: 0.92rem;
  flex: 1;
  word-break: break-word;
}

.preset-added-badge {
  font-size: 0.66rem;
  color: var(--success);
  background: var(--success-light);
  padding: 0.1rem 0.4rem;
  border-radius: 5px;
  flex-shrink: 0;
}

.preset-base {
  font-size: 0.72rem;
  color: #7a8699;
  font-family: var(--font-mono, ui-monospace, monospace);
  word-break: break-all;
}

.preset-desc {
  font-size: 0.76rem;
  color: #55637e;
  line-height: 1.35;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.field {
  display: grid;
  gap: 0.35rem;
}

.field label {
  font-size: 0.88rem;
  font-weight: 500;
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.field label .optional {
  font-weight: 400;
  color: #6b7280;
  font-size: 0.8rem;
}

.field label .value {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  color: var(--accent);
}

.field input[type="text"],
.field input[type="password"],
.field input[type="url"],
.field input[type="number"],
.field input[type="search"],
.field select {
  border: 1px solid #d1d5db;
  border-radius: 10px;
  padding: 0.56rem 0.7rem;
  font-size: 0.92rem;
  background: #fff;
  color: #111827;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.field input:focus,
.field select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(13, 124, 117, 0.1);
}

.field input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
}

.model-chip-active:hover {
  color: #fff;
}

.field-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.8rem;
}

@media (max-width: 640px) {
  .field-row {
    grid-template-columns: 1fr;
  }

  .provider-grid {
    grid-template-columns: 1fr;
  }

  .preset-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .preset-search {
    margin-left: 0;
    min-width: 0;
    width: 100%;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes rise-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes modal-in {
  from {
    opacity: 0;
    transform: scale(0.96) translateY(10px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
  }
}
</style>
