<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { marked } from "marked";
import DOMPurify from "dompurify";

import { apiRequest } from "../api";
import type { Draft } from "../types";

const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.projectId));

const drafts = ref<Draft[]>([]);
const activeDraftId = ref("");
const loading = ref(false);
const error = ref("");

const activeDraft = computed(() => drafts.value.find((item) => item.id === activeDraftId.value) ?? null);
const viewMode = ref<"source" | "preview">("preview");

const renderedHtml = computed(() => {
  const md = activeDraft.value?.content_md || "";
  const raw = marked.parse(md, { breaks: true, gfm: true }) as string;
  return DOMPurify.sanitize(raw);
});

const qualityGate = computed(() => {
  const payload = activeDraft.value?.quality_score;
  if (!payload || typeof payload !== "object") return null;
  return payload as Record<string, unknown>;
});

const isPublicationPrepared = computed(() => {
  const flag = qualityGate.value?.publication_prepared;
  return flag === true;
});

async function loadDrafts() {
  loading.value = true;
  error.value = "";
  try {
    drafts.value = await apiRequest<Draft[]>(`/api/projects/${projectId.value}/drafts`);
    if (drafts.value.length > 0 && !activeDraftId.value) {
      activeDraftId.value = drafts.value[0].id;
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载终稿失败";
  } finally {
    loading.value = false;
  }
}

onMounted(loadDrafts);
</script>

<template>
  <section class="page">
    <header class="card head">
      <div>
        <h1>终稿查看</h1>
        <p>仅当 publication_prepared=true 时，才视为“出版准备级终稿”。</p>
      </div>
      <div class="ops">
        <button type="button" class="ghost" @click="loadDrafts">刷新</button>
        <button type="button" @click="router.push(`/projects/${projectId}/drafts`)">进入草稿编辑</button>
      </div>
    </header>

    <section class="card picker">
      <label>
        选择版本
        <select v-model="activeDraftId" :disabled="loading || drafts.length === 0">
          <option v-for="item in drafts" :key="item.id" :value="item.id">
            v{{ item.version }} · {{ item.status }} · {{ item.title || "untitled" }}
          </option>
        </select>
      </label>
    </section>

    <section v-if="activeDraft" class="card viewer">
      <div class="meta">
        <span>版本 v{{ activeDraft.version }}</span>
        <span>状态 {{ activeDraft.status }}</span>
        <span>创建时间 {{ new Date(activeDraft.created_at).toLocaleString() }}</span>
      </div>

      <div class="gate" :class="isPublicationPrepared ? 'pass' : 'warn'">
        <strong>{{ isPublicationPrepared ? "已达终稿门禁" : "未达终稿门禁" }}</strong>
        <span v-if="!isPublicationPrepared">请先修复审查问题并提升质量指标。</span>
      </div>

      <ul v-if="qualityGate" class="gate-metrics">
        <li>publication_prepared: {{ String(qualityGate.publication_prepared ?? false) }}</li>
        <li>critical_issues: {{ qualityGate.critical_issues ?? "n/a" }}</li>
        <li>unsupported_claims: {{ qualityGate.unsupported_claims ?? "n/a" }}</li>
        <li>unresolved_citations: {{ qualityGate.unresolved_citations ?? "n/a" }}</li>
        <li>evidence_coverage: {{ qualityGate.evidence_coverage ?? "n/a" }}</li>
        <li>citation_validity: {{ qualityGate.citation_validity ?? "n/a" }}</li>
        <li>logic_score: {{ qualityGate.logic_score ?? "n/a" }}</li>
        <li>style_score: {{ qualityGate.style_score ?? "n/a" }}</li>
      </ul>

      <div class="viewer-tabs">
        <button type="button" :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'">预览</button>
        <button type="button" :class="{ active: viewMode === 'source' }" @click="viewMode = 'source'">源码</button>
      </div>

      <h2>{{ activeDraft.title || "未命名文稿" }}</h2>
      <div v-if="viewMode === 'preview'" class="md-preview" v-html="renderedHtml" />
      <pre v-else>{{ activeDraft.content_md }}</pre>
    </section>

    <section v-else class="card empty">
      <p v-if="loading">正在加载...</p>
      <p v-else>当前项目还没有可查看的文稿，请先生成草稿。</p>
    </section>

    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.page {
  display: grid;
  gap: 1rem;
  max-width: 1100px;
}

.card {
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  padding: 0.9rem;
  box-shadow: var(--shadow-sm);
}

.head {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: flex-start;
}

.head h1 {
  margin: 0;
  font-family: var(--font-display);
}

.head p {
  margin: 0.35rem 0 0;
  color: var(--muted);
}

.ops {
  display: flex;
  gap: 0.5rem;
}

.picker label {
  display: grid;
  gap: 0.3rem;
}

select {
  border: 1px solid #c8d2e0;
  border-radius: var(--radius-sm);
  padding: 0.5rem 0.62rem;
  font: inherit;
  background: #fff;
}

select:focus {
  outline: none;
  border-color: var(--accent);
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  color: var(--muted-soft);
  font-size: 0.9rem;
}

.gate {
  margin-top: 0.6rem;
  border: 1px solid;
  border-radius: 10px;
  padding: 0.5rem 0.65rem;
  display: grid;
  gap: 0.2rem;
}

.gate.pass {
  border-color: rgba(26, 122, 76, 0.35);
  background: var(--success-light);
  color: var(--success);
}

.gate.warn {
  border-color: rgba(181, 121, 31, 0.35);
  background: var(--signal-light);
  color: var(--signal);
}

.gate-metrics {
  margin: 0.6rem 0 0;
  padding-left: 1rem;
  color: var(--ink-soft);
  display: grid;
  gap: 0.2rem;
}

h2 {
  margin: 0.6rem 0;
  font-family: var(--font-display);
}

.viewer-tabs {
  display: flex;
  gap: 0.4rem;
  margin-bottom: 0.6rem;
}

.viewer-tabs button {
  background: #e8edf5;
  color: #3a4c67;
  font-size: 0.88rem;
  border-radius: var(--radius-sm);
}

.viewer-tabs button.active {
  background: var(--accent);
  color: #fff;
}

.md-preview {
  padding: 1rem;
  border-radius: var(--radius-md);
  border: 1px solid var(--line-soft);
  background: #fff;
  line-height: 1.75;
  color: #1f2937;
  /* 长文在框内滚动，不把页面撑出屏幕 */
  max-height: clamp(320px, calc(100vh - 26rem), 720px);
  overflow-y: auto;
  min-height: 320px;
}

.md-preview :deep(h1) { font-size: 1.6rem; margin: 0.8rem 0 0.4rem; }
.md-preview :deep(h2) { font-size: 1.3rem; margin: 0.7rem 0 0.35rem; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.2rem; }
.md-preview :deep(h3) { font-size: 1.1rem; margin: 0.6rem 0 0.3rem; }
.md-preview :deep(p) { margin: 0.5rem 0; }
.md-preview :deep(ul), .md-preview :deep(ol) { margin: 0.5rem 0; padding-left: 1.4rem; }
.md-preview :deep(li) { margin: 0.2rem 0; }
.md-preview :deep(blockquote) { margin: 0.5rem 0; padding-left: 0.8rem; border-left: 3px solid var(--accent); color: #4b5563; }
.md-preview :deep(pre) { background: #f6f8fb; padding: 0.7rem; border-radius: 8px; overflow-x: auto; }
.md-preview :deep(code) { background: #f0f2f6; padding: 0.15rem 0.3rem; border-radius: 4px; font-size: 0.88em; }
.md-preview :deep(a) { color: #0b6dd6; }
.md-preview :deep(table) { border-collapse: collapse; margin: 0.5rem 0; width: 100%; }
.md-preview :deep(th), .md-preview :deep(td) { border: 1px solid #d6deec; padding: 0.4rem 0.6rem; }
.md-preview :deep(th) { background: #f6f8fb; }

pre {
  margin: 0;
  padding: 0.8rem;
  border-radius: var(--radius-md);
  border: 1px solid #d6deec;
  background: #fcfdff;
  white-space: pre-wrap;
  word-break: break-word;
  font: 400 0.92rem/1.55 var(--font-mono);
  max-height: clamp(320px, calc(100vh - 26rem), 720px);
  overflow-y: auto;
  min-height: 320px;
}

button {
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: #fff;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
}

button.ghost {
  background: var(--accent-light);
  color: var(--accent-strong);
}

.empty p {
  margin: 0;
  color: var(--muted);
}

.error {
  color: var(--danger);
}

@media (max-width: 760px) {
  .head {
    flex-direction: column;
  }
}
</style>
