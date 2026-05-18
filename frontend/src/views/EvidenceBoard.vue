<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
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

const paperTitleMap = computed(() => Object.fromEntries(papers.value.map((paper) => [paper.id, paper.title])));

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

onMounted(load);
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>Evidence Board</h1>
      <p>证据卡是写作唯一输入来源，确保可追溯与可审计。</p>
    </header>

    <section class="toolbar">
      <button type="button" @click="buildEvidence">从已选论文重建 Evidence Cards</button>
    </section>

    <TaskProgress :task="task" />

    <section class="cards">
      <p v-if="loading">加载中...</p>
      <p v-else-if="evidence.length === 0">暂无证据卡，请先在论文库完成 PDF 下载/上传与解析。</p>
      <article v-for="card in evidence" :key="card.id" class="card">
        <header>
          <strong>{{ card.claim }}</strong>
          <span>{{ card.evidence_type || "unknown" }} · {{ card.strength || "unknown" }}</span>
        </header>
        <p>{{ card.supporting_text }}</p>
        <footer>
          <span>paper: {{ paperTitleMap[card.paper_id] || card.paper_id }}</span>
          <span>page: {{ card.page_start || "?" }}-{{ card.page_end || "?" }}</span>
          <span>id: {{ card.id }}</span>
        </footer>
      </article>
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
  margin: 0.4rem 0 0;
  color: var(--muted);
}

.toolbar {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 0.8rem;
}

button {
  border: 0;
  border-radius: 10px;
  background: #0d6ed7;
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
  border-radius: 12px;
  background: #fff;
  padding: 0.8rem;
  display: grid;
  gap: 0.55rem;
}

.card header {
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
}

.card header span {
  color: #3f5d84;
  font-size: 0.88rem;
}

.card p {
  margin: 0;
  color: #213a59;
  line-height: 1.55;
}

.card footer {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  color: #587193;
  font-size: 0.86rem;
}

.error {
  color: #b42318;
}
</style>
