<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { marked } from "marked";
import DOMPurify from "dompurify";

import { apiRequest } from "../api";
import TaskProgress from "../components/TaskProgress.vue";
import type { Draft, TaskPayload } from "../types";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId));

const drafts = ref<Draft[]>([]);
const activeDraftId = ref("");
const draftContent = ref("");
const task = ref<TaskPayload | null>(null);
const error = ref("");

const activeDraft = computed(() => drafts.value.find((item) => item.id === activeDraftId.value) ?? null);
const previewMode = ref<"edit" | "preview">("edit");

const renderedHtml = computed(() => {
  const raw = marked.parse(draftContent.value, { breaks: true, gfm: true }) as string;
  return DOMPurify.sanitize(raw);
});

async function loadDrafts() {
  drafts.value = await apiRequest<Draft[]>(`/api/projects/${projectId.value}/drafts`);
  if (!activeDraftId.value && drafts.value[0]) {
    activeDraftId.value = drafts.value[0].id;
  }
  if (activeDraft.value) {
    draftContent.value = activeDraft.value.content_md;
  }
}

async function refreshTask(taskId: string) {
  task.value = await apiRequest<TaskPayload>(`/api/tasks/${taskId}`);
}

async function generateOutline() {
  const result = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/generate-outline`, {
    method: "POST",
    body: JSON.stringify({ force: false })
  });
  await refreshTask(result.task_id);
  await loadDrafts();
}

async function generateDraft() {
  const result = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/generate-draft`, {
    method: "POST",
    body: JSON.stringify({ title: "Draft Generated From Evidence" })
  });
  await refreshTask(result.task_id);
  await loadDrafts();
}

async function saveDraft() {
  if (!activeDraft.value) return;
  const updated = await apiRequest<Draft>(`/api/drafts/${activeDraft.value.id}`, {
    method: "PATCH",
    body: JSON.stringify({ content_md: draftContent.value })
  });
  drafts.value = drafts.value.map((item) => (item.id === updated.id ? updated : item));
}

onMounted(async () => {
  try {
    await loadDrafts();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  }
});
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>草稿编辑</h1>
      <p>每个版本都会保留，可对照审查问题逐步修订。</p>
    </header>

    <section class="toolbar">
      <button type="button" @click="generateOutline">生成大纲草稿</button>
      <button type="button" @click="generateDraft">生成正文草稿</button>
      <button type="button" class="ghost" @click="loadDrafts">刷新</button>
    </section>

    <TaskProgress :task="task" />

    <section class="editor">
      <aside class="versions">
        <h2>版本</h2>
        <button
          v-for="draft in drafts"
          :key="draft.id"
          type="button"
          class="version-item"
          :class="{ active: draft.id === activeDraftId }"
          @click="
            activeDraftId = draft.id;
            draftContent = draft.content_md;
          "
        >
          v{{ draft.version }} · {{ draft.status }}
        </button>
      </aside>

      <div class="editor-main">
        <div class="editor-tabs">
          <h2>{{ activeDraft?.title || "未选择草稿" }}</h2>
          <div class="tabs">
            <button type="button" :class="{ active: previewMode === 'edit' }" @click="previewMode = 'edit'">编辑</button>
            <button type="button" :class="{ active: previewMode === 'preview' }" @click="previewMode = 'preview'">预览</button>
          </div>
        </div>
        <textarea v-if="previewMode === 'edit'" v-model="draftContent" rows="24" />
        <div v-else class="md-preview" v-html="renderedHtml" />
        <button type="button" :disabled="!activeDraft" @click="saveDraft">保存当前草稿</button>
      </div>
    </section>

    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
}

.head h1 {
  margin: 0;
}

.head p {
  margin: 0.36rem 0 0;
  color: var(--muted);
}

.toolbar {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 0.8rem;
  display: flex;
  gap: 0.6rem;
}

.editor {
  display: grid;
  gap: 0.9rem;
  grid-template-columns: 260px 1fr;
}

.versions,
.editor-main {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 0.8rem;
}

.versions {
  display: grid;
  gap: 0.5rem;
  align-content: start;
}

.version-item {
  text-align: left;
  border: 1px solid #c9d8ed;
  background: #f7fbff;
  color: #194774;
}

.version-item.active {
  border-color: #0b6bce;
  background: #e8f2ff;
}

.editor-tabs {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.6rem;
  margin-bottom: 0.4rem;
}

.editor-tabs h2 {
  margin: 0;
}

.tabs {
  display: flex;
  gap: 0.3rem;
}

.tabs button {
  background: #e8edf5;
  color: #3a4c67;
  font-size: 0.86rem;
  padding: 0.4rem 0.65rem;
}

.tabs button.active {
  background: #0b6dd6;
  color: #fff;
}

.md-preview {
  min-height: 360px;
  padding: 0.8rem;
  border-radius: 12px;
  border: 1px solid #bfd0e5;
  background: #fff;
  line-height: 1.75;
  color: #1f2937;
  overflow: auto;
}

.md-preview :deep(h1) { font-size: 1.5rem; margin: 0.7rem 0 0.35rem; }
.md-preview :deep(h2) { font-size: 1.25rem; margin: 0.6rem 0 0.3rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.2rem; }
.md-preview :deep(h3) { font-size: 1.05rem; margin: 0.5rem 0 0.25rem; }
.md-preview :deep(p) { margin: 0.45rem 0; }
.md-preview :deep(ul), .md-preview :deep(ol) { margin: 0.45rem 0; padding-left: 1.4rem; }
.md-preview :deep(li) { margin: 0.15rem 0; }
.md-preview :deep(blockquote) { margin: 0.45rem 0; padding-left: 0.7rem; border-left: 3px solid #0b6dd6; color: #4b5563; }
.md-preview :deep(pre) { background: #f6f8fb; padding: 0.6rem; border-radius: 8px; overflow-x: auto; }
.md-preview :deep(code) { background: #f0f2f6; padding: 0.12rem 0.25rem; border-radius: 4px; font-size: 0.88em; }
.md-preview :deep(a) { color: #0b6dd6; }
.md-preview :deep(table) { border-collapse: collapse; margin: 0.45rem 0; width: 100%; }
.md-preview :deep(th), .md-preview :deep(td) { border: 1px solid #d6deec; padding: 0.35rem 0.5rem; }
.md-preview :deep(th) { background: #f6f8fb; }

textarea {
  width: 100%;
  border: 1px solid #bfd0e5;
  border-radius: 12px;
  padding: 0.7rem;
  font: 400 0.94rem/1.5 "JetBrains Mono", "Consolas", monospace;
  resize: vertical;
}

button {
  border: 0;
  border-radius: 10px;
  background: #0b6dd6;
  color: #fff;
  padding: 0.52rem 0.8rem;
  cursor: pointer;
}

button.ghost {
  background: #deecfb;
  color: #1f416a;
}

.error {
  color: #b42318;
}

@media (max-width: 980px) {
  .editor {
    grid-template-columns: 1fr;
  }
}
</style>
