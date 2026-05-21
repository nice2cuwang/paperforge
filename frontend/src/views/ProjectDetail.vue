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
const error = ref("");
const busy = ref(false);
const workflowHint = ref("");

const workflowStages = [
  { threshold: 5, label: "检索论文", desc: "拉取候选并去重" },
  { threshold: 30, label: "下载与解析", desc: "下载或复用 PDF 并切块" },
  { threshold: 66, label: "构建证据", desc: "生成可追溯证据卡" },
  { threshold: 78, label: "生成草稿", desc: "基于证据自动写作" },
  { threshold: 86, label: "审查草稿", desc: "识别引用与逻辑问题" },
  { threshold: 93, label: "自动修订", desc: "生成修订稿" },
  { threshold: 97, label: "导出文件", desc: "导出 markdown/docx/pdf/bib" }
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
    <header class="hero card">
      <div>
        <h1>{{ project.title }}</h1>
        <p>{{ project.research_question }}</p>
      </div>
      <button class="ghost" type="button" @click="router.push('/')">返回项目列表</button>
    </header>

    <section class="stats">
      <article class="stat card">
        <strong>{{ papers.length }}</strong>
        <span>论文候选</span>
      </article>
      <article class="stat card">
        <strong>{{ evidence.length }}</strong>
        <span>证据卡</span>
      </article>
      <article class="stat card">
        <strong>{{ drafts.length }}</strong>
        <span>草稿版本</span>
      </article>
      <article class="stat card">
        <strong>{{ issues.length }}</strong>
        <span>审查问题</span>
      </article>
    </section>

    <section class="control card">
      <h2>自动工作流控制台</h2>
      <div class="buttons">
        <button class="primary" :disabled="busy" type="button" @click="runAutoWorkflow">一键全自动</button>
        <button :disabled="busy" type="button" @click="quickSearch">检索论文</button>
        <button :disabled="busy" type="button" @click="quickBuildEvidence">构建证据</button>
        <button :disabled="busy" type="button" @click="quickGenerateDraft">生成草稿</button>
        <button :disabled="busy || drafts.length === 0" type="button" @click="quickReview">审查草稿</button>
        <button :disabled="busy || drafts.length === 0" type="button" @click="quickRevise">生成修订</button>
      </div>
    </section>

    <section class="workflow card">
      <div class="head">
        <h2>流程可视化</h2>
        <span>{{ workflowHint || "点击一键全自动后，系统会实时显示阶段与日志。" }}</span>
      </div>
      <ol class="stage-list">
        <li
          v-for="(stage, idx) in workflowStages"
          :key="stage.label"
          :class="{
            done: idx < currentStageIndex,
            active: idx === currentStageIndex
          }"
        >
          <b>{{ idx + 1 }}. {{ stage.label }}</b>
          <small>{{ stage.desc }}</small>
        </li>
      </ol>
    </section>

    <TaskProgress :task="task" />

    <section v-if="autoWorkflow" class="result card">
      <h2>本次自动执行结果</h2>
      <p>
        已选 {{ autoWorkflow.selected_count }} 篇（自动纳入 {{ autoWorkflow.auto_selected_count }}），复用本地
        {{ autoWorkflow.reused_local_pdf_count }}，二次回退命中 {{ autoWorkflow.resolved_via_fallback_count }}，下载
        {{ autoWorkflow.downloaded_count }}，解析 {{ autoWorkflow.parsed_count }}，无 PDF 跳过
        {{ autoWorkflow.skipped_no_pdf_count }}。
      </p>
      <p>
        证据卡 {{ autoWorkflow.evidence_count }}，审查问题 {{ autoWorkflow.review_issue_count }}（critical
        {{ autoWorkflow.critical_issue_count }}）。
      </p>
      <p class="hint">
        publication_prepared: {{ autoWorkflow.publication_prepared ? "true" : "false" }}
      </p>
      <p v-if="Object.keys(autoWorkflow.export_files).length > 0" class="hint">
        导出文件已写入 `backend/data/exports/{{ projectId }}`
      </p>
      <button type="button" class="ghost" @click="router.push(`/projects/${projectId}/final`)">前端查看终稿</button>
    </section>

    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
}

.card {
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--surface);
  padding: 1rem;
  animation: rise-in 260ms ease;
}

.hero {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  background:
    radial-gradient(260px 130px at 95% -10%, rgba(245, 214, 158, 0.55) 0%, transparent 70%),
    var(--surface);
}

.hero h1 {
  margin: 0;
  font: 700 1.82rem/1.2 "Space Grotesk", "Noto Sans SC", sans-serif;
}

.hero p {
  margin: 0.56rem 0 0;
  color: #3a4c67;
}

.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.7rem;
}

.stat {
  background: var(--surface-strong);
  display: grid;
  gap: 0.2rem;
  padding: 0.86rem;
}

.stat strong {
  font-size: 1.46rem;
}

.stat span {
  color: #576583;
}

.control {
  display: grid;
  gap: 0.7rem;
}

.control h2,
.workflow h2,
.result h2 {
  margin: 0;
  font-size: 1.04rem;
}

.buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.56rem;
}

button {
  border: 0;
  border-radius: 11px;
  background: #e4edf8;
  color: #173656;
  padding: 0.5rem 0.76rem;
  cursor: pointer;
}

button.primary {
  color: #fff;
  background: linear-gradient(90deg, #0f7f78 0%, #c07817 100%);
}

button.ghost {
  color: #1f4568;
  background: #deecff;
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.workflow {
  display: grid;
  gap: 0.7rem;
  background:
    radial-gradient(240px 120px at 0% 0%, rgba(201, 233, 230, 0.62) 0%, transparent 70%),
    var(--surface);
}

.head {
  display: grid;
  gap: 0.2rem;
}

.head span {
  color: #506079;
  font-size: 0.92rem;
}

.stage-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.45rem;
}

.stage-list li {
  border: 1px solid #d9dfeb;
  border-radius: 12px;
  padding: 0.5rem 0.62rem;
  display: grid;
}

.stage-list b {
  font-size: 0.93rem;
}

.stage-list small {
  color: #55637e;
}

.stage-list li.done {
  border-color: rgba(19, 121, 99, 0.42);
  background: #ebf8f1;
}

.stage-list li.active {
  border-color: rgba(195, 129, 28, 0.45);
  background: #fff2de;
}

.result p {
  margin: 0.42rem 0;
}

.hint {
  color: #3b526f;
}

.error {
  color: var(--danger);
}

@media (max-width: 950px) {
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 640px) {
  .stats {
    grid-template-columns: 1fr;
  }
}
</style>
