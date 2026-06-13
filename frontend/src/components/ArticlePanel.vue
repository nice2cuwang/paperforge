<script setup lang="ts">
import { computed, ref } from "vue";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { diffLines, diffStats, type DiffLine } from "../utils/diff";

const props = defineProps<{
  currentMd: string;
  previousMd: string;
  title: string;
  version: number;
  qualityScore: Record<string, unknown> | null;
  sourceBreakdown?: Record<string, number>;
}>();

const viewMode = ref<"preview" | "diff">("preview");

/**
 * Strip internal/system metadata from markdown before rendering.
 * Defense-in-depth: backend should not include these, but filter as safety net.
 */
function stripInternalMetadata(md: string): string {
  let cleaned = md;

  // Remove blockquote metadata lines (> article_type:, > citation_style:, > writing_mode:)
  cleaned = cleaned.replace(/^>\s*(article_type|citation_style|writing_mode):[^\n]*\n?/gm, "");

  // Remove > 修订说明：... lines
  cleaned = cleaned.replace(/^>\s*修订说明[：:][^\n]*\n?/gm, "");

  // Remove entire "## 证据索引" section (heading through next ## heading or end)
  cleaned = cleaned.replace(/## 证据索引\n(?:(?!##)[\s\S])*/gm, "");

  // Remove entire "## 人工终审提示" section
  cleaned = cleaned.replace(/## 人工终审提示\n(?:(?!##)[\s\S])*/gm, "");

  // Remove evidence_id list items (- evidence_id=xxx -> source=yyy)
  cleaned = cleaned.replace(/^- evidence_id=\S+\s*->\s*\S+[^\n]*\n?/gm, "");

  // Remove HTML evidence comments (<!-- evidence: ... -->)
  cleaned = cleaned.replace(/<!--\s*evidence:[^>]*-->/g, "");

  // Clean up excess blank lines (3+ newlines → 2)
  cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

  return cleaned.trim();
}

const cleanedMd = computed(() => stripInternalMetadata(props.currentMd));
const cleanedPrevMd = computed(() => props.previousMd ? stripInternalMetadata(props.previousMd) : "");

const renderedHtml = computed(() => {
  const raw = marked.parse(cleanedMd.value, { breaks: true, gfm: true }) as string;
  return DOMPurify.sanitize(raw);
});

const diffResult = computed<DiffLine[]>(() => {
  if (!cleanedPrevMd.value) return [];
  return diffLines(cleanedPrevMd.value, cleanedMd.value);
});

const diffSummary = computed(() => {
  if (!cleanedPrevMd.value) return null;
  return diffStats(diffResult.value);
});

const qualityGate = computed(() => {
  if (!props.qualityScore || typeof props.qualityScore !== "object") return null;
  return props.qualityScore as Record<string, unknown>;
});

const isPublicationReady = computed(() => qualityGate.value?.publication_prepared === true);

const metrics = computed(() => {
  const qg = qualityGate.value;
  if (!qg) return [];
  const keys = ["evidence_coverage", "citation_validity", "logic_score", "style_score", "de_ai_score"];
  return keys
    .filter((k) => qg[k] !== undefined && qg[k] !== null)
    .map((k) => ({
      label: k.replace(/_/g, " "),
      value: typeof qg[k] === "number" ? (qg[k] as number).toFixed(2) : String(qg[k])
    }));
});

const sourceLabels: Record<string, string> = {
  academic: "学术论文",
  web: "网络来源",
  community: "社区讨论",
  llm_knowledge: "背景知识",
};

const sourceEntries = computed(() => {
  if (!props.sourceBreakdown) return [];
  return Object.entries(props.sourceBreakdown)
    .filter(([, count]) => count > 0)
    .map(([type, count]) => ({
      label: sourceLabels[type] || type,
      count,
      type,
    }));
});
</script>

<template>
  <aside class="panel">
    <header class="panel-head">
      <div class="title-row">
        <span class="version-badge">v{{ version }}</span>
        <h3>{{ title || "草稿" }}</h3>
      </div>
      <div class="mode-tabs">
        <button :class="{ active: viewMode === 'preview' }" @click="viewMode = 'preview'">预览</button>
        <button :class="{ active: viewMode === 'diff' }" :disabled="!cleanedPrevMd" @click="viewMode = 'diff'">
          对比
        </button>
      </div>
    </header>

    <!-- Quality gate -->
    <div v-if="qualityGate" class="quality-bar" :class="isPublicationReady ? 'pass' : 'warn'">
      <span class="gate-label">{{ isPublicationReady ? "终稿就绪" : "待完善" }}</span>
      <div v-if="metrics.length" class="metrics-row">
        <span v-for="m in metrics" :key="m.label" class="metric">
          {{ m.label }}: <strong>{{ m.value }}</strong>
        </span>
      </div>
    </div>

    <!-- Source breakdown -->
    <div v-if="sourceEntries.length" class="source-bar">
      <span class="source-label">证据来源</span>
      <div class="source-chips">
        <span
          v-for="s in sourceEntries"
          :key="s.type"
          class="source-chip"
          :class="s.type"
        >
          {{ s.label }} {{ s.count }}
        </span>
      </div>
    </div>

    <!-- Diff summary -->
    <div v-if="viewMode === 'diff' && diffSummary" class="diff-summary">
      <span class="add">+{{ diffSummary.added }}</span>
      <span class="remove">-{{ diffSummary.removed }}</span>
      <span class="equal">{{ diffSummary.unchanged }} 行未变</span>
    </div>

    <!-- Content area -->
    <div class="content-area">
      <!-- Preview mode -->
      <div v-if="viewMode === 'preview'" class="md-preview" v-html="renderedHtml" />

      <!-- Diff mode -->
      <div v-else-if="viewMode === 'diff' && cleanedPrevMd" class="diff-view">
        <div
          v-for="(line, idx) in diffResult"
          :key="idx"
          class="diff-line"
          :class="line.type"
        >
          <span class="line-marker">
            {{ line.type === "add" ? "+" : line.type === "remove" ? "-" : " " }}
          </span>
          <span class="line-text">{{ line.value || "\u00A0" }}</span>
        </div>
      </div>

      <!-- No previous version for diff -->
      <div v-else-if="viewMode === 'diff' && !cleanedPrevMd" class="no-diff">
        <p>这是第一个版本，暂无对比基准。</p>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface-strong, #fff);
  border-left: 1px solid var(--line, #d4dbe8);
  overflow: hidden;
}

.panel-head {
  padding: 0.75rem 0.9rem;
  border-bottom: 1px solid var(--line-soft, #e4e9f1);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}

.version-badge {
  font: 700 0.72rem/1 var(--font-display, "Space Grotesk", sans-serif);
  color: var(--accent, #0d7c75);
  background: var(--accent-light, #e0f5f0);
  border-radius: 6px;
  padding: 0.22rem 0.42rem;
  flex-shrink: 0;
}

.title-row h3 {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mode-tabs {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.mode-tabs button {
  border: 1px solid var(--line-soft, #e4e9f1);
  border-radius: 7px;
  background: var(--surface, #fffef8);
  color: var(--muted, #627191);
  padding: 0.3rem 0.55rem;
  font-size: 0.78rem;
  cursor: pointer;
  transition: all 140ms ease;
}

.mode-tabs button.active {
  background: var(--accent, #0d7c75);
  color: #fff;
  border-color: var(--accent, #0d7c75);
}

.mode-tabs button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* Quality gate */
.quality-bar {
  padding: 0.45rem 0.9rem;
  border-bottom: 1px solid var(--line-soft, #e4e9f1);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
}

.quality-bar.pass {
  background: var(--success-light, #e6f7ed);
}

.quality-bar.warn {
  background: var(--signal-light, #fef3e0);
}

.gate-label {
  font-size: 0.8rem;
  font-weight: 600;
}

.quality-bar.pass .gate-label {
  color: var(--success, #1a7a4c);
}

.quality-bar.warn .gate-label {
  color: var(--signal, #b5791f);
}

.metrics-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.metric {
  font-size: 0.72rem;
  color: var(--muted, #627191);
}

.metric strong {
  color: var(--ink-soft, #2a3550);
}

/* Source breakdown bar */
.source-bar {
  padding: 0.35rem 0.9rem;
  border-bottom: 1px solid var(--line-soft, #e4e9f1);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-shrink: 0;
  background: var(--surface, #fffef8);
}

.source-bar .source-label {
  font-size: 0.75rem;
  color: var(--muted, #627191);
  flex-shrink: 0;
}

.source-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.source-chip {
  font-size: 0.7rem;
  font-weight: 500;
  padding: 0.15rem 0.45rem;
  border-radius: 10px;
  background: var(--accent-light, #e0f5f0);
  color: var(--accent, #0d7c75);
}

.source-chip.web {
  background: #e8f0fe;
  color: #1a65c4;
}

.source-chip.community {
  background: #fef3e0;
  color: #b5791f;
}

.source-chip.llm_knowledge {
  background: #f3e8ff;
  color: #7c3aed;
}

/* Diff summary */
.diff-summary {
  padding: 0.35rem 0.9rem;
  border-bottom: 1px solid var(--line-soft, #e4e9f1);
  display: flex;
  gap: 0.6rem;
  font-size: 0.78rem;
  flex-shrink: 0;
}

.diff-summary .add {
  color: var(--success, #1a7a4c);
  font-weight: 600;
}

.diff-summary .remove {
  color: var(--danger, #b42318);
  font-weight: 600;
}

.diff-summary .equal {
  color: var(--muted-soft, #8b96ad);
}

/* Content area */
.content-area {
  flex: 1;
  overflow-y: auto;
  padding: 0.9rem;
}

/* Markdown preview */
.md-preview {
  line-height: 1.75;
  color: var(--ink-soft, #2a3550);
}

.md-preview :deep(h1) { font-size: 1.45rem; margin: 0.8rem 0 0.4rem; font-family: var(--font-display); }
.md-preview :deep(h2) { font-size: 1.2rem; margin: 0.7rem 0 0.35rem; border-bottom: 1px solid var(--line-soft); padding-bottom: 0.2rem; }
.md-preview :deep(h3) { font-size: 1.02rem; margin: 0.6rem 0 0.25rem; }
.md-preview :deep(p) { margin: 0.45rem 0; }
.md-preview :deep(ul), .md-preview :deep(ol) { margin: 0.45rem 0; padding-left: 1.4rem; }
.md-preview :deep(li) { margin: 0.15rem 0; }
.md-preview :deep(blockquote) { margin: 0.45rem 0; padding-left: 0.7rem; border-left: 3px solid var(--accent); color: var(--muted); }
.md-preview :deep(pre) { background: #f6f8fb; padding: 0.6rem; border-radius: 8px; overflow-x: auto; }
.md-preview :deep(code) { background: #f0f2f6; padding: 0.12rem 0.25rem; border-radius: 4px; font-size: 0.88em; }
.md-preview :deep(a) { color: var(--accent); }
.md-preview :deep(img) { max-width: 100%; border-radius: 8px; margin: 0.5rem 0; }
.md-preview :deep(table) { border-collapse: collapse; margin: 0.45rem 0; width: 100%; }
.md-preview :deep(th), .md-preview :deep(td) { border: 1px solid var(--line-soft); padding: 0.35rem 0.5rem; }
.md-preview :deep(th) { background: var(--surface); }

/* Diff view */
.diff-view {
  font: 400 0.82rem/1.6 var(--font-mono, "JetBrains Mono", monospace);
  white-space: pre-wrap;
  word-break: break-word;
}

.diff-line {
  display: flex;
  gap: 0.3rem;
  padding: 0 0.3rem;
  border-radius: 3px;
  min-height: 1.6em;
}

.diff-line.add {
  background: rgba(26, 122, 76, 0.08);
}

.diff-line.remove {
  background: rgba(180, 35, 24, 0.06);
}

.line-marker {
  flex-shrink: 0;
  width: 1em;
  text-align: center;
  font-weight: 600;
  user-select: none;
}

.diff-line.add .line-marker {
  color: var(--success, #1a7a4c);
}

.diff-line.remove .line-marker {
  color: var(--danger, #b42318);
}

.line-text {
  flex: 1;
  color: var(--ink-soft, #2a3550);
}

.diff-line.add .line-text {
  color: var(--success, #1a7a4c);
}

.diff-line.remove .line-text {
  color: var(--danger, #b42318);
  text-decoration: line-through;
  opacity: 0.7;
}

.no-diff {
  padding: 2rem 0;
  text-align: center;
  color: var(--muted-soft, #8b96ad);
}

.no-diff p {
  margin: 0;
}
</style>
