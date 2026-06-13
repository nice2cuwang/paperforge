<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { apiRequest, getApiBase, uploadFile } from "../api";
import AgentMessage from "../components/AgentMessage.vue";
import ArticlePanel from "../components/ArticlePanel.vue";
import ChatComposer from "../components/ChatComposer.vue";
import type {
  ChatMessage,
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

/* ── State ──────────────────────────────────────── */
const project = ref<Project | null>(null);
const papers = ref<Paper[]>([]);
const evidence = ref<EvidenceCard[]>([]);
const drafts = ref<Draft[]>([]);
const issues = ref<ReviewIssue[]>([]);
const messages = ref<ChatMessage[]>([]);
const chatContainer = ref<HTMLElement | null>(null);
const busy = ref(false);
const error = ref("");

// Article panel
const selectedDraftId = ref("");
const compareDraftId = ref("");
const showPanel = ref(false);

const selectedDraft = computed(() => drafts.value.find((d) => d.id === selectedDraftId.value) ?? null);
const compareDraft = computed(() => drafts.value.find((d) => d.id === compareDraftId.value) ?? null);

const sourceBreakdown = computed(() => {
  const counts: Record<string, number> = {};
  for (const card of evidence.value) {
    const type = card.source_type || "academic";
    counts[type] = (counts[type] || 0) + 1;
  }
  return counts;
});

/* ── Helpers ────────────────────────────────────── */
let _msgCounter = 0;
function msgId(): string {
  return `msg-${Date.now()}-${++_msgCounter}`;
}

function pushMessage(
  agent: ChatMessage["agent"],
  type: ChatMessage["type"],
  text: string,
  data?: Record<string, unknown>,
  draftId?: string
) {
  messages.value.push({
    id: msgId(),
    agent,
    type,
    text,
    data,
    draftId,
    timestamp: Date.now()
  });
  // Keep last 500 messages to avoid localStorage quota issues
  if (messages.value.length > 500) {
    messages.value = messages.value.slice(-500);
  }
  scrollToBottom();
}

function scrollToBottom() {
  nextTick(() => {
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
    }
  });
}

const TASK_STORAGE_KEY = (pid: string) => `paperforge_task_${pid}`;
const MESSAGES_STORAGE_KEY = (pid: string) => `paperforge_messages_${pid}`;

/* ── Message persistence ─────────────────────────── */
function saveMessages() {
  try {
    const payload = JSON.stringify(messages.value);
    localStorage.setItem(MESSAGES_STORAGE_KEY(projectId.value), payload);
  } catch {
    // localStorage quota exceeded or unavailable — silent fail
  }
}

function loadMessages(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(MESSAGES_STORAGE_KEY(projectId.value));
    if (raw) {
      const parsed = JSON.parse(raw) as ChatMessage[];
      if (Array.isArray(parsed)) return parsed;
    }
  } catch {
    // Corrupted data — start fresh
  }
  return [];
}

function clearMessages() {
  localStorage.removeItem(MESSAGES_STORAGE_KEY(projectId.value));
}

// Auto-save messages whenever they change
watch(messages, saveMessages, { deep: true });

function pushDebateMessage(logText: string) {
  // Strip the [辩论] prefix before passing to the message
  const cleanText = logText.replace(/^\[辩论\]\s*/, "");
  pushMessage("review", "debate", cleanText);
}

async function pollTask(taskId: string, maxMs = 30 * 60 * 1000, startLogIndex = 0): Promise<TaskPayload | null> {
  const start = Date.now();
  let lastProgress = -1;
  let lastLogIndex = startLogIndex;
  while (Date.now() - start < maxMs) {
    try {
      const payload = await apiRequest<TaskPayload>(`/api/tasks/${taskId}`);

      // ── Real-time debate log streaming ──
      if (payload.logs && payload.logs.length > lastLogIndex) {
        for (let i = lastLogIndex; i < payload.logs.length; i++) {
          const log = payload.logs[i];
          if (log.startsWith("[辩论]")) {
            pushDebateMessage(log);
          }
        }
        lastLogIndex = payload.logs.length;
      }

      // Emit progress messages at meaningful intervals
      if (payload.progress !== lastProgress && payload.current_step) {
        const step = payload.current_step;
        const pct = payload.progress;
        // Only emit at stage boundaries, not every percent
        if (pct - lastProgress >= 5 || payload.status !== "running") {
          pushMessage("system", "progress", `[${pct}%] ${step}`);
          lastProgress = pct;
        }
      }
      if (payload.status !== "running") {
        localStorage.removeItem(TASK_STORAGE_KEY(projectId.value));
        return payload;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (msg.includes("404") || msg.toLowerCase().includes("not found")) {
        localStorage.removeItem(TASK_STORAGE_KEY(projectId.value));
        return null;
      }
      throw err;
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
  // Timeout: task is still running on backend, don't mark as lost
  pushMessage("system", "status", "工作流仍在运行中（已超出前端轮询时限），请稍后刷新页面查看结果。");
  return null;
}

/* ── Data loading ──────────────────────────────── */
async function loadAll() {
  const id = projectId.value;
  project.value = await apiRequest<Project>(`/api/projects/${id}`);
  papers.value = await apiRequest<Paper[]>(`/api/projects/${id}/papers`);
  evidence.value = await apiRequest<EvidenceCard[]>(`/api/projects/${id}/evidence`);
  drafts.value = await apiRequest<Draft[]>(`/api/projects/${id}/drafts`);
  issues.value = await apiRequest<ReviewIssue[]>(`/api/projects/${id}/review-issues`);
}

async function refreshDrafts() {
  drafts.value = await apiRequest<Draft[]>(`/api/projects/${projectId.value}/drafts`);
}

async function refreshIssues() {
  issues.value = await apiRequest<ReviewIssue[]>(`/api/projects/${projectId.value}/review-issues`);
}

/* ── Actions ───────────────────────────────────── */
async function runAutoWorkflow() {
  if (!project.value || busy.value) return;
  busy.value = true;
  error.value = "";
  pushMessage("user", "command", "启动全自动流程");
  pushMessage("system", "status", "正在初始化全自动工作流…");

  try {
    const payload = await apiRequest<{ task_id: string }>(
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

    localStorage.setItem(TASK_STORAGE_KEY(projectId.value), payload.task_id);
    pushMessage("system", "status", `任务 ${payload.task_id.slice(0, 8)} 已提交`);

    const result = await pollTask(payload.task_id);
    if (!result) {
      // pollTask timed out or returned 404 — still try to load latest data
      await loadAll();
      if (drafts.value.length > 0) {
        pushMessage("system", "status", "工作流可能仍在后端运行中，已刷新当前数据。");
      }
      return;
    }

    if (result.status === "completed") {
      const r = result.result;
      // Emit agent messages for each stage
      pushMessage("research", "search", "论文检索完成。", {
        total: r.total_papers ?? 0,
        auto_selected: r.auto_selected_count ?? 0,
        selected: r.selected_count ?? 0
      });

      pushMessage("evidence", "evidence", "证据卡构建完成。", {
        count: r.evidence_count ?? 0
      });

      // Load the new draft
      await refreshDrafts();
      await refreshIssues();
      const newDraft = drafts.value[0];
      if (newDraft) {
        pushMessage("writing", "draft", `草稿生成完成（v${newDraft.version}）。`, {
          version: newDraft.version,
          title: newDraft.title || "草稿"
        }, newDraft.id);
      }

      const issueCount = Number(r.review_issue_count ?? 0);
      const criticalCount = Number(r.critical_issue_count ?? 0);
      const qg = (r.quality_gate ?? {}) as Record<string, unknown>;
      pushMessage("review", "review", "多智能体辩论审查完成。", {
        issue_count: issueCount,
        critical_count: criticalCount,
        issues: issues.value.slice(0, 5),
        debate_log: qg.debate_log,
        consensus_count: qg.debate_consensus_count,
        disputed_count: qg.debate_disputed_count,
        overall_score: qg.overall_score,
        evidence_coverage: qg.evidence_coverage,
        logic_score: qg.logic_score,
        style_score: qg.style_score,
        revision_rounds: r.revision_rounds_executed,
      });

      if (r.revised_draft_id) {
        const revisedDraft = drafts.value.find((d) => d.id === r.revised_draft_id) ?? drafts.value[0];
        if (revisedDraft) {
          pushMessage("editor", "revision", `修订稿已生成（v${revisedDraft.version}）。`, {
            version: revisedDraft.version,
            title: revisedDraft.title || "修订稿"
          }, revisedDraft.id);
        }
      }

      const pubReady = Boolean(r.publication_prepared ?? false);
      pushMessage("system", "status",
        pubReady ? "全流程完成，已达终稿门禁。" : "全流程完成，尚未达到终稿门禁。"
      );
    } else {
      const failMsg = typeof result.result?.message === "string" ? result.result.message : "未知错误";
      pushMessage("system", "status", `全自动流程失败：${failMsg}`);
    }
    await loadAll();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "全自动流程失败";
    pushMessage("system", "status", `错误：${error.value}`);
  } finally {
    busy.value = false;
  }
}

async function doSearch() {
  if (busy.value) return;
  busy.value = true;
  pushMessage("user", "command", "检索论文");
  pushMessage("research", "status", "正在检索论文…");
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/search-papers`,
      { method: "POST", body: "{}" }
    );
    const result = await pollTask(payload.task_id);
    await loadAll();
    pushMessage("research", "search", "论文检索完成。", {
      total: papers.value.length,
      auto_selected: papers.value.filter((p) => p.selected).length
    });
  } catch (err) {
    pushMessage("system", "status", `检索失败：${err instanceof Error ? err.message : "未知"}`);
  } finally {
    busy.value = false;
  }
}

async function doEvidence() {
  if (busy.value) return;
  busy.value = true;
  pushMessage("user", "command", "构建证据卡");
  pushMessage("evidence", "status", "正在从已选论文构建证据卡…");
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/build-evidence`,
      { method: "POST", body: JSON.stringify({ max_cards: 120, only_selected: true }) }
    );
    await pollTask(payload.task_id);
    await loadAll();
    pushMessage("evidence", "evidence", "证据卡构建完成。", { count: evidence.value.length });
  } catch (err) {
    pushMessage("system", "status", `构建证据失败：${err instanceof Error ? err.message : "未知"}`);
  } finally {
    busy.value = false;
  }
}

async function doDraft() {
  if (busy.value) return;
  busy.value = true;
  pushMessage("user", "command", "生成草稿");
  pushMessage("writing", "status", "正在基于证据生成草稿…");
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/generate-draft`,
      { method: "POST", body: JSON.stringify({ title: project.value?.title ?? "Draft" }) }
    );
    await pollTask(payload.task_id);
    await refreshDrafts();
    const newDraft = drafts.value[0];
    if (newDraft) {
      pushMessage("writing", "draft", `草稿 v${newDraft.version} 已生成。`, {
        version: newDraft.version,
        title: newDraft.title || "草稿"
      }, newDraft.id);
    }
  } catch (err) {
    pushMessage("system", "status", `生成草稿失败：${err instanceof Error ? err.message : "未知"}`);
  } finally {
    busy.value = false;
  }
}

async function doReview() {
  if (busy.value || !drafts.value[0]) return;
  busy.value = true;
  pushMessage("user", "command", "审查草稿");
  pushMessage("review", "status", "正在执行多智能体辩论审查…");
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/review-draft`,
      { method: "POST", body: JSON.stringify({ draft_id: drafts.value[0].id }) }
    );
    await pollTask(payload.task_id);
    await refreshIssues();
    pushMessage("review", "review", "审查完成。", {
      issue_count: issues.value.length,
      critical_count: issues.value.filter((i) => i.severity === "critical").length,
      issues: issues.value.slice(0, 5)
    });
  } catch (err) {
    pushMessage("system", "status", `审查失败：${err instanceof Error ? err.message : "未知"}`);
  } finally {
    busy.value = false;
  }
}

async function doRevise() {
  if (busy.value || !drafts.value[0]) return;
  busy.value = true;
  pushMessage("user", "command", "生成修订稿");
  pushMessage("editor", "status", "正在根据审查意见生成修订稿…");
  try {
    const payload = await apiRequest<{ task_id: string }>(
      `/api/projects/${projectId.value}/revise-draft`,
      { method: "POST", body: JSON.stringify({ draft_id: drafts.value[0].id }) }
    );
    await pollTask(payload.task_id);
    await refreshDrafts();
    const newDraft = drafts.value[0];
    if (newDraft) {
      pushMessage("editor", "revision", `修订稿 v${newDraft.version} 已生成。`, {
        version: newDraft.version,
        title: newDraft.title || "修订稿"
      }, newDraft.id);
    }
  } catch (err) {
    pushMessage("system", "status", `修订失败：${err instanceof Error ? err.message : "未知"}`);
  } finally {
    busy.value = false;
  }
}

function handleAction(action: string) {
  const handlers: Record<string, () => void> = {
    auto: runAutoWorkflow,
    search: doSearch,
    evidence: doEvidence,
    draft: doDraft,
    review: doReview,
    revise: doRevise
  };
  handlers[action]?.();
}

function handleCommand(text: string) {
  pushMessage("user", "command", text);
  // Simple intent matching for natural language commands
  const lower = text.toLowerCase();
  if (lower.includes("全自动") || lower.includes("一键")) {
    runAutoWorkflow();
  } else if (lower.includes("检索") || lower.includes("搜索") || lower.includes("论文")) {
    doSearch();
  } else if (lower.includes("证据")) {
    doEvidence();
  } else if (lower.includes("草稿") || lower.includes("写作") || lower.includes("生成")) {
    doDraft();
  } else if (lower.includes("审查") || lower.includes("检查") || lower.includes("review")) {
    doReview();
  } else if (lower.includes("修订") || lower.includes("修改") || lower.includes("改")) {
    doRevise();
  } else if (lower.includes("导出") || lower.includes("export")) {
    doExport();
  } else {
    pushMessage("system", "status",
      `收到指令：「${text}」。你可以试试：全自动、检索论文、构建证据、生成草稿、审查草稿、生成修订、导出。`
    );
  }
}

function doExport() {
  const base = getApiBase();
  const formats = ["markdown", "docx", "pdf", "bibtex"];
  pushMessage("system", "export",
    "导出链接已生成（在新标签页打开）：\n" +
    formats.map((f) => `${f}: ${base}/api/projects/${projectId.value}/export/${f}`).join("\n")
  );
  formats.forEach((f) => {
    window.open(`${base}/api/projects/${projectId.value}/export/${f}`, "_blank");
  });
}

function onSelectDraft(draftId: string) {
  selectedDraftId.value = draftId;
  // Find the previous version for comparison
  const idx = drafts.value.findIndex((d) => d.id === draftId);
  if (idx >= 0 && idx < drafts.value.length - 1) {
    compareDraftId.value = drafts.value[idx + 1].id; // drafts sorted newest first
  } else {
    compareDraftId.value = "";
  }
  showPanel.value = true;
}

function clearChatHistory() {
  if (!confirm("确定清除当前项目的所有对话历史？此操作不可恢复。")) return;
  messages.value = [];
  clearMessages();
  pushMessage("system", "status", "对话历史已清除。");
}

/* ── Lifecycle ─────────────────────────────────── */
onMounted(async () => {
  try {
    await loadAll();

    // Restore persisted messages
    const saved = loadMessages();
    if (saved.length > 0) {
      // Restore history silently — update the first status message with current counts
      messages.value = [...saved];
      const statusIdx = messages.value.findIndex((m) => m.agent === "system" && m.type === "status");
      const countInfo = `当前 ${papers.value.length} 篇论文、${evidence.value.length} 张证据卡、${drafts.value.length} 个草稿版本。`;
      if (statusIdx >= 0) {
        // Update existing status message in-place (not pushMessage, to avoid save loop)
        messages.value[statusIdx] = {
          ...messages.value[statusIdx],
          text: countInfo
        };
      } else {
        // No existing status message — prepend one without triggering watcher redundantly
        messages.value.unshift({
          id: msgId(),
          agent: "system",
          type: "status",
          text: countInfo,
          timestamp: Date.now()
        });
      }
      saveMessages();
    } else {
      pushMessage("system", "status",
        `已加载项目「${project.value?.title}」。当前 ${papers.value.length} 篇论文、${evidence.value.length} 张证据卡、${drafts.value.length} 个草稿版本。`
      );
    }

    // Emit latest draft as message (only if not already in history)
    if (drafts.value.length > 0) {
      const latest = drafts.value[0];
      const alreadyHasDraftMsg = messages.value.some(
        (m) => m.draftId === latest.id && m.type === "draft"
      );
      if (!alreadyHasDraftMsg) {
        pushMessage("writing", "draft", `最新草稿 v${latest.version}。`, {
          version: latest.version,
          title: latest.title || "草稿"
        }, latest.id);
      }
    }

    // Resume stored task
    const storedTaskId = localStorage.getItem(TASK_STORAGE_KEY(projectId.value));
    if (storedTaskId) {
      busy.value = true;
      pushMessage("system", "status", `恢复任务 ${storedTaskId.slice(0, 8)} 轮询…`);
      try {
        // Pre-fetch to skip past existing debate logs (avoid re-emitting)
        const preTask = await apiRequest<TaskPayload>(`/api/tasks/${storedTaskId}`);
        const existingLogCount = preTask?.logs?.length ?? 0;
        await pollTask(storedTaskId, 30 * 60 * 1000, existingLogCount);
        await loadAll();
      } catch {
        // ignore
      } finally {
        busy.value = false;
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "加载失败";
  }
});

// Auto-scroll when messages change
watch(messages, scrollToBottom, { deep: true });
</script>

<template>
  <section class="workspace">
    <!-- ── Chat column ── -->
    <div class="chat-col">
      <header class="chat-header">
        <button class="back-btn" type="button" @click="router.push('/')">
          &larr; 返回
        </button>
        <h2 v-if="project">{{ project.title }}</h2>
        <div class="header-stats">
          <span class="stat">{{ papers.length }} 论文</span>
          <span class="stat">{{ evidence.length }} 证据</span>
          <span class="stat">{{ drafts.length }} 草稿</span>
        </div>
        <button
          v-if="messages.length > 1"
          class="clear-btn"
          type="button"
          title="清除对话历史"
          @click="clearChatHistory"
        >
          清除对话
        </button>
      </header>

      <div ref="chatContainer" class="chat-stream">
        <AgentMessage
          v-for="msg in messages"
          :key="msg.id"
          :message="msg"
          @select-draft="onSelectDraft"
        />
        <div v-if="busy" class="typing-indicator">
          <span class="dot" /><span class="dot" /><span class="dot" />
          <span class="typing-text">处理中…</span>
        </div>
      </div>

      <ChatComposer @action="handleAction" @command="handleCommand" />
    </div>

    <!-- ── Article panel ── -->
    <transition name="slide-panel">
      <ArticlePanel
        v-if="showPanel && selectedDraft"
        :current-md="selectedDraft.content_md"
        :previous-md="compareDraft?.content_md ?? ''"
        :title="selectedDraft.title || '草稿'"
        :version="selectedDraft.version"
        :quality-score="selectedDraft.quality_score"
        :source-breakdown="sourceBreakdown"
        class="article-col"
      />
    </transition>

    <p v-if="error" class="error-toast">{{ error }}</p>
  </section>
</template>

<style scoped>
.workspace {
  display: flex;
  height: calc(100vh - 3.6rem);
  border: 1px solid var(--line, #d4dbe8);
  border-radius: var(--radius-lg, 20px);
  overflow: hidden;
  background: var(--surface, #fffef8);
  box-shadow: var(--shadow-md);
}

/* ── Chat column ── */
.chat-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid var(--line-soft, #e4e9f1);
  background: var(--surface-strong, #fff);
  flex-shrink: 0;
}

.back-btn {
  border: 0;
  background: transparent;
  color: var(--muted, #627191);
  cursor: pointer;
  font-size: 0.86rem;
  padding: 0;
  transition: color 140ms ease;
  flex-shrink: 0;
}

.back-btn:hover {
  color: var(--accent, #0d7c75);
}

.chat-header h2 {
  margin: 0;
  font-size: 0.95rem;
  font-weight: 600;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-display, "Space Grotesk", sans-serif);
}

.header-stats {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.stat {
  font-size: 0.74rem;
  color: var(--muted-soft, #8b96ad);
  background: rgba(21, 29, 46, 0.03);
  border-radius: 6px;
  padding: 0.15rem 0.4rem;
}

.clear-btn {
  margin-left: auto;
  font-size: 0.72rem;
  color: var(--muted, #627191);
  background: transparent;
  border: 1px solid var(--line-soft, #e4e9f1);
  border-radius: 6px;
  padding: 0.2rem 0.55rem;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 140ms ease;
}

.clear-btn:hover {
  color: var(--danger, #b42318);
  border-color: var(--danger, #b42318);
  background: rgba(180, 35, 24, 0.04);
}

/* ── Chat stream ── */
.chat-stream {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 0.9rem;
  display: grid;
  gap: 0.85rem;
  align-content: start;
}

/* ── Typing indicator ── */
.typing-indicator {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.5rem 0.9rem;
  color: var(--muted-soft, #8b96ad);
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--muted-soft, #8b96ad);
  animation: typing-bounce 1.2s ease-in-out infinite;
}

.dot:nth-child(2) {
  animation-delay: 0.15s;
}

.dot:nth-child(3) {
  animation-delay: 0.3s;
}

.typing-text {
  font-size: 0.82rem;
  margin-left: 0.3rem;
}

@keyframes typing-bounce {
  0%, 80%, 100% {
    opacity: 0.3;
    transform: scale(0.8);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

/* ── Article panel ── */
.article-col {
  width: min(480px, 42%);
  flex-shrink: 0;
}

/* ── Panel transition ── */
.slide-panel-enter-active,
.slide-panel-leave-active {
  transition: all 280ms cubic-bezier(0.16, 1, 0.3, 1);
}

.slide-panel-enter-from,
.slide-panel-leave-to {
  opacity: 0;
  transform: translateX(20px);
}

/* ── Error toast ── */
.error-toast {
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  padding: 0.65rem 1rem;
  border-radius: var(--radius-md, 14px);
  background: var(--danger-light, #fef0ee);
  color: var(--danger, #b42318);
  border: 1px solid rgba(180, 35, 24, 0.2);
  font-size: 0.88rem;
  z-index: 200;
  animation: rise-in 200ms ease;
  margin: 0;
}

@media (max-width: 800px) {
  .workspace {
    flex-direction: column;
    height: calc(100vh - 2.8rem);
  }

  .article-col {
    width: 100% !important;
    max-height: 50vh;
    border-left: 0 !important;
    border-top: 1px solid var(--line, #d4dbe8);
  }

  .header-stats {
    display: none;
  }
}
</style>
