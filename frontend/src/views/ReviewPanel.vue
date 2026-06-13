<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { apiRequest, getApiBase } from "../api";
import TaskProgress from "../components/TaskProgress.vue";
import type { Draft, ReviewIssue, TaskPayload } from "../types";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId));

const drafts = ref<Draft[]>([]);
const issues = ref<ReviewIssue[]>([]);
const selectedDraftId = ref("");
const task = ref<TaskPayload | null>(null);
const error = ref("");

const selectedDraft = computed(() => drafts.value.find((draft) => draft.id === selectedDraftId.value) ?? null);

async function load() {
  drafts.value = await apiRequest<Draft[]>(`/api/projects/${projectId.value}/drafts`);
  issues.value = await apiRequest<ReviewIssue[]>(`/api/projects/${projectId.value}/review-issues`);
  if (!selectedDraftId.value && drafts.value[0]) selectedDraftId.value = drafts.value[0].id;
}

async function pollTask(taskId: string) {
  task.value = await apiRequest<TaskPayload>(`/api/tasks/${taskId}`);
}

async function runReview() {
  if (!selectedDraftId.value) return;
  const payload = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/review-draft`, {
    method: "POST",
    body: JSON.stringify({ draft_id: selectedDraftId.value })
  });
  await pollTask(payload.task_id);
  await load();
}

async function runRevision() {
  if (!selectedDraftId.value) return;
  const payload = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/revise-draft`, {
    method: "POST",
    body: JSON.stringify({ draft_id: selectedDraftId.value })
  });
  await pollTask(payload.task_id);
  await load();
}

function openExport(path: string) {
  window.open(`${getApiBase()}${path}`, "_blank");
}

onMounted(async () => {
  try {
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  }
});
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>审查面板</h1>
      <p>执行审查、生成修订稿，并导出投稿文件。</p>
    </header>

    <section class="controls">
      <label>
        目标草稿
        <select v-model="selectedDraftId">
          <option v-for="draft in drafts" :key="draft.id" :value="draft.id">
            v{{ draft.version }} · {{ draft.status }}
          </option>
        </select>
      </label>

      <button type="button" @click="runReview">执行审查</button>
      <button type="button" @click="runRevision">生成修订稿</button>
    </section>

    <TaskProgress :task="task" />

    <section class="exports">
      <h2>导出</h2>
      <div class="export-grid">
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/markdown`)">Markdown</button>
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/docx`)">DOCX</button>
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/pdf`)">PDF</button>
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/bibtex`)">BibTeX</button>
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/evidence-map`)">Evidence Map</button>
        <button type="button" @click="openExport(`/api/projects/${projectId}/export/review-report`)">
          Review Report
        </button>
      </div>
    </section>

    <section class="issues">
      <h2>审查问题</h2>
      <p v-if="issues.length === 0">暂无问题。</p>
      <article v-for="issue in issues" :key="issue.id" class="issue">
        <header>
          <strong>{{ issue.severity.toUpperCase() }} · {{ issue.issue_type }}</strong>
          <span>{{ issue.location || "global" }}</span>
        </header>
        <p>{{ issue.description }}</p>
        <p class="hint">{{ issue.suggestion || "No suggestion" }}</p>
      </article>
    </section>

    <p v-if="selectedDraft" class="hint">当前草稿：v{{ selectedDraft.version }} · {{ selectedDraft.status }}</p>
    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
  max-width: 1100px;
}

.head h1 {
  margin: 0;
  font-family: var(--font-display);
}

.head p {
  margin: 0.38rem 0 0;
  color: var(--muted);
}

.controls,
.exports,
.issues {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  padding: 0.8rem;
  box-shadow: var(--shadow-sm);
}

.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: end;
}

label {
  display: grid;
  gap: 0.3rem;
}

select {
  border: 1px solid #bfd0e5;
  border-radius: var(--radius-sm);
  padding: 0.45rem 0.62rem;
}

button {
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: #fff;
  padding: 0.48rem 0.72rem;
  cursor: pointer;
}

.export-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 0.6rem;
}

.export-grid button {
  background: var(--surface-strong);
  border: 1px solid var(--line);
  color: var(--ink-soft);
}

.export-grid button:hover {
  border-color: var(--accent-muted);
}

.issues .issue {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  padding: 0.62rem;
  margin-bottom: 0.58rem;
}

.issue header {
  display: flex;
  justify-content: space-between;
  gap: 0.6rem;
}

.issue header strong {
  color: var(--ink);
}

.issue header span {
  background: rgba(21, 29, 46, 0.04);
  border-radius: 4px;
  padding: 0.1rem 0.35rem;
  font-size: 0.82rem;
}

.issue p {
  margin: 0.35rem 0 0;
}

.hint {
  color: var(--muted);
}

.error {
  color: var(--danger);
}
</style>
