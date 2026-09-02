<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { apiRequest } from "../api";
import type { Project } from "../types";

const router = useRouter();
const projects = ref<Project[]>([]);
const loading = ref(false);
const error = ref("");
const creating = ref(false);

/* ── Wizard state ──────────────────────────────── */
const step = ref(1);
const totalSteps = 3;

const form = reactive({
  title: "",
  research_question: "",
  article_type: "",
  language: "zh",
  target_words_min: 5000,
  target_words_max: 8000,
  citation_style: "GB/T 7714",
  target_audience: "",
  research_domain: "",
  writing_tone: "standard"
});

/* ── Template data (Step 1) ───────────────────── */
const templates = [
  {
    id: "policy_report",
    emoji: "📊",
    name: "政策报告",
    desc: "面向决策者的结构化分析报告，注重数据支撑与可行性建议。",
    useCase: "政策建议、行业分析、调研报告",
    words: [5000, 8000],
    audience: "政策研究者",
    tone: "standard",
    citation: "GB/T 7714",
    color: "#1a65c4",
    bg: "#e8f0fe"
  },
  {
    id: "literature_review",
    emoji: "📚",
    name: "文献综述",
    desc: "系统性梳理已有研究，识别研究空白与趋势，构建理论基础。",
    useCase: "综述论文、研究背景、文献梳理",
    words: [8000, 15000],
    audience: "学术同行",
    tone: "academic",
    citation: "APA 7th",
    color: "#0d7c75",
    bg: "#e0f5f0"
  },
  {
    id: "academic_draft",
    emoji: "🎓",
    name: "学术草稿",
    desc: "标准学术论文结构：引言、方法、结果、讨论，引用严谨。",
    useCase: "期刊投稿、学位论文、会议论文",
    words: [5000, 8000],
    audience: "学术同行",
    tone: "academic",
    citation: "APA 7th",
    color: "#6b21a8",
    bg: "#f3e8ff"
  },
  {
    id: "wechat_article",
    emoji: "📱",
    name: "公众号文章",
    desc: "面向大众读者的深度长文，兼顾专业性与可读性。",
    useCase: "公众号推文、知乎专栏、科普文章",
    words: [2000, 4000],
    audience: "普通读者",
    tone: "casual",
    citation: "GB/T 7714",
    color: "#b5791f",
    bg: "#fef3e0"
  }
];

/* ── Audience presets (Step 3) ────────────────── */
const audiencePresets = [
  "学术同行", "政策研究者", "行业从业者", "企业管理者", "普通读者", "学生"
];

/* ── Tone options ──────────────────────────────── */
const toneOptions = [
  { id: "casual", label: "轻松", desc: "口语化、生动" },
  { id: "standard", label: "标准", desc: "清晰、专业" },
  { id: "academic", label: "学术", desc: "严谨、规范" }
];

/* ── Citation options ──────────────────────────── */
const citationOptions = [
  "GB/T 7714", "APA 7th", "MLA 9th", "Chicago 17th", "IEEE", "Harvard"
];

/* ── Language options ──────────────────────────── */
const languageOptions = [
  { code: "zh", label: "中文" },
  { code: "en", label: "English" },
  { code: "ja", label: "日本語" }
];

/* ── Computed ──────────────────────────────────── */
const selectedTemplate = computed(() =>
  templates.find((t) => t.id === form.article_type)
);

const targetWordsAvg = computed(() =>
  Math.round((form.target_words_min + form.target_words_max) / 2)
);

const canProceedStep1 = computed(() => !!form.article_type);
const canProceedStep2 = computed(() => form.title.trim().length > 0 && form.research_question.trim().length >= 10);
const canCreate = computed(() => canProceedStep2.value);

const stepLabels = ["选择模板", "描述研究", "定制输出"];

/* ── Template selection ────────────────────────── */
function selectTemplate(tpl: typeof templates[number]) {
  form.article_type = tpl.id;
  form.target_words_min = tpl.words[0];
  form.target_words_max = tpl.words[1];
  form.target_audience = tpl.audience;
  form.writing_tone = tpl.tone;
  form.citation_style = tpl.citation;
}

/* ── Domain tags ───────────────────────────────── */
const domainTags = computed(() =>
  form.research_domain
    .split(/[,，、]+/)
    .map((s) => s.trim())
    .filter(Boolean)
);

/* ── Step navigation ───────────────────────────── */
function nextStep() {
  if (step.value < totalSteps) step.value++;
}
function prevStep() {
  if (step.value > 1) step.value--;
}

/* ── Project card menu ─────────────────────────── */
const openMenuId = ref<string | null>(null);

function toggleProjectMenu(event: Event, id: string) {
  event.stopPropagation();
  openMenuId.value = openMenuId.value === id ? null : id;
}

function closeProjectMenu() {
  openMenuId.value = null;
}

function onMenuDelete(project: Project) {
  closeProjectMenu();
  void deleteProject(project);
}

/* ── Project CRUD ──────────────────────────────── */
const templateLabelMap = computed(() => {
  const map: Record<string, string> = {};
  templates.forEach((t) => { map[t.id] = t.name; });
  return map;
});

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
  if (!canCreate.value) return;
  error.value = "";
  creating.value = true;
  try {
    const payload: Record<string, unknown> = {
      title: form.title.trim(),
      research_question: form.research_question.trim(),
      article_type: form.article_type,
      language: form.language,
      target_words: targetWordsAvg.value,
      citation_style: form.citation_style,
      target_audience: form.target_audience || null,
      settings: {
        writing_tone: form.writing_tone,
        research_domain: domainTags.value,
        word_range: [form.target_words_min, form.target_words_max]
      }
    };
    const created = await apiRequest<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    await router.push(`/projects/${created.id}/chat`);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "创建失败";
  } finally {
    creating.value = false;
  }
}

async function deleteProject(project: Project) {
  const ok = window.confirm(`确认删除项目：\n\n${project.title}\n\n该操作会删除项目下所有论文、证据与草稿。`);
  if (!ok) return;
  error.value = "";
  try {
    await apiRequest(`/api/projects/${project.id}`, { method: "DELETE", body: "{}" });
    await loadProjects();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "删除失败";
  }
}

function formatProjectType(type: string): string {
  return templateLabelMap.value[type] || type;
}

function langLabel(code: string): string {
  return languageOptions.find((l) => l.code === code)?.label ?? code;
}

/* ── Lifecycle ─────────────────────────────────── */
onMounted(() => {
  loadProjects();
  document.addEventListener("click", closeProjectMenu);
});

onUnmounted(() => {
  document.removeEventListener("click", closeProjectMenu);
});
</script>

<template>
  <section class="board">
    <header class="headline">
      <h1>PaperForge</h1>
      <p>证据锚定写作 — 从检索到发表的完整可追溯流程。</p>
    </header>

    <div class="main-grid">
      <!-- ═══════════════════════════════════════════
           WIZARD COLUMN
           ═══════════════════════════════════════════ -->
      <div class="wizard-col">
        <!-- Stepper -->
        <div class="stepper">
          <div
            v-for="(label, idx) in stepLabels"
            :key="idx"
            class="step-item"
            :class="{ active: step === idx + 1, done: step > idx + 1 }"
          >
            <span class="step-dot">{{ step > idx + 1 ? '✓' : idx + 1 }}</span>
            <span class="step-label">{{ label }}</span>
          </div>
          <div class="step-line" />
        </div>

        <!-- ── Step 1: Template Selection ── -->
        <div v-if="step === 1" class="step-content">
          <h2>选择创作模板</h2>
          <p class="step-desc">不同模板会预设字数范围、写作风格和引用格式，后续均可调整。</p>

          <div class="template-grid">
            <button
              v-for="tpl in templates"
              :key="tpl.id"
              type="button"
              class="tpl-card"
              :class="{ selected: form.article_type === tpl.id }"
              @click="selectTemplate(tpl)"
            >
              <span class="tpl-emoji">{{ tpl.emoji }}</span>
              <div class="tpl-body">
                <strong>{{ tpl.name }}</strong>
                <p>{{ tpl.desc }}</p>
                <div class="tpl-meta">
                  <span class="tpl-tag" :style="{ background: tpl.bg, color: tpl.color }">
                    {{ tpl.words[0].toLocaleString() }}–{{ tpl.words[1].toLocaleString() }} 字
                  </span>
                  <span class="tpl-use">{{ tpl.useCase }}</span>
                </div>
              </div>
              <span v-if="form.article_type === tpl.id" class="tpl-check">✓</span>
            </button>
          </div>
        </div>

        <!-- ── Step 2: Research Description ── -->
        <div v-if="step === 2" class="step-content">
          <h2>描述你的研究</h2>
          <p class="step-desc">清晰的研究问题是高质量写作的基础。写清问题边界与目标。</p>

          <div class="form-group">
            <label class="field-label">项目标题 <span class="required">*</span></label>
            <input
              v-model="form.title"
              type="text"
              class="field-input"
              :placeholder="selectedTemplate ? `例如：${selectedTemplate.name} — 具体主题` : '输入项目标题'"
              maxlength="300"
            />
            <span class="char-count">{{ form.title.length }}/300</span>
          </div>

          <div class="form-group">
            <label class="field-label">研究问题 <span class="required">*</span></label>
            <textarea
              v-model="form.research_question"
              class="field-textarea"
              rows="5"
              maxlength="4000"
              :placeholder="
                form.article_type === 'literature_review'
                  ? '例如：近五年深度学习在医学影像诊断中的研究进展与主要技术瓶颈是什么？'
                  : form.article_type === 'policy_report'
                  ? '例如：双减政策对K12教培行业就业结构的影响及政策优化建议'
                  : form.article_type === 'wechat_article'
                  ? '例如：普通读者如何理解和利用大语言模型提升日常工作效率？'
                  : '写清问题边界、目标对象与希望验证的结论'"
            />
            <span class="char-count">{{ form.research_question.length }}/4000</span>
            <p v-if="form.research_question.length > 0 && form.research_question.length < 10" class="field-hint warn">
              研究问题建议至少 10 个字符，写清问题边界。
            </p>
          </div>

          <div class="form-group">
            <label class="field-label">研究领域 / 关键词</label>
            <input
              v-model="form.research_domain"
              type="text"
              class="field-input"
              placeholder="用逗号分隔，如：大语言模型, 教育技术, 学习路径"
            />
            <div v-if="domainTags.length" class="tag-preview">
              <span v-for="tag in domainTags" :key="tag" class="domain-tag">{{ tag }}</span>
            </div>
          </div>
        </div>

        <!-- ── Step 3: Output Configuration ── -->
        <div v-if="step === 3" class="step-content">
          <h2>定制输出</h2>
          <p class="step-desc">根据目标读者和场景微调写作参数。所有选项创建后仍可修改。</p>

          <!-- Audience -->
          <div class="form-group">
            <label class="field-label">目标受众</label>
            <div class="chip-row">
              <button
                v-for="aud in audiencePresets"
                :key="aud"
                type="button"
                class="chip"
                :class="{ active: form.target_audience === aud }"
                @click="form.target_audience = form.target_audience === aud ? '' : aud"
              >
                {{ aud }}
              </button>
            </div>
            <input
              v-model="form.target_audience"
              type="text"
              class="field-input mt-sm"
              placeholder="或输入自定义受众"
            />
          </div>

          <!-- Word count -->
          <div class="form-group">
            <div class="label-row">
              <label class="field-label">目标篇幅</label>
              <span class="target-display">~{{ targetWordsAvg.toLocaleString() }} 字</span>
            </div>
            <div class="range-row">
              <span class="range-label">{{ form.target_words_min.toLocaleString() }}</span>
              <div class="range-inputs">
                <input v-model.number="form.target_words_min" type="range" min="1000" max="20000" step="500" />
                <input v-model.number="form.target_words_max" type="range" min="1000" max="30000" step="500" />
              </div>
              <span class="range-label">{{ form.target_words_max.toLocaleString() }}</span>
            </div>
          </div>

          <!-- Tone -->
          <div class="form-group">
            <label class="field-label">写作风格</label>
            <div class="tone-row">
              <button
                v-for="tone in toneOptions"
                :key="tone.id"
                type="button"
                class="tone-card"
                :class="{ active: form.writing_tone === tone.id }"
                @click="form.writing_tone = tone.id"
              >
                <strong>{{ tone.label }}</strong>
                <small>{{ tone.desc }}</small>
              </button>
            </div>
          </div>

          <!-- Citation + Language -->
          <div class="duo-row">
            <div class="form-group">
              <label class="field-label">引用格式</label>
              <div class="chip-row">
                <button
                  v-for="cite in citationOptions"
                  :key="cite"
                  type="button"
                  class="chip sm"
                  :class="{ active: form.citation_style === cite }"
                  @click="form.citation_style = cite"
                >
                  {{ cite }}
                </button>
              </div>
            </div>
            <div class="form-group">
              <label class="field-label">语言</label>
              <div class="chip-row">
                <button
                  v-for="lang in languageOptions"
                  :key="lang.code"
                  type="button"
                  class="chip sm"
                  :class="{ active: form.language === lang.code }"
                  @click="form.language = lang.code"
                >
                  {{ lang.label }}
                </button>
              </div>
            </div>
          </div>

          <!-- Summary -->
          <div class="summary-card">
            <h3>创建摘要</h3>
            <div class="summary-grid">
              <div class="summary-item">
                <span class="s-label">模板</span>
                <span class="s-value">{{ selectedTemplate?.emoji }} {{ selectedTemplate?.name || '—' }}</span>
              </div>
              <div class="summary-item">
                <span class="s-label">标题</span>
                <span class="s-value">{{ form.title || '—' }}</span>
              </div>
              <div class="summary-item">
                <span class="s-label">受众</span>
                <span class="s-value">{{ form.target_audience || '未指定' }}</span>
              </div>
              <div class="summary-item">
                <span class="s-label">篇幅</span>
                <span class="s-value">{{ form.target_words_min.toLocaleString() }}–{{ form.target_words_max.toLocaleString() }} 字</span>
              </div>
              <div class="summary-item">
                <span class="s-label">风格</span>
                <span class="s-value">{{ toneOptions.find(t => t.id === form.writing_tone)?.label || '—' }}</span>
              </div>
              <div class="summary-item">
                <span class="s-label">引用</span>
                <span class="s-value">{{ form.citation_style }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- ── Navigation ── -->
        <div class="nav-row">
          <button v-if="step > 1" type="button" class="nav-btn back" @click="prevStep">
            ← 上一步
          </button>
          <span v-else />
          <button
            v-if="step < totalSteps"
            type="button"
            class="nav-btn next"
            :disabled="step === 1 ? !canProceedStep1 : !canProceedStep2"
            @click="nextStep"
          >
            下一步 →
          </button>
          <button
            v-else
            type="button"
            class="nav-btn create"
            :disabled="!canCreate || creating"
            @click="createProject"
          >
            {{ creating ? '创建中…' : '创建项目 →' }}
          </button>
        </div>
      </div>

      <!-- ═══════════════════════════════════════════
           PROJECT LIST COLUMN
           ═══════════════════════════════════════════ -->
      <section class="list-col">
        <div class="list-header">
          <h2>已有项目 <span class="list-count">{{ projects.length }}</span></h2>
          <button class="ghost-btn" type="button" @click="loadProjects">刷新</button>
        </div>

        <div v-if="loading" class="loading-state">加载中…</div>
        <div v-else-if="projects.length === 0" class="empty-state">
          <p>还没有项目，先创建一个吧。</p>
        </div>
        <ul v-else class="project-list">
          <li v-for="project in projects" :key="project.id" class="project-card">
            <div class="pc-info">
              <h3>{{ project.title }}</h3>
              <p>{{ project.research_question }}</p>
              <span class="pc-meta">
                {{ formatProjectType(project.article_type) }}
                · {{ langLabel(project.language) }}
                · ~{{ project.target_words.toLocaleString() }} 字
              </span>
            </div>
            <div class="pc-actions">
              <RouterLink :to="`/projects/${project.id}/chat`" class="pc-btn primary">继续写作</RouterLink>
              <div class="pc-menu-wrap">
                <button
                  type="button"
                  class="pc-kebab"
                  aria-label="更多操作"
                  @click="toggleProjectMenu($event, project.id)"
                >···</button>
                <div v-if="openMenuId === project.id" class="pc-menu" @click.stop>
                  <RouterLink :to="`/projects/${project.id}`" class="pc-menu-item" @click="closeProjectMenu">
                    项目总览
                  </RouterLink>
                  <button type="button" class="pc-menu-item danger" @click="onMenuDelete(project)">
                    删除项目
                  </button>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </section>
    </div>

    <p v-if="error" class="error-toast">{{ error }}</p>
  </section>
</template>

<style scoped>
.board {
  display: grid;
  gap: 1.6rem;
}

.headline h1 {
  margin: 0;
  font: 700 2rem/1.15 var(--font-display);
  background: linear-gradient(135deg, var(--ink) 0%, var(--accent-strong) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.headline p {
  margin: 0.4rem 0 0;
  color: var(--muted);
  font-size: 0.93rem;
}

/* ── Main grid ── */
.main-grid {
  display: grid;
  /* minmax(0, fr)：去掉 fr 的 auto 最小宽度，防止项目卡片里的 nowrap 长文本
     把右列轨道撑爆、挤占左列并向外溢出 */
  grid-template-columns: minmax(0, 1fr) clamp(300px, 30vw, 400px);
  gap: 3rem;
  align-items: start;
}

/* ═══════════════════════════════════════════════════
   WIZARD
   ═══════════════════════════════════════════════════ */
.wizard-col {
  animation: rise-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

/* ── Stepper ── */
.stepper {
  display: flex;
  align-items: center;
  gap: 0;
  margin-bottom: 1.3rem;
  position: relative;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex: 1;
  z-index: 1;
}

.step-dot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font: 600 0.75rem/1 var(--font-display);
  background: var(--line-soft);
  color: var(--muted);
  flex-shrink: 0;
  transition: all 200ms ease;
}

.step-item.active .step-dot {
  background: var(--accent);
  color: #fff;
  box-shadow: 0 0 0 4px rgba(13, 124, 117, 0.12);
}

.step-item.done .step-dot {
  background: var(--success-light);
  color: var(--success);
}

.step-label {
  font-size: 0.82rem;
  color: var(--muted);
  white-space: nowrap;
  transition: color 200ms ease;
}

.step-item.active .step-label {
  color: var(--ink);
  font-weight: 500;
}

.step-line {
  position: absolute;
  top: 50%;
  left: 14px;
  right: 14px;
  height: 2px;
  background: var(--line-soft);
  z-index: 0;
  transform: translateY(-50%);
}

/* ── Step content ── */
.step-content {
  animation: rise-in 250ms cubic-bezier(0.16, 1, 0.3, 1);
}

.step-content h2 {
  margin: 0 0 0.3rem;
  font-size: 1.3rem;
  font-family: var(--font-display);
}

.step-desc {
  margin: 0 0 1rem;
  color: var(--muted);
  font-size: 0.88rem;
}

/* ── Template cards (Step 1) ── */
.template-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.9rem;
}

.tpl-card {
  display: flex;
  align-items: flex-start;
  gap: 0.8rem;
  padding: 1rem;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--surface-strong);
  cursor: pointer;
  text-align: left;
  transition: all 180ms ease;
  position: relative;
}

.tpl-card:hover {
  border-color: var(--accent-muted);
  box-shadow: var(--shadow-sm);
  transform: translateY(-1px);
}

.tpl-card.selected {
  border-color: var(--accent);
  background: var(--accent-light);
  box-shadow: 0 0 0 1px var(--accent);
}

.tpl-emoji {
  font-size: 1.8rem;
  flex-shrink: 0;
  margin-top: 2px;
}

.tpl-body {
  flex: 1;
  min-width: 0;
}

.tpl-body strong {
  font-size: 0.95rem;
  display: block;
  margin-bottom: 2px;
}

.tpl-body p {
  margin: 0;
  font-size: 0.84rem;
  color: var(--muted);
  line-height: 1.45;
}

.tpl-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.4rem;
}

.tpl-tag {
  font-size: 0.72rem;
  padding: 0.12rem 0.45rem;
  border-radius: 6px;
  font-weight: 500;
}

.tpl-use {
  font-size: 0.74rem;
  color: var(--muted-soft);
}

.tpl-check {
  position: absolute;
  top: 0.6rem;
  right: 0.7rem;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 0.72rem;
  font-weight: 700;
}

/* ── Form fields (Steps 2 & 3) ── */
.form-group {
  margin-bottom: 1rem;
}

.field-label {
  display: block;
  font-size: 0.84rem;
  font-weight: 500;
  color: var(--ink-soft);
  margin-bottom: 0.35rem;
}

.required {
  color: var(--danger);
}

.field-input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.72rem;
  font: inherit;
  font-size: 0.92rem;
  background: var(--surface-strong);
  color: var(--ink);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}

.field-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(13, 124, 117, 0.1);
  outline: none;
}

.field-textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.72rem;
  font: inherit;
  font-size: 0.92rem;
  background: var(--surface-strong);
  color: var(--ink);
  resize: vertical;
  transition: border-color 160ms ease, box-shadow 160ms ease;
  line-height: 1.55;
}

.field-textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(13, 124, 117, 0.1);
  outline: none;
}

.char-count {
  display: block;
  text-align: right;
  font-size: 0.72rem;
  color: var(--muted-soft);
  margin-top: 2px;
}

.field-hint {
  margin: 0.3rem 0 0;
  font-size: 0.8rem;
}

.field-hint.warn {
  color: var(--signal);
}

.tag-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.4rem;
}

.domain-tag {
  font-size: 0.76rem;
  background: var(--accent-light);
  color: var(--accent-strong);
  border-radius: 6px;
  padding: 0.15rem 0.5rem;
}

.mt-sm {
  margin-top: 0.4rem;
}

/* ── Chips ── */
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.chip {
  border: 1px solid var(--line);
  border-radius: 99px;
  padding: 0.32rem 0.72rem;
  background: var(--surface-strong);
  color: var(--muted);
  font-size: 0.82rem;
  cursor: pointer;
  transition: all 150ms ease;
}

.chip.sm {
  padding: 0.25rem 0.6rem;
  font-size: 0.78rem;
}

.chip:hover {
  border-color: var(--accent-muted);
  color: var(--ink-soft);
}

.chip.active {
  border-color: var(--accent);
  background: var(--accent-light);
  color: var(--accent-strong);
  font-weight: 500;
}

/* ── Word count range ── */
.label-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.target-display {
  font: 600 1rem/1 var(--font-display);
  color: var(--accent);
}

.range-row {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.3rem;
}

.range-label {
  font-size: 0.76rem;
  color: var(--muted-soft);
  font-variant-numeric: tabular-nums;
  min-width: 2.8rem;
  text-align: center;
}

.range-inputs {
  flex: 1;
  display: grid;
  gap: 4px;
}

input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
  cursor: pointer;
}

/* ── Tone cards ── */
.tone-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.5rem;
}

.tone-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 0.55rem 0.65rem;
  background: var(--surface-strong);
  cursor: pointer;
  text-align: center;
  transition: all 150ms ease;
}

.tone-card:hover {
  border-color: var(--accent-muted);
}

.tone-card.active {
  border-color: var(--accent);
  background: var(--accent-light);
}

.tone-card strong {
  display: block;
  font-size: 0.88rem;
}

.tone-card small {
  font-size: 0.74rem;
  color: var(--muted);
}

.duo-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.8rem;
}

/* ── Summary card ── */
.summary-card {
  border: 0;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, var(--accent-light) 0%, var(--surface) 100%);
  padding: 0.85rem;
  margin-top: 0.5rem;
}

.summary-card h3 {
  margin: 0 0 0.5rem;
  font-size: 0.88rem;
  color: var(--accent-strong);
}

.summary-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 0.35rem 0.8rem;
}

.summary-item {
  display: flex;
  gap: 0.4rem;
  align-items: baseline;
}

.s-label {
  font-size: 0.76rem;
  color: var(--muted);
  min-width: 2.2rem;
}

.s-value {
  font-size: 0.84rem;
  color: var(--ink-soft);
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Navigation buttons ── */
.nav-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 1.3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--line-soft);
}

.nav-btn {
  border: 0;
  border-radius: var(--radius-sm);
  padding: 0.55rem 1.1rem;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 160ms ease;
}

.nav-btn.back {
  background: transparent;
  color: var(--muted);
}

.nav-btn.back:hover {
  color: var(--ink-soft);
}

.nav-btn.next {
  background: var(--surface-strong);
  color: var(--accent);
  border: 1px solid var(--accent);
}

.nav-btn.next:hover:not(:disabled) {
  background: var(--accent-light);
}

.nav-btn.create {
  background: linear-gradient(135deg, var(--accent) 0%, #a06a18 100%);
  color: #fff;
}

.nav-btn.create:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(13, 124, 117, 0.25);
}

.nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

/* ═══════════════════════════════════════════════════
   PROJECT LIST
   ═══════════════════════════════════════════════════ */
.list-col {
  animation: rise-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.5rem;
  padding-bottom: 0.6rem;
  border-bottom: 1px solid var(--line-soft);
}

.list-header h2 {
  margin: 0;
  font-size: 0.8rem;
  font-weight: 500;
  letter-spacing: 0.08em;
  color: var(--muted);
}

.list-count {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  margin-left: 0.4rem;
  border-radius: 99px;
  background: var(--line-soft);
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 600;
  vertical-align: middle;
}

.ghost-btn {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 0.3rem 0.6rem;
  background: var(--surface-strong);
  color: var(--muted);
  cursor: pointer;
  font-size: 0.8rem;
}

.loading-state,
.empty-state {
  text-align: center;
  padding: 1.5rem 0;
  color: var(--muted-soft);
}

.project-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  /* minmax(0, 1fr)：卡片内 nowrap 的研究问题文本会抬高 min-content，
     无此约束时隐式轨道被撑破容器导致横向溢出 */
  grid-template-columns: minmax(0, 1fr);
  gap: 0.2rem;
}

.project-card {
  border-radius: var(--radius-md);
  padding: 0.85rem 0.9rem;
  background: transparent;
  display: flex;
  justify-content: space-between;
  gap: 0.7rem;
  align-items: center;
  transition: background 160ms ease, box-shadow 160ms ease;
}

.project-card:hover {
  background: var(--surface-strong);
  box-shadow: var(--shadow-sm);
}

.pc-info {
  flex: 1;
  min-width: 0;
}

.pc-info h3 {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 600;
}

.pc-info p {
  margin: 0.2rem 0;
  color: var(--muted);
  font-size: 0.8rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-meta {
  display: block;
  margin-top: 0.2rem;
  font-size: 0.74rem;
  color: var(--muted-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pc-actions {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
}

.pc-btn {
  border: 0;
  border-radius: var(--radius-sm);
  padding: 0.42rem 0.9rem;
  font-size: 0.82rem;
  font-weight: 500;
  text-decoration: none;
  text-align: center;
  cursor: pointer;
  transition: all 150ms ease;
}

.pc-btn.primary {
  background: var(--accent);
  color: #fff;
}

.pc-btn.primary:hover {
  background: var(--accent-strong);
}

/* ── Kebab menu ── */
.pc-menu-wrap {
  position: relative;
}

.pc-kebab {
  width: 30px;
  height: 30px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted-soft);
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  line-height: 1;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all 150ms ease;
}

.pc-kebab:hover {
  background: var(--line-soft);
  color: var(--ink);
}

.pc-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 60;
  min-width: 128px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface-strong);
  box-shadow: var(--shadow-md);
  display: grid;
}

.pc-menu-item {
  border: 0;
  border-radius: 7px;
  padding: 0.45rem 0.7rem;
  background: transparent;
  color: var(--ink-soft);
  font: inherit;
  font-size: 0.84rem;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
  transition: background 120ms ease;
}

.pc-menu-item:hover {
  background: var(--line-soft);
}

.pc-menu-item.danger {
  color: var(--danger);
}

.pc-menu-item.danger:hover {
  background: var(--danger-light);
}

.error-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  padding: 0.65rem 1rem;
  border-radius: var(--radius-md);
  background: var(--danger-light);
  color: var(--danger);
  border: 1px solid rgba(180, 35, 24, 0.2);
  font-size: 0.88rem;
  z-index: 200;
  animation: rise-in 200ms ease;
}

@media (max-width: 1150px) {
  .main-grid {
    grid-template-columns: 1fr;
    gap: 2rem;
  }
}

@media (max-width: 640px) {
  .template-grid {
    grid-template-columns: 1fr;
    gap: 0.6rem;
  }

  .tone-row {
    grid-template-columns: 1fr;
  }

  .duo-row {
    grid-template-columns: 1fr;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .step-label {
    display: none;
  }
}
</style>
