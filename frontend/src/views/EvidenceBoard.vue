<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";

import { apiRequest } from "../api";
import TaskProgress from "../components/TaskProgress.vue";
import type { EvidenceCard, Paper, TaskPayload } from "../types";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId));
const evidence = ref<EvidenceCard[]>([]);
const papers = ref<Paper[]>([]);
const task = ref<TaskPayload | null>(null);
const loading = ref(false);
const error = ref("");

/* ── Detail modal ─────────────────────────────────── */
const detailCard = ref<EvidenceCard | null>(null);

function openDetail(card: EvidenceCard) {
  detailCard.value = card;
}

function closeDetail() {
  detailCard.value = null;
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") closeDetail();
}

const paperTitleMap = computed(() => Object.fromEntries(papers.value.map((paper) => [paper.id, paper.title])));
const paperMap = computed(() => Object.fromEntries(papers.value.map((paper) => [paper.id, paper])));

const detailPaper = computed(() => {
  if (!detailCard.value) return null;
  return paperMap.value[detailCard.value.paper_id] ?? null;
});

/* ── Source badges ────────────────────────────────── */
const SOURCE_META: Record<string, { label: string; color: string; bg: string }> = {
  academic: { label: "论文", color: "#6b21a8", bg: "#f3e8ff" },
  web: { label: "网页", color: "#1a65c4", bg: "#e8f0fe" },
  community: { label: "社区", color: "#b5791f", bg: "#fef3e0" },
  llm_knowledge: { label: "模型知识", color: "#64748b", bg: "#f1f5f9" }
};

function sourceMeta(card: EvidenceCard) {
  return SOURCE_META[card.source_type || "academic"] ?? { label: card.source_type || "未知", color: "#627191", bg: "#f1f5f9" };
}

const STRENGTH_COLOR: Record<string, string> = {
  high: "#1a7a4c",
  medium: "#b5791f",
  low: "#b42318"
};

function strengthColor(card: EvidenceCard): string {
  return STRENGTH_COLOR[card.strength || ""] ?? "#627191";
}

const TYPE_LABEL: Record<string, string> = {
  empirical_result: "实证结果",
  model_result: "模型结果",
  web_source: "网页来源",
  expert_opinion: "观点",
  general_evidence: "一般证据",
  paper: "论文"
};

function typeLabel(card: EvidenceCard): string {
  return TYPE_LABEL[card.evidence_type || ""] ?? card.evidence_type ?? "未知";
}

/* ── Filter ───────────────────────────────────────── */
const sourceFilter = ref<string>("all");

const filteredEvidence = computed(() => {
  if (sourceFilter.value === "all") return evidence.value;
  return evidence.value.filter((c) => (c.source_type || "academic") === sourceFilter.value);
});

const sourceCounts = computed(() => {
  const counts: Record<string, number> = {};
  for (const card of evidence.value) {
    const type = card.source_type || "academic";
    counts[type] = (counts[type] || 0) + 1;
  }
  return counts;
});

async function load() {
  loading.value = true;
  error.value = "";
  try {
    papers.value = await apiRequest<Paper[]>(`/api/projects/${projectId.value}/papers`);
    evidence.value = await apiRequest<EvidenceCard[]>(`/api/projects/${projectId.value}/evidence`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载证据板失败";
  } finally {
    loading.value = false;
  }
}

async function buildEvidence() {
  error.value = "";
  try {
    const payload = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/build-evidence`, {
      method: "POST",
      body: JSON.stringify({ max_cards: 150, only_selected: true })
    });
    task.value = await apiRequest<TaskPayload>(`/api/tasks/${payload.task_id}`);
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "重建证据失败";
  }
}

onMounted(() => {
  load();
  document.addEventListener("keydown", onKeydown);
});

onUnmounted(() => {
  document.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>Evidence Board</h1>
      <p>证据卡是写作唯一输入来源，确保可追溯与可审计。点击卡片查看完整详情。</p>
    </header>

    <section class="toolbar">
      <button type="button" @click="buildEvidence">从已选论文重建 Evidence Cards</button>
      <div class="filters">
        <button
          v-for="(meta, key) in SOURCE_META"
          :key="key"
          type="button"
          class="filter-chip"
          :class="{ active: sourceFilter === key }"
          :style="sourceFilter === key ? { background: meta.bg, color: meta.color, borderColor: meta.color } : {}"
          @click="sourceFilter = sourceFilter === key ? 'all' : key"
        >
          {{ meta.label }} <span class="count">{{ sourceCounts[key] ?? 0 }}</span>
        </button>
        <button
          v-if="sourceFilter !== 'all'"
          type="button"
          class="filter-chip active"
          @click="sourceFilter = 'all'"
        >
          显示全部 ({{ evidence.length }})
        </button>
      </div>
    </section>

    <TaskProgress :task="task" />

    <section class="cards">
      <p v-if="loading">加载中...</p>
      <p v-else-if="filteredEvidence.length === 0">暂无证据卡，请先在论文库完成 PDF 下载/上传与解析。</p>
      <article
        v-for="card in filteredEvidence"
        :key="card.id"
        class="card"
        role="button"
        tabindex="0"
        @click="openDetail(card)"
        @keydown.enter="openDetail(card)"
      >
        <header>
          <strong class="claim">{{ card.claim }}</strong>
          <span class="badges">
            <span class="badge" :style="{ background: sourceMeta(card).bg, color: sourceMeta(card).color }">
              {{ sourceMeta(card).label }}
            </span>
            <span class="badge strength" :style="{ color: strengthColor(card) }">
              {{ card.strength || "unknown" }}
            </span>
          </span>
        </header>
        <p class="support">{{ card.supporting_text }}</p>
        <footer>
          <span class="src">📄 {{ paperTitleMap[card.paper_id] || card.paper_id }}</span>
          <span v-if="card.page_start">p.{{ card.page_start }}-{{ card.page_end }}</span>
          <span class="detail-hint">查看详情 →</span>
        </footer>
      </article>
    </section>

    <p v-if="error" class="error">{{ error }}</p>

    <!-- ── Detail modal ── -->
    <div v-if="detailCard" class="modal-mask" @click.self="closeDetail">
      <div class="modal">
        <header class="modal-head">
          <div class="modal-title">
            <span class="badge" :style="{ background: sourceMeta(detailCard).bg, color: sourceMeta(detailCard).color }">
              {{ sourceMeta(detailCard).label }}
            </span>
            <span class="badge strength" :style="{ color: strengthColor(detailCard) }">
              {{ detailCard.strength || "unknown" }}
            </span>
            <span class="badge plain">{{ typeLabel(detailCard) }}</span>
          </div>
          <button class="close-btn" type="button" aria-label="关闭" @click="closeDetail">×</button>
        </header>

        <div class="modal-body">
          <section class="field">
            <h3>核心论断</h3>
            <p class="claim-full">{{ detailCard.claim }}</p>
          </section>

          <section class="field">
            <h3>支撑原文</h3>
            <p class="support-full">{{ detailCard.supporting_text || "（无支撑文本）" }}</p>
          </section>

          <section v-if="detailCard.limitations" class="field">
            <h3>限制说明</h3>
            <p class="limitations">{{ detailCard.limitations }}</p>
          </section>

          <section class="field">
            <h3>来源信息</h3>
            <dl class="meta-grid">
              <div class="meta-item">
                <dt>所属论文</dt>
                <dd>{{ paperTitleMap[detailCard.paper_id] || detailCard.paper_id }}</dd>
              </div>
              <div v-if="detailPaper?.venue" class="meta-item">
                <dt>出处</dt>
                <dd>{{ detailPaper.venue }}</dd>
              </div>
              <div v-if="detailPaper?.year" class="meta-item">
                <dt>年份</dt>
                <dd>{{ detailPaper.year }}</dd>
              </div>
              <div v-if="detailPaper?.doi" class="meta-item">
                <dt>DOI</dt>
                <dd class="mono">{{ detailPaper.doi }}</dd>
              </div>
              <div v-if="detailPaper?.source_url" class="meta-item wide">
                <dt>链接</dt>
                <dd><a :href="detailPaper.source_url" target="_blank" rel="noopener">{{ detailPaper.source_url }}</a></dd>
              </div>
              <div class="meta-item">
                <dt>页码</dt>
                <dd>{{ detailCard.page_start ?? "?" }} – {{ detailCard.page_end ?? "?" }}</dd>
              </div>
              <div class="meta-item">
                <dt>文本块</dt>
                <dd>{{ detailCard.chunk_ids.length }} 个</dd>
              </div>
              <div class="meta-item">
                <dt>证据类型</dt>
                <dd>{{ typeLabel(detailCard) }}</dd>
              </div>
              <div class="meta-item">
                <dt>已用于草稿</dt>
                <dd>{{ detailCard.used_in_draft ? "是" : "否" }}</dd>
              </div>
            </dl>
          </section>
        </div>

        <footer class="modal-foot">
          <span class="card-id mono">{{ detailCard.id }}</span>
        </footer>
      </div>
    </div>
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
  margin: 0.4rem 0 0;
  color: var(--muted);
}

.toolbar {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface);
  padding: 0.8rem;
  display: flex;
  gap: 0.8rem;
  align-items: center;
  flex-wrap: wrap;
  box-shadow: var(--shadow-sm);
}

.filters {
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.filter-chip {
  border: 1px solid var(--line);
  border-radius: 99px;
  background: var(--surface-strong);
  color: var(--muted);
  font-size: 0.8rem;
  padding: 0.28rem 0.7rem;
  cursor: pointer;
  transition: all 150ms ease;
}

.filter-chip:hover {
  border-color: var(--accent-muted);
}

.filter-chip.active {
  font-weight: 500;
}

.count {
  margin-left: 0.2rem;
  font-size: 0.72rem;
  opacity: 0.7;
}

button {
  border: 0;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: #fff;
  padding: 0.55rem 0.8rem;
  cursor: pointer;
}

.cards {
  display: grid;
  gap: 0.7rem;
}

.card {
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-strong);
  padding: 0.8rem;
  display: grid;
  gap: 0.55rem;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms ease;
}

.card:hover,
.card:focus-visible {
  border-color: var(--accent-muted);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
  outline: none;
}

.card header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.8rem;
}

.claim {
  color: var(--ink);
  line-height: 1.5;
}

.badges {
  display: flex;
  gap: 0.3rem;
  flex-shrink: 0;
}

.badge {
  border-radius: 6px;
  padding: 0.12rem 0.45rem;
  font-size: 0.74rem;
  font-weight: 500;
  white-space: nowrap;
}

.badge.strength {
  border: 1px solid currentColor;
  background: transparent;
}

.badge.plain {
  background: var(--line-soft);
  color: var(--muted);
}

.support {
  margin: 0;
  color: var(--muted);
  font-size: 0.87rem;
  line-height: 1.55;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.card footer {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  align-items: baseline;
  color: var(--muted-soft);
  font-size: 0.8rem;
}

.src {
  max-width: 55%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-hint {
  margin-left: auto;
  color: var(--accent);
  font-size: 0.78rem;
  opacity: 0;
  transition: opacity 150ms ease;
}

.card:hover .detail-hint {
  opacity: 1;
}

/* ── Modal ── */
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
  display: grid;
  place-items: center;
  padding: 1.5rem;
  animation: fade-in 160ms ease;
}

.modal {
  width: min(720px, 100%);
  max-height: min(82vh, 760px);
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-lg);
  background: var(--surface-strong);
  box-shadow: var(--shadow-lg);
  animation: rise-in 220ms cubic-bezier(0.16, 1, 0.3, 1);
  overflow: hidden;
}

.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.8rem;
  padding: 0.9rem 1.2rem 0.6rem;
  border-bottom: 1px solid var(--line-soft);
}

.modal-title {
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
}

.close-btn {
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 1.5rem;
  line-height: 1;
  padding: 0.1rem 0.4rem;
  border-radius: 8px;
  cursor: pointer;
}

.close-btn:hover {
  background: var(--line-soft);
  color: var(--ink);
}

.modal-body {
  padding: 0.9rem 1.2rem;
  overflow-y: auto;
  display: grid;
  gap: 1rem;
}

.field h3 {
  margin: 0 0 0.35rem;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  color: var(--muted);
  text-transform: uppercase;
}

.claim-full {
  margin: 0;
  font-size: 1rem;
  line-height: 1.65;
  color: var(--ink);
  font-weight: 500;
}

.support-full {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.7;
  color: var(--ink-soft);
  white-space: pre-wrap;
  word-break: break-word;
}

.limitations {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--signal);
  background: var(--signal-light);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.7rem;
}

.meta-grid {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.6rem 1.2rem;
}

.meta-item {
  display: grid;
  gap: 0.1rem;
  min-width: 0;
}

.meta-item.wide {
  grid-column: 1 / -1;
}

.meta-item dt {
  font-size: 0.72rem;
  color: var(--muted-soft);
}

.meta-item dd {
  margin: 0;
  font-size: 0.86rem;
  color: var(--ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
}

.meta-item dd a {
  color: var(--accent);
  word-break: break-all;
}

.mono {
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.modal-foot {
  padding: 0.6rem 1.2rem;
  border-top: 1px solid var(--line-soft);
  background: var(--surface);
}

.card-id {
  font-size: 0.72rem;
  color: var(--muted-soft);
}

.error {
  color: var(--danger);
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (max-width: 640px) {
  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
