<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import { apiRequest, uploadFile } from "../api";
import TaskProgress from "../components/TaskProgress.vue";
import type { Paper, TaskPayload } from "../types";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId));
const papers = ref<Paper[]>([]);
const task = ref<TaskPayload | null>(null);
const loading = ref(false);
const busy = ref(false);
const error = ref("");

const searchForm = reactive({
  query: "",
  max_results: 20
});

async function loadPapers() {
  loading.value = true;
  try {
    papers.value = await apiRequest<Paper[]>(`/api/projects/${projectId.value}/papers`);
  } finally {
    loading.value = false;
  }
}

async function refreshTask(taskId: string) {
  task.value = await apiRequest<TaskPayload>(`/api/tasks/${taskId}`);
}

async function searchPapers() {
  error.value = "";
  busy.value = true;
  try {
    const payload = await apiRequest<{ task_id: string }>(`/api/projects/${projectId.value}/search-papers`, {
      method: "POST",
      body: JSON.stringify({
        query: searchForm.query || undefined,
        max_results: searchForm.max_results
      })
    });
    await refreshTask(payload.task_id);
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "检索失败";
  } finally {
    busy.value = false;
  }
}

async function toggleSelect(paper: Paper) {
  await apiRequest(`/api/papers/${paper.id}/select`, {
    method: "POST",
    body: JSON.stringify({ selected: !paper.selected })
  });
  await loadPapers();
}

async function uploadForPaper(paper: Paper, event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;

  try {
    const payload = await uploadFile<{ paper: Paper }>(
      `/api/projects/${projectId.value}/papers/upload`,
      file,
      { paper_id: paper.id }
    );
    papers.value = papers.value.map((item) => (item.id === payload.paper.id ? payload.paper : item));
    input.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : "上传失败";
  }
}

async function parsePaper(paper: Paper) {
  error.value = "";
  busy.value = true;
  try {
    const payload = await apiRequest<{ task_id: string }>(`/api/papers/${paper.id}/parse`, {
      method: "POST",
      body: JSON.stringify({ chunk_size: 900 })
    });
    await refreshTask(payload.task_id);
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "解析失败";
  } finally {
    busy.value = false;
  }
}

async function autoDownloadPaper(paper: Paper) {
  error.value = "";
  busy.value = true;
  try {
    const payload = await apiRequest<{ task_id: string }>(`/api/papers/${paper.id}/download`, {
      method: "POST",
      body: "{}"
    });
    await refreshTask(payload.task_id);
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "自动下载失败";
  } finally {
    busy.value = false;
  }
}

async function autoDownloadAndParseSelected() {
  error.value = "";
  busy.value = true;
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/download-selected-papers?auto_parse=true&chunk_size=900`,
      {
        method: "POST",
        body: "{}"
      }
    );
    await refreshTask(payload.task_id);
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "批量自动下载失败";
  } finally {
    busy.value = false;
  }
}

async function deletePaper(paper: Paper) {
  const ok = window.confirm(`确认删除论文：\n\n${paper.title}`);
  if (!ok) return;

  error.value = "";
  busy.value = true;
  try {
    await apiRequest(`/api/papers/${paper.id}`, {
      method: "DELETE",
      body: "{}"
    });
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除失败";
  } finally {
    busy.value = false;
  }
}

async function deleteUnselectedPapers() {
  const targets = papers.value.filter((item) => !item.selected);
  if (targets.length === 0) return;
  const ok = window.confirm(`确认删除 ${targets.length} 篇未纳入论文？`);
  if (!ok) return;

  error.value = "";
  busy.value = true;
  try {
    for (const item of targets) {
      await apiRequest(`/api/papers/${item.id}`, {
        method: "DELETE",
        body: "{}"
      });
    }
    await loadPapers();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "批量删除失败";
  } finally {
    busy.value = false;
  }
}

onMounted(loadPapers);
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>论文库</h1>
      <p>检索候选论文、勾选纳入、自动下载或上传 PDF，并解析为 chunks。</p>
    </header>

    <section class="search card">
      <label>
        检索词
        <input v-model="searchForm.query" placeholder="留空则使用项目研究问题" />
      </label>
      <label>
        返回数量
        <input v-model.number="searchForm.max_results" type="number" min="1" max="100" />
      </label>
      <button type="button" :disabled="busy" @click="searchPapers">检索论文</button>
      <button type="button" class="ghost" :disabled="busy" @click="autoDownloadAndParseSelected">
        一键下载并解析已选论文
      </button>
      <button type="button" class="danger" :disabled="busy" @click="deleteUnselectedPapers">
        删除未纳入
      </button>
    </section>

    <TaskProgress :task="task" />

    <section class="card">
      <h2>候选列表</h2>
      <p v-if="loading">加载中...</p>
      <p v-else-if="papers.length === 0">暂无论文，先执行检索。</p>
      <table v-else>
        <thead>
          <tr>
            <th>纳入</th>
            <th>标题</th>
            <th>来源</th>
            <th>PDF</th>
            <th>解析</th>
            <th>删除</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="paper in papers" :key="paper.id">
            <td>
              <button type="button" class="ghost" :disabled="busy" @click="toggleSelect(paper)">
                {{ paper.selected ? "已选" : "未选" }}
              </button>
            </td>
            <td>
              <strong>{{ paper.title }}</strong>
              <div class="meta">
                {{ paper.year || "n/a" }} · {{ paper.venue || paper.source || "unknown" }} ·
                {{ paper.doi || paper.arxiv_id || "no-id" }} · 相关度 {{ (paper.relevance_score ?? 0).toFixed(3) }}
              </div>
            </td>
            <td>{{ paper.source || "unknown" }}</td>
            <td>
              <div class="pdf-cell">
                <button type="button" :disabled="busy || !paper.pdf_url" @click="autoDownloadPaper(paper)">
                  自动下载
                </button>
                <input type="file" accept=".pdf,application/pdf" @change="uploadForPaper(paper, $event)" />
              </div>
            </td>
            <td>
              <button type="button" :disabled="busy || !paper.local_pdf_path" @click="parsePaper(paper)">
                {{ paper.parse_status === "parsed" ? "重解析" : "解析" }}
              </button>
            </td>
            <td>
              <button type="button" class="danger" :disabled="busy" @click="deletePaper(paper)">
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
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
  margin: 0.35rem 0 0;
  color: var(--muted);
}

.card {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 0.9rem;
}

.search {
  display: flex;
  flex-wrap: wrap;
  gap: 0.7rem;
  align-items: flex-end;
}

label {
  display: grid;
  gap: 0.3rem;
}

input {
  border: 1px solid #bfd0e5;
  border-radius: 10px;
  padding: 0.5rem 0.68rem;
}

button {
  border: 0;
  border-radius: 10px;
  background: #0a6bd4;
  color: white;
  padding: 0.5rem 0.75rem;
  cursor: pointer;
}

button.ghost {
  background: #dceafb;
  color: #1c3f69;
}

button.danger {
  background: #b42318;
}

button:disabled {
  opacity: 0.65;
  cursor: not-allowed;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid #e2eaf6;
  text-align: left;
  vertical-align: top;
  padding: 0.55rem;
}

.meta {
  color: #587193;
  font-size: 0.88rem;
  margin-top: 0.25rem;
}

.pdf-cell {
  display: grid;
  gap: 0.4rem;
}

.error {
  color: #b42318;
}

@media (max-width: 900px) {
  table,
  thead,
  tbody,
  tr,
  th,
  td {
    display: block;
  }

  th {
    display: none;
  }
}
</style>
