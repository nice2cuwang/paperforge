<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import DOMPurify from "dompurify";
import { apiRequest } from "../api";
import type { LLMConfig, LLMConfigListResponse, LLMPreset, LLMProviderModel, LLMTestResult } from "../types";

const configs = ref<LLMConfig[]>([]);
const activeId = ref<string | null>(null);
const presets = ref<LLMPreset[]>([]);
const loading = ref(false);
const error = ref("");
const success = ref("");

// Add modal state
const showAddModal = ref(false);
const addStep = ref(1);
const selectedPresetId = ref("");
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

// Edit modal state
const showEditModal = ref(false);
const editForm = ref<Partial<LLMConfig>>({});
const editingId = ref("");

// Test states per config
const testStates = ref<Record<string, { loading: boolean; result?: LLMTestResult }>>({});

const selectedPreset = computed<LLMPreset | undefined>(() =>
  presets.value.find((p) => p.id === selectedPresetId.value)
);

const availableModels = computed<LLMProviderModel[]>(() => {
  return selectedPreset.value?.models ?? [];
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const data = await apiRequest<LLMConfigListResponse>("/api/llm/configs");
    configs.value = data.configs;
    activeId.value = data.active_id;
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
  addStep.value = 2;
}

function goBackToPresetSelect() {
  addStep.value = 1;
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
  editForm.value = { ...config };
  showEditModal.value = true;
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
        <p>管理多个 LLM 提供商，一键切换、实时测速。</p>
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
            <p class="hint">选择预设提供商，系统将自动填入默认参数。</p>
            <div class="preset-grid">
              <button
                v-for="preset in presets"
                :key="preset.id"
                type="button"
                class="preset-card"
                @click="selectPreset(preset.id)"
              >
                <span class="preset-icon" v-html="DOMPurify.sanitize(preset.logo_svg)" />
                <span class="preset-name">{{ preset.name }}</span>
                <span class="preset-desc">{{ preset.description }}</span>
              </button>
            </div>
          </div>

          <div v-else class="modal-body">
            <button type="button" class="back-link" @click="goBackToPresetSelect">&larr; 重新选择预设</button>

            <div class="field">
              <label for="add-name">配置名称</label>
              <input id="add-name" v-model="addForm.name" type="text" placeholder="例如：我的 DeepSeek" />
            </div>

            <div class="field">
              <label for="add-model">模型</label>
              <select id="add-model" v-model="addForm.model">
                <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.name }} — {{ m.description }}</option>
              </select>
            </div>

            <div class="field">
              <label for="add-api-key">
                API Key
                <span v-if="selectedPreset && !selectedPreset.requires_api_key" class="optional">（可选）</span>
              </label>
              <input id="add-api-key" v-model="addForm.api_key" type="password" placeholder="sk-..." />
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
              <input id="edit-name" v-model="editForm.name" type="text" />
            </div>

            <div class="field">
              <label for="edit-model">模型</label>
              <input id="edit-model" v-model="editForm.model" type="text" />
            </div>

            <div class="field">
              <label for="edit-api-key">API Key</label>
              <input id="edit-api-key" v-model="editForm.api_key" type="password" placeholder="留空则保持原值" />
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
  border-radius: 18px;
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
  font: 700 1.82rem/1.2 "Space Grotesk", "Noto Sans SC", sans-serif;
}

.hero p {
  margin: 0.56rem 0 0;
  color: #3a4c67;
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
  background: linear-gradient(90deg, #0f7f78 0%, #c07817 100%);
}

button.secondary {
  color: #1f4568;
  background: #deecff;
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.9rem;
}

.provider-card {
  display: grid;
  gap: 0.7rem;
  transition: transform 160ms ease, box-shadow 160ms ease, border-color 160ms ease;
}

.provider-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(15, 39, 61, 0.08);
}

.provider-card.active {
  border-color: rgba(19, 121, 99, 0.55);
  background: linear-gradient(180deg, #f6fdf9 0%, #fff 100%);
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
  background: #22c55e;
  box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.2);
}

.card-header h3 {
  margin: 0;
  font-size: 1rem;
  flex: 1;
}

.provider-tag {
  font-size: 0.72rem;
  color: #576583;
  background: #eef2f8;
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
  background: #dcfce7;
  color: #166534;
}

.latency-mid {
  background: #fef9c3;
  color: #854d0e;
}

.latency-bad {
  background: #fee2e2;
  color: #991b1b;
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
  background: linear-gradient(90deg, #0f7f78 0%, #c07817 100%);
}

.activated-label {
  font-size: 0.82rem;
  color: #15803d;
  font-weight: 500;
  padding: 0.4rem 0.6rem;
}

.ghost-btn {
  color: #1f4568;
  background: #deecff;
}

.danger-btn {
  color: #991b1b;
  background: #fee2e2;
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
  background: #dcfce7;
  color: #166534;
  border: 1px solid #bbf7d0;
}

.toast.error {
  background: #fee2e2;
  color: #991b1b;
  border: 1px solid #fecaca;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 37, 0.45);
  backdrop-filter: blur(4px);
  display: grid;
  place-items: center;
  z-index: 150;
  padding: 1rem;
}

.modal {
  background: #fff;
  border-radius: 18px;
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

.hint {
  margin: 0;
  color: #576583;
  font-size: 0.9rem;
}

.back-link {
  background: transparent;
  color: #0f7f78;
  padding: 0;
  font-size: 0.88rem;
  text-align: left;
}

.preset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 0.7rem;
}

.preset-card {
  display: grid;
  gap: 0.35rem;
  text-align: left;
  border: 1px solid #d9dfeb;
  border-radius: 14px;
  padding: 0.9rem;
  background: #f7f9fc;
  cursor: pointer;
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
}

.preset-card:hover {
  transform: translateY(-2px);
  border-color: rgba(19, 121, 99, 0.35);
}

.preset-icon {
  width: 28px;
  height: 28px;
  color: #0f7f78;
}

.preset-icon :deep(svg) {
  width: 100%;
  height: 100%;
}

.preset-name {
  font-weight: 600;
  font-size: 0.96rem;
}

.preset-desc {
  font-size: 0.78rem;
  color: #55637e;
  line-height: 1.35;
}

.field {
  display: grid;
  gap: 0.35rem;
}

.field label {
  font-size: 0.88rem;
  font-weight: 500;
  color: #1f2937;
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
  color: #0f7f78;
}

.field input[type="text"],
.field input[type="password"],
.field input[type="url"],
.field input[type="number"],
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
  border-color: #0f7f78;
  box-shadow: 0 0 0 3px rgba(15, 127, 120, 0.12);
}

.field input[type="range"] {
  width: 100%;
  accent-color: #0f7f78;
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
