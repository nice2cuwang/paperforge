<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";

import { apiRequest } from "../api";
import type { Project } from "../types";

const router = useRouter();
const projects = ref<Project[]>([]);
const loading = ref(false);
const error = ref("");

const form = reactive({
  title: "",
  research_question: "",
  article_type: "policy_report",
  language: "zh",
  target_words: 5000,
  citation_style: "GB/T 7714",
  target_audience: ""
});

const articleTypeOptions = [
  { value: "policy_report", label: "policy_report（政策报告）" },
  { value: "literature_review", label: "literature_review（文献综述）" },
  { value: "academic_draft", label: "academic_draft（学术草稿）" },
  { value: "wechat_article", label: "wechat_article（公众号文章）" }
];

async function loadProjects() {
  loading.value = true;
  error.value = "";
  try {
    projects.value = await apiRequest<Project[]>("/api/projects");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

async function createProject() {
  error.value = "";
  try {
    const payload = {
      ...form,
      target_audience: form.target_audience || null
    };
    const created = await apiRequest<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    await loadProjects();
    await router.push(`/projects/${created.id}`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "创建失败";
  }
}

async function deleteProject(project: Project) {
  const ok = window.confirm(`确认删除项目：\n\n${project.title}\n\n该操作会删除项目下所有论文、证据与草稿。`);
  if (!ok) return;

  error.value = "";
  try {
    await apiRequest(`/api/projects/${project.id}`, {
      method: "DELETE",
      body: "{}"
    });
    await loadProjects();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除失败";
  }
}

onMounted(loadProjects);
</script>

<template>
  <section class="board">
    <header class="headline">
      <h1>PaperForge 控制台</h1>
      <p>把“检索-证据-写作-审查-导出”压进同一条可追溯流程。</p>
    </header>

    <div class="grid">
      <form class="card creator" @submit.prevent="createProject">
        <h2>新建研究项目</h2>

        <label>
          标题
          <input v-model="form.title" required placeholder="例如：AI 与大学生学习路径优化" />
        </label>

        <label>
          研究问题
          <textarea
            v-model="form.research_question"
            rows="5"
            required
            placeholder="写清问题边界、目标对象与希望验证的结论"
          />
        </label>

        <div class="duo">
          <label>
            文章类型
            <select v-model="form.article_type">
              <option v-for="item in articleTypeOptions" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>

          <label>
            语言
            <input v-model="form.language" />
          </label>
        </div>

        <div class="duo">
          <label>
            目标字数
            <input v-model.number="form.target_words" type="number" min="500" max="50000" />
          </label>
          <label>
            引用格式
            <input v-model="form.citation_style" />
          </label>
        </div>

        <label>
          目标受众
          <input v-model="form.target_audience" placeholder="例如：高校教师、政策研究者" />
        </label>

        <button type="submit" class="primary">创建并进入流程</button>
      </form>

      <section class="card radar">
        <div class="row">
          <h2>项目列表</h2>
          <button class="ghost" type="button" @click="loadProjects">刷新</button>
        </div>
        <p v-if="loading">加载中...</p>
        <p v-else-if="projects.length === 0">暂无项目，先创建一个。</p>
        <ul v-else class="list">
          <li v-for="project in projects" :key="project.id">
            <article>
              <h3>{{ project.title }}</h3>
              <p>{{ project.research_question }}</p>
              <small>{{ project.article_type }} · {{ project.language }} · {{ project.target_words }} 字</small>
            </article>
            <div class="actions">
              <RouterLink :to="`/projects/${project.id}`">进入</RouterLink>
              <button type="button" class="danger" @click="deleteProject(project)">删除</button>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
  </section>
</template>

<style scoped>
.board {
  display: grid;
  gap: 1rem;
}

.headline h1 {
  margin: 0;
  font: 700 2rem/1.2 "Space Grotesk", "Noto Sans SC", sans-serif;
  letter-spacing: 0.01em;
}

.headline p {
  margin: 0.48rem 0 0;
  color: var(--muted);
}

.grid {
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  gap: 1rem;
}

.card {
  border: 1px solid var(--line);
  border-radius: 18px;
  background: var(--surface);
  padding: 1rem;
  display: grid;
  gap: 0.75rem;
  animation: rise-in 280ms ease;
}

.creator {
  background:
    radial-gradient(300px 140px at 88% -8%, rgba(186, 223, 219, 0.55) 0%, transparent 70%),
    var(--surface);
}

.radar {
  background:
    radial-gradient(230px 120px at 0% 0%, rgba(247, 231, 197, 0.6) 0%, transparent 70%),
    var(--surface);
}

h2 {
  margin: 0;
  font-size: 1.05rem;
}

label {
  display: grid;
  gap: 0.3rem;
}

input,
select,
textarea {
  border: 1px solid #c8d2e0;
  border-radius: 12px;
  padding: 0.56rem 0.68rem;
  font: inherit;
  background: #fff;
}

textarea {
  resize: vertical;
}

.duo {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.6rem;
}

button,
a {
  border: 0;
  border-radius: 11px;
  padding: 0.56rem 0.82rem;
  cursor: pointer;
  text-decoration: none;
  text-align: center;
}

.primary {
  color: #fff;
  background: linear-gradient(90deg, #0f7f78 0%, #c07817 100%);
}

.ghost {
  color: #21415f;
  background: #deecff;
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 0.7rem;
}

.list li {
  border: 1px solid #d8dfeb;
  border-radius: 12px;
  padding: 0.72rem;
  display: flex;
  justify-content: space-between;
  gap: 0.8rem;
  align-items: start;
  background: #fff;
}

.list h3 {
  margin: 0;
  font-size: 1rem;
}

.list p {
  margin: 0.3rem 0;
  color: #384a66;
}

.list small {
  color: #5b6b84;
}

.list a {
  color: #0f4560;
  background: #def2ef;
}

.actions {
  display: grid;
  gap: 0.5rem;
}

button.danger {
  color: #fff;
  background: #b42318;
}

.error {
  color: var(--danger);
}

@media (max-width: 1050px) {
  .grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .duo {
    grid-template-columns: 1fr;
  }
}
</style>
