<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { apiRequest } from "../api";
import TaskProgress from "../components/TaskProgress.vue";
import type {
  AutoWorkflowResult,
  Draft,
  EvidenceCard,
  Paper,
  Project,
  ProjectTokenUsage,
  ReviewIssue,
  TaskPayload
} from "../types";

const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.projectId));

const project = ref<Project | null>(null);
const papers = ref<Paper[]>([]);
const evidence = ref<EvidenceCard[]>([]);
const drafts = ref<Draft[]>([]);
const issues = ref<ReviewIssue[]>([]);
const task = ref<TaskPayload | null>(null);
const autoWorkflow = ref<AutoWorkflowResult | null>(null);
const tokenUsage = ref<ProjectTokenUsage | null>(null);
const error = ref("");
const busy = ref(false);
const workflowHint = ref("");

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtDuration(ms: number): string {
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)}h`;
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}min`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${ms}ms`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  return iso.replace("T", " ").slice(0, 16);
}

const workflowStages = [
  { threshold: 5, label: "检索", desc: "拉取候选论文并去重" },
  { threshold: 30, label: "解析", desc: "下载/复用 PDF 并切块" },
  { threshold: 66, label: "证据", desc: "生成可追溯证据卡" },
  { threshold: 78, label: "草稿", desc: "基于证据自动写作" },
  { threshold: 86, label: "审查", desc: "多智能体辩论审查" },
  { threshold: 93, label: "修订", desc: "生成修订稿" },
  { threshold: 97, label: "导出", desc: "markdown/docx/pdf/bib" }
];

const currentStageIndex = computed(() => {
  const progress = task.value?.progress ?? 0;
  let idx = -1;
  workflowStages.forEach((stage, stageIndex) => {
    if (progress >= stage.threshold) idx = stageIndex;
  });
  return idx;
});

async function loadAll() {
  const id = projectId.value;
  project.value = await apiRequest<Project>(`/api/projects/${id}`);
  papers.value = await apiRequest<Paper[]>(`/api/projects/${id}/papers`);
  evidence.value = await apiRequest<EvidenceCard[]>(`/api/projects/${id}/evidence`);
  drafts.value = await apiRequest<Draft[]>(`/api/projects/${id}/drafts`);
  issues.value = await apiRequest<ReviewIssue[]>(`/api/projects/${id}/review-issues`);
  try {
    tokenUsage.value = await apiRequest<ProjectTokenUsage>(`/api/projects/${id}/token-usage`);
  } catch {
    tokenUsage.value = null; // 统计失败不阻塞页面
  }
}

const TASK_STORAGE_KEY = (pid: string) => `paperforge_task_${pid}`;

async function pollTask(taskId: string, maxMs = 10 * 60 * 1000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    try {
      const payload = await apiRequest<TaskPayload>(`/api/tasks/${taskId}`);
      task.value = payload;
      if (payload.status !== "running") {
        localStorage.removeItem(TASK_STORAGE_KEY(projectId.value));
        break;
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "";
      if (message.includes("404") || message.toLowerCase().includes("not found")) {
        task.value = null;
        localStorage.removeItem(TASK_STORAGE_KEY(projectId.value));
        throw new Error("任务已丢失（后端可能重启）。请重新提交。");
      }
      throw err;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function execute(action: () => Promise<{ task_id?: string }>) {
  error.value = "";
  busy.value = true;
  try {
    const result = await action();
    if (result.task_id) {
      await pollTask(result.task_id);
    }
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "操作失败";
  } finally {
    busy.value = false;
  }
}

async function quickSearch() {
  await execute(() => apiRequest(`/api/projects/${projectId.value}/search-papers`, { method: "POST", body: "{}" }));
}

async function quickBuildEvidence() {
  await execute(() =>
    apiRequest(`/api/projects/${projectId.value}/build-evidence`, {
      method: "POST",
      body: JSON.stringify({ max_cards: 120, only_selected: true })
    })
  );
}

async function quickGenerateDraft() {
  await execute(() =>
    apiRequest(`/api/projects/${projectId.value}/generate-draft`, {
      method: "POST",
      body: JSON.stringify({ title: project.value?.title ?? "Draft" })
    })
  );
}

async function quickReview() {
  if (!drafts.value[0]) return;
  await execute(() =>
    apiRequest(`/api/projects/${projectId.value}/review-draft`, {
      method: "POST",
      body: JSON.stringify({ draft_id: drafts.value[0].id })
    })
  );
}

async function quickRevise() {
  if (!drafts.value[0]) return;
  await execute(() =>
    apiRequest(`/api/projects/${projectId.value}/revise-draft`, {
      method: "POST",
      body: JSON.stringify({ draft_id: drafts.value[0].id })
    })
  );
}

async function runAutoWorkflow() {
  if (!project.value) return;
  error.value = "";
  busy.value = true;
  autoWorkflow.value = null;
  workflowHint.value = "";
  try {
    const payload = await apiRequest<{ task_id: string; status: string }>(
      `/api/projects/${projectId.value}/run-auto-workflow-async`,
      {
        method: "POST",
        body: JSON.stringify({
          query: project.value.research_question,
          max_results: 25,
          auto_select_limit: 12,
          chunk_size: 900,
          max_cards: 120,
          draft_title: `${project.value.title} 自动草稿`,
          auto_export: true
        })
      }
    );
    workflowHint.value = `任务 ${payload.task_id.slice(0, 8)} 已提交，正在执行...`;
    localStorage.setItem(TASK_STORAGE_KEY(projectId.value), payload.task_id);
    await pollTask(payload.task_id);

    const result = task.value?.result ?? {};
    if (task.value?.status === "completed") {
      autoWorkflow.value = {
        task_id: payload.task_id,
        query: String(result.query ?? ""),
        inserted_count: Number(result.inserted_count ?? 0),
        total_papers: Number(result.total_papers ?? 0),
        selected_count: Number(result.selected_count ?? 0),
        auto_selected_count: Number(result.auto_selected_count ?? 0),
        reused_local_pdf_count: Number(result.reused_local_pdf_count ?? 0),
        resolved_via_fallback_count: Number(result.resolved_via_fallback_count ?? 0),
        downloaded_count: Number(result.downloaded_count ?? 0),
        parsed_count: Number(result.parsed_count ?? 0),
        skipped_no_pdf_count: Number(result.skipped_no_pdf_count ?? 0),
        failed_count: Number(result.failed_count ?? 0),
        evidence_count: Number(result.evidence_count ?? 0),
        draft_id: String(result.draft_id ?? ""),
        revised_draft_id: String(result.revised_draft_id ?? ""),
        review_issue_count: Number(result.review_issue_count ?? 0),
        critical_issue_count: Number(result.critical_issue_count ?? 0),
        publication_prepared: Boolean(result.publication_prepared ?? false),
        quality_gate:
          typeof result.quality_gate === "object" && result.quality_gate
            ? (result.quality_gate as Record<string, unknown>)
            : {},
        export_files:
          typeof result.export_files === "object" && result.export_files
            ? (result.export_files as Record<string, string>)
            : {}
      };
      workflowHint.value = "全自动流程完成。";
    } else if (task.value?.status === "failed") {
      const failResult = task.value?.result ?? {};
      const failCode = typeof failResult.code === "string" ? failResult.code : "";
      const failMessage = typeof failResult.message === "string" ? failResult.message : "";
      workflowHint.value = failMessage
        ? `${failCode ? `[${failCode}] ` : ""}${failMessage}`
        : "全自动流程失败，请查看任务日志与失败详情。";
    }
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "全自动流程失败";
  } finally {
    busy.value = false;
  }
}

onMounted(async () => {
  try {
    await loadAll();
    const storedTaskId = localStorage.getItem(TASK_STORAGE_KEY(projectId.value));
    if (storedTaskId && !task.value) {
      busy.value = true;
      workflowHint.value = `恢复任务 ${storedTaskId.slice(0, 8)} 轮询...`;
      try {
        await pollTask(storedTaskId);
      } catch (err) {
        error.value = err instanceof Error ? err.message : "恢复任务失败";
      } finally {
        busy.value = false;
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  }
});
</script>

<template>
  <section v-if="project" class="page">
    <!-- ── Hero ── -->
    <header class="hero">
      <div class="hero-content">
        <button class="back-link" type="button" @click="router.push('/')">
          <span>&larr;</span> 项目列表
        </button>
        <h1>{{ project.title }}</h1>
        <p class="rq">{{ project.research_question }}</p>
        <div class="hero-tags">
          <span class="tag">{{ project.article_type }}</span>
          <span class="tag">{{ project.language }}</span>
          <span class="tag">~{{ project.target_words.toLocaleString() }} 字</span>
          <span v-if="project.citation_style" class="tag">{{ project.citation_style }}</span>
        </div>
        <RouterLink :to="`/projects/${projectId}/chat`" class="chat-entry-btn">
          进入对话模式
        </RouterLink>
      </div>
    </header>

    <!-- ── Stats ── -->
    <div class="stats-row">
      <div class="stat-chip">
        <strong>{{ papers.length }}</strong>
        <span>论文</span>
      </div>
      <div class="stat-chip">
        <strong>{{ evidence.length }}</strong>
        <span>证据卡</span>
      </div>
      <div class="stat-chip">
        <strong>{{ drafts.length }}</strong>
        <span>草稿</span>
      </div>
      <div class="stat-chip">
        <strong>{{ issues.length }}</strong>
        <span>审查问题</span>
      </div>
      <div v-if="tokenUsage && tokenUsage.total_tokens > 0" class="stat-chip">
        <strong>{{ fmtTokens(tokenUsage.total_tokens) }}</strong>
        <span>Token 消耗</span>
      </div>
    </div>

    <!-- ── Pipeline Visualization ── -->
    <section class="card pipeline-card">
      <div class="pipeline-header">
        <h2>工作流</h2>
        <span v-if="workflowHint" class="pipeline-hint">{{ workflowHint }}</span>
        <span v-else class="pipeline-hint">点击下方按钮启动流程</span>
      </div>

      <div class="pipeline">
        <div
          v-for="(stage, idx) in workflowStages"
          :key="stage.label"
          class="pipe-node"
          :class="{
            done: idx < currentStageIndex,
            active: idx === currentStageIndex
          }"
        >
          <div class="pipe-dot" />
          <div class="pipe-content">
            <strong>{{ stage.label }}</strong>
            <small>{{ stage.desc }}</small>
          </div>
        </div>
      </div>
    </section>

    <!-- ── Actions ── -->
    <section class="card actions-card">
      <h2>操作</h2>
      <div class="action-grid">
        <button class="primary-action" :disabled="busy" type="button" @click="runAutoWorkflow">
          一键全自动
        </button>
        <div class="step-actions">
          <button :disabled="busy" type="button" @click="quickSearch">检索论文</button>
          <button :disabled="busy" type="button" @click="quickBuildEvidence">构建证据</button>
          <button :disabled="busy" type="button" @click="quickGenerateDraft">生成草稿</button>
          <button :disabled="busy || drafts.length === 0" type="button" @click="quickReview">审查草稿</button>
          <button :disabled="busy || drafts.length === 0" type="button" @click="quickRevise">生成修订</button>
        </div>
      </div>
    </section>

    <!-- ── Task Progress ── -->
    <TaskProgress :task="task" />

    <!-- ── Auto Workflow Result ── -->
    <section v-if="autoWorkflow" class="card result-card">
      <h2>本次自动执行结果</h2>

      <div class="result-grid">
        <div class="result-item">
          <span class="result-label">已选论文</span>
          <strong>{{ autoWorkflow.selected_count }}</strong>
          <small>（自动 {{ autoWorkflow.auto_selected_count }}）</small>
        </div>
        <div class="result-item">
          <span class="result-label">复用本地</span>
          <strong>{{ autoWorkflow.reused_local_pdf_count }}</strong>
        </div>
        <div class="result-item">
          <span class="result-label">下载</span>
          <strong>{{ autoWorkflow.downloaded_count }}</strong>
        </div>
        <div class="result-item">
          <span class="result-label">解析</span>
          <strong>{{ autoWorkflow.parsed_count }}</strong>
        </div>
        <div class="result-item">
          <span class="result-label">证据卡</span>
          <strong>{{ autoWorkflow.evidence_count }}</strong>
        </div>
        <div class="result-item">
          <span class="result-label">审查问题</span>
          <strong>{{ autoWorkflow.review_issue_count }}</strong>
          <small>（critical {{ autoWorkflow.critical_issue_count }}）</small>
        </div>
      </div>

      <div class="result-status" :class="autoWorkflow.publication_prepared ? 'pass' : 'warn'">
        <strong>{{ autoWorkflow.publication_prepared ? "已达终稿门禁" : "未达终稿门禁" }}</strong>
      </div>

      <p v-if="Object.keys(autoWorkflow.export_files).length > 0" class="export-hint">
        导出文件已写入 backend/data/exports/
      </p>

      <button type="button" class="ghost-btn" @click="router.push(`/projects/${projectId}/final`)">
        查看终稿 &rarr;
      </button>
    </section>

    <!-- ── Token 用量 ── -->
    <section v-if="tokenUsage && tokenUsage.total_calls > 0" class="card usage-card">
      <h2>Token 消耗</h2>

      <div class="usage-summary">
        <div class="usage-stat">
          <strong>{{ fmtTokens(tokenUsage.total_tokens) }}</strong>
          <span>总 Token</span>
        </div>
        <div class="usage-stat">
          <strong>{{ fmtTokens(tokenUsage.prompt_tokens) }}</strong>
          <span>输入（prompt）</span>
        </div>
        <div class="usage-stat">
          <strong>{{ fmtTokens(tokenUsage.completion_tokens) }}</strong>
          <span>输出（completion）</span>
        </div>
        <div class="usage-stat">
          <strong>{{ tokenUsage.total_calls }}</strong>
          <span>调用次数</span>
        </div>
        <div class="usage-stat">
          <strong>{{ fmtDuration(tokenUsage.total_latency_ms) }}</strong>
          <span>累计耗时</span>
        </div>
      </div>

      <h3 v-if="tokenUsage.by_model.length > 0">按模型</h3>
      <table v-if="tokenUsage.by_model.length > 0" class="usage-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>调用</th>
            <th>输入</th>
            <th>输出</th>
            <th>合计</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in tokenUsage.by_model" :key="`${m.provider}/${m.model}`">
            <td class="usage-model">
              {{ m.model }}<small v-if="m.provider">{{ m.provider }}</small>
            </td>
            <td>{{ m.calls }}</td>
            <td>{{ fmtTokens(m.prompt_tokens) }}</td>
            <td>{{ fmtTokens(m.completion_tokens) }}</td>
            <td>{{ fmtTokens(m.total_tokens) }}</td>
          </tr>
        </tbody>
      </table>

      <h3 v-if="tokenUsage.by_task.length > 0">按运行批次</h3>
      <table v-if="tokenUsage.by_task.length > 0" class="usage-table">
        <thead>
          <tr>
            <th>批次</th>
            <th>调用</th>
            <th>合计 Token</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, idx) in tokenUsage.by_task" :key="t.task_id ?? `no-task-${idx}`">
            <td class="usage-task">
              {{ t.task_id ? `运行 ${t.task_id.slice(0, 8)}` : "未归属调用" }}
            </td>
            <td>{{ t.calls }}</td>
            <td>{{ fmtTokens(t.total_tokens) }}</td>
            <td class="usage-time">
              {{ fmtTime(t.first_call_at) }}<template v-if="t.last_call_at && t.first_call_at !== t.last_call_at"> ~ {{ fmtTime(t.last_call_at) }}</template>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <p v-if="error" class="error-toast">{{ error }}</p>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
  max-width: 1100px;
}

/* ── Hero ── */
.hero {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(240px 120px at 95% -5%, rgba(240, 210, 150, 0.35) 0%, transparent 70%),
    var(--surface);
  padding: 1.3rem;
  animation: rise-in 280ms cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: var(--shadow-sm);
}

.back-link {
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  font-size: 0.86rem;
  padding: 0;
  margin-bottom: 0.6rem;
  display: flex;
  align-items: center;
  gap: 0.3rem;
  transition: color 160ms ease;
}

.back-link:hover {
  color: var(--accent);
}

.hero h1 {
  margin: 0;
  font: 700 1.7rem/1.2 var(--font-display);
  letter-spacing: 0.01em;
}

.rq {
  margin: 0.5rem 0 0;
  color: var(--muted);
  line-height: 1.55;
}

.hero-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-top: 0.7rem;
}

.tag {
  font-size: 0.76rem;
  color: var(--muted);
  background: rgba(21, 29, 46, 0.04);
  border: 1px solid var(--line-soft);
  border-radius: 6px;
  padding: 0.15rem 0.5rem;
}

.chat-entry-btn {
  display: inline-block;
  margin-top: 0.8rem;
  padding: 0.55rem 1.2rem;
  border: 0;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, var(--accent) 0%, #a06a18 100%);
  color: #fff;
  font-weight: 600;
  font-size: 0.92rem;
  text-decoration: none;
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease;
}

.chat-entry-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(13, 124, 117, 0.25);
}

/* ── Stats ── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.65rem;
}

.stat-chip {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-strong);
  padding: 0.75rem 0.85rem;
  display: grid;
  gap: 2px;
  box-shadow: var(--shadow-sm);
  animation: rise-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

.stat-chip strong {
  font: 700 1.5rem/1 var(--font-display);
  color: var(--ink);
}

.stat-chip span {
  font-size: 0.82rem;
  color: var(--muted);
}

/* ── Card base ── */
.card {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 1.1rem;
  animation: rise-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
  box-shadow: var(--shadow-sm);
}

/* ── Pipeline ── */
.pipeline-card {
  background:
    radial-gradient(200px 100px at 0% 0%, rgba(195, 230, 222, 0.4) 0%, transparent 70%),
    var(--surface);
}

.pipeline-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.9rem;
}

.pipeline-header h2 {
  margin: 0;
  font-size: 1rem;
}

.pipeline-hint {
  font-size: 0.84rem;
  color: var(--muted-soft);
}

.pipeline {
  display: flex;
  gap: 0;
  overflow-x: auto;
  padding-bottom: 0.3rem;
}

.pipe-node {
  flex: 1;
  min-width: 90px;
  display: grid;
  grid-template-rows: auto auto;
  gap: 0.4rem;
  align-items: center;
  justify-items: center;
  position: relative;
  padding: 0.6rem 0.3rem;
  border-radius: var(--radius-sm);
  transition: all 200ms ease;
}

.pipe-node::after {
  content: "";
  position: absolute;
  top: 18px;
  left: calc(50% + 12px);
  right: calc(-50% + 12px);
  height: 2px;
  background: var(--line);
  transition: background 200ms ease;
}

.pipe-node:last-child::after {
  display: none;
}

.pipe-dot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--line-soft);
  border: 2px solid var(--line);
  transition: all 200ms ease;
  z-index: 1;
}

.pipe-content {
  text-align: center;
  display: grid;
  gap: 1px;
}

.pipe-content strong {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--muted);
}

.pipe-content small {
  font-size: 0.7rem;
  color: var(--muted-soft);
}

.pipe-node.done .pipe-dot {
  background: var(--accent);
  border-color: var(--accent);
}

.pipe-node.done .pipe-content strong {
  color: var(--accent-strong);
}

.pipe-node.done::after {
  background: var(--accent-muted);
}

.pipe-node.active .pipe-dot {
  background: var(--signal);
  border-color: var(--signal);
  box-shadow: 0 0 0 4px rgba(181, 121, 31, 0.15);
  animation: pulse-soft 2s ease-in-out infinite;
}

.pipe-node.active .pipe-content strong {
  color: var(--signal);
}

.pipe-node.active {
  background: rgba(181, 121, 31, 0.04);
}

/* ── Actions ── */
.actions-card {
  display: grid;
  gap: 0.7rem;
}

.actions-card h2 {
  margin: 0;
  font-size: 1rem;
}

.action-grid {
  display: grid;
  gap: 0.6rem;
}

.primary-action {
  border: 0;
  border-radius: var(--radius-sm);
  padding: 0.65rem 1rem;
  color: #fff;
  font-weight: 600;
  font-size: 0.95rem;
  background: linear-gradient(135deg, var(--accent) 0%, #a06a18 100%);
  cursor: pointer;
  transition: transform 160ms ease, box-shadow 160ms ease;
}

.primary-action:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(13, 124, 117, 0.25);
}

.step-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.step-actions button {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-strong);
  color: var(--ink-soft);
  padding: 0.45rem 0.72rem;
  cursor: pointer;
  font-size: 0.86rem;
  transition: all 160ms ease;
}

.step-actions button:hover:not(:disabled) {
  border-color: var(--accent-muted);
  background: var(--accent-light);
}

button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── Result ── */
.result-card {
  display: grid;
  gap: 0.7rem;
}

.result-card h2 {
  margin: 0;
  font-size: 1rem;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 0.55rem;
}

.result-item {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.7rem;
  background: var(--surface-strong);
  display: grid;
  gap: 2px;
}

.result-label {
  font-size: 0.76rem;
  color: var(--muted-soft);
}

.result-item strong {
  font-size: 1.1rem;
  font-family: var(--font-display);
}

.result-item small {
  font-size: 0.76rem;
  color: var(--muted);
}

.result-status {
  border: 1px solid;
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.7rem;
}

.result-status.pass {
  border-color: rgba(26, 122, 76, 0.35);
  background: var(--success-light);
  color: var(--success);
}

.result-status.warn {
  border-color: rgba(181, 121, 31, 0.35);
  background: var(--signal-light);
  color: var(--signal);
}

.export-hint {
  margin: 0;
  font-size: 0.84rem;
  color: var(--muted);
}

.ghost-btn {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--surface-strong);
  color: var(--accent);
  padding: 0.5rem 0.8rem;
  cursor: pointer;
  font-size: 0.9rem;
  transition: all 160ms ease;
}

.ghost-btn:hover {
  border-color: var(--accent-muted);
  background: var(--accent-light);
}

.error-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  padding: 0.7rem 1.1rem;
  border-radius: var(--radius-md);
  background: var(--danger-light);
  color: var(--danger);
  border: 1px solid rgba(180, 35, 24, 0.2);
  font-size: 0.9rem;
  z-index: 200;
  animation: rise-in 200ms ease;
}

/* ── Token 用量 ── */
.usage-card h3 {
  margin: 1rem 0 0.4rem;
  font-size: 0.92rem;
  color: var(--muted);
}

.usage-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 0.6rem;
}

.usage-stat {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.6rem 0.8rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
}

.usage-stat strong {
  font-size: 1.15rem;
  font-variant-numeric: tabular-nums;
}

.usage-stat span {
  font-size: 0.74rem;
  color: var(--muted);
}

.usage-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.86rem;
  font-variant-numeric: tabular-nums;
}

.usage-table th {
  text-align: left;
  font-weight: 500;
  color: var(--muted);
  font-size: 0.76rem;
  padding: 0.35rem 0.5rem;
  border-bottom: 1px solid var(--line);
}

.usage-table td {
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--line);
}

.usage-table tbody tr:last-child td {
  border-bottom: none;
}

.usage-model {
  display: flex;
  flex-direction: column;
}

.usage-model small,
.usage-task small {
  color: var(--muted);
  font-size: 0.72rem;
}

.usage-time {
  color: var(--muted);
  font-size: 0.8rem;
}

@media (max-width: 950px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }

  .pipeline {
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .pipe-node {
    min-width: 80px;
  }

  .pipe-node::after {
    display: none;
  }
}

@media (max-width: 640px) {
  .stats-row {
    grid-template-columns: 1fr 1fr;
  }

  .step-actions {
    flex-direction: column;
  }
}
</style>
