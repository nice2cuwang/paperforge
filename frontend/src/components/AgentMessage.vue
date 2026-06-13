<script setup lang="ts">
import { computed } from "vue";
import type { ChatMessage } from "../types";

const props = defineProps<{
  message: ChatMessage;
}>();

const emit = defineEmits<{
  (e: "select-draft", draftId: string): void;
}>();

const agentMeta: Record<string, { name: string; color: string; bg: string; emoji: string }> = {
  research: { name: "检索员", color: "#1a65c4", bg: "#e8f0fe", emoji: "🔍" },
  evidence: { name: "证据师", color: "#0d7c75", bg: "#e0f5f0", emoji: "📋" },
  writing: { name: "撰稿人", color: "#6b21a8", bg: "#f3e8ff", emoji: "✍️" },
  review: { name: "审查团", color: "#b5791f", bg: "#fef3e0", emoji: "🔍" },
  editor: { name: "编辑官", color: "#047857", bg: "#e6f7ed", emoji: "📝" },
  user: { name: "你", color: "#374151", bg: "#f3f4f6", emoji: "👤" },
  system: { name: "系统", color: "#6b7280", bg: "#f9fafb", emoji: "⚙️" }
};

/* ── Debate agent definitions ── */
const debateAgents: Record<string, { color: string; bg: string; emoji: string; role: string }> = {
  "明鉴": { color: "#1a65c4", bg: "#e8f0fe", emoji: "🔍", role: "证据审查" },
  "持正": { color: "#047857", bg: "#e6f7ed", emoji: "⚖️", role: "逻辑审查" },
  "破壁": { color: "#b42318", bg: "#fef0ee", emoji: "⚔️", role: "对抗质疑" },
};

function parseDebateLog(text: string): { agentName: string; emoji: string; content: string } {
  // Matches: 📋 明鉴完成审查，发现 5 个问题
  // Or:      🔍 破壁发现 2 个盲区/隐患
  const m = text.match(/^(\S+)\s+(\S+?)(完成|开始|发现|审视)(.*)$/);
  if (m) {
    return { agentName: m[2], emoji: m[1], content: m[3] + m[4] };
  }
  return { agentName: "团队", emoji: "💬", content: text };
}

const debateAgentMeta = computed(() => {
  const parsed = parseDebateLog(props.message.text);
  return debateAgents[parsed.agentName] || { color: "#b5791f", bg: "#fef3e0", emoji: parsed.emoji, role: "助手" };
});

const debateParsed = computed(() => parseDebateLog(props.message.text));

function getMeta() {
  return agentMeta[props.message.agent] ?? agentMeta.system;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function formatReviewData(): string {
  const data = props.message.data;
  if (!data) return props.message.text;
  const issues = Number(data.issue_count ?? 0);
  const critical = Number(data.critical_count ?? 0);
  const score = data.overall_score != null ? Number(data.overall_score).toFixed(2) : null;
  const rounds = data.revision_rounds != null ? Number(data.revision_rounds) : null;
  let text = `${issues} 个问题（${critical} 个关键）`;
  if (score) text += `，综合评分 ${score}`;
  if (rounds && rounds > 0) text += `，经过 ${rounds} 轮修订`;
  text += "。";
  return text;
}

function getDebatePhases(): Array<{ label: string; detail: string }> {
  const log = props.message.data?.debate_log as any[] | undefined;
  if (!log || !Array.isArray(log)) return [];
  const roleLabels: Record<string, string> = {
    evidence_reviewer: "明鉴",
    logic_reviewer: "持正",
    challenger: "破壁",
  };
  return log.map((entry: any) => {
    if (entry.role) {
      const label = roleLabels[entry.role] || entry.role;
      const types = (entry.issue_types || entry.challenge_types || []).join("、");
      return { label, detail: `发现 ${entry.count ?? 0} 个问题${types ? "：" + types : ""}` };
    }
    if (entry.phase === "cross_review") {
      return { label: "交叉审查", detail: `证据补充 ${entry.evidence_supplements ?? 0}，逻辑补充 ${entry.logic_supplements ?? 0}` };
    }
    if (entry.phase === "consolidated") {
      return { label: "最终合并", detail: `${entry.consensus_count ?? 0} 个共识，${entry.disputed_count ?? 0} 个争议` };
    }
    return { label: entry.phase || "其他", detail: JSON.stringify(entry) };
  });
}

function getQualityScores(): Array<{ label: string; value: number; color: string }> {
  const data = props.message.data;
  if (!data) return [];
  const scores: Array<{ label: string; value: number; color: string }> = [];
  if (data.overall_score != null) scores.push({ label: "综合", value: Number(data.overall_score), color: "#4C72B0" });
  if (data.evidence_coverage != null) scores.push({ label: "证据", value: Number(data.evidence_coverage), color: "#55A868" });
  if (data.logic_score != null) scores.push({ label: "逻辑", value: Number(data.logic_score), color: "#DD8452" });
  if (data.style_score != null) scores.push({ label: "表达", value: Number(data.style_score), color: "#8172B3" });
  return scores;
}

function formatSearchData(): string {
  const data = props.message.data;
  if (!data) return props.message.text;
  return `找到 ${data.total ?? 0} 篇候选论文，自动纳入 ${data.auto_selected ?? 0} 篇。`;
}

function formatEvidenceData(): string {
  const data = props.message.data;
  if (!data) return props.message.text;
  return `生成 ${data.count ?? 0} 张证据卡。`;
}
</script>

<template>
  <div class="msg" :class="[message.agent, message.type]">
    <div
      class="avatar"
      :style="{
        background: message.type === 'debate' ? debateAgentMeta.bg : getMeta().bg,
        color: message.type === 'debate' ? debateAgentMeta.color : getMeta().color
      }"
    >
      {{ message.type === 'debate' ? debateAgentMeta.emoji : getMeta().emoji }}
    </div>

    <div class="bubble-wrap">
      <div class="meta-row">
        <strong :style="{ color: message.type === 'debate' ? debateAgentMeta.color : getMeta().color }">
          {{ message.type === 'debate' ? (debateParsed.agentName === '团队' ? debateParsed.agentName : `${debateParsed.agentName}（${debateAgentMeta.role}）`) : getMeta().name }}
        </strong>
        <span class="time">{{ formatTime(message.timestamp) }}</span>
      </div>

      <div class="bubble">
        <!-- Status / progress -->
        <template v-if="message.type === 'status' || message.type === 'progress'">
          <p class="status-text">{{ message.text }}</p>
        </template>

        <!-- Search results -->
        <template v-else-if="message.type === 'search'">
          <p>{{ formatSearchData() }}</p>
        </template>

        <!-- Evidence -->
        <template v-else-if="message.type === 'evidence'">
          <p>{{ formatEvidenceData() }}</p>
        </template>

        <!-- Draft version card -->
        <template v-else-if="message.type === 'draft' || message.type === 'revision'">
          <p>{{ message.text }}</p>
          <button
            class="draft-card"
            @click="message.draftId && emit('select-draft', message.draftId)"
          >
            <span class="draft-version">v{{ message.data?.version ?? '?' }}</span>
            <span class="draft-title">{{ message.data?.title || '草稿' }}</span>
            <span class="draft-hint">点击查看 / 对比</span>
          </button>
        </template>

        <!-- Debate real-time messages -->
        <template v-else-if="message.type === 'debate'">
          <p class="debate-text">{{ debateParsed.content }}</p>
        </template>

        <!-- Review issues -->
        <template v-else-if="message.type === 'review'">
          <p>{{ formatReviewData() }}</p>

          <!-- Quality scores -->
          <div v-if="getQualityScores().length" class="quality-scores">
            <span
              v-for="s in getQualityScores()"
              :key="s.label"
              class="score-badge"
              :style="{ borderColor: s.color }"
            >
              {{ s.label }} <strong>{{ (s.value * 100).toFixed(0) }}</strong>
            </span>
          </div>

          <!-- Debate phases -->
          <details v-if="getDebatePhases().length" class="debate-phases">
            <summary>查看辩论过程</summary>
            <div class="phase-list">
              <div v-for="(p, i) in getDebatePhases()" :key="i" class="phase-item">
                <span class="phase-label">{{ p.label }}</span>
                <span class="phase-detail">{{ p.detail }}</span>
              </div>
            </div>
          </details>

          <div v-if="message.data?.issues" class="issue-preview">
            <div
              v-for="(issue, idx) in (message.data.issues as any[]).slice(0, 3)"
              :key="idx"
              class="issue-chip"
              :class="issue.severity"
            >
              {{ issue.severity }} · {{ issue.issue_type }}
            </div>
          </div>
        </template>

        <!-- Export -->
        <template v-else-if="message.type === 'export'">
          <p>{{ message.text }}</p>
        </template>

        <!-- Command / user -->
        <template v-else>
          <p>{{ message.text }}</p>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg {
  display: flex;
  gap: 0.65rem;
  align-items: flex-start;
  animation: rise-in 250ms cubic-bezier(0.16, 1, 0.3, 1);
}

.msg.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 1.1rem;
  flex-shrink: 0;
}

.bubble-wrap {
  max-width: 72%;
  min-width: 180px;
}

.msg.user .bubble-wrap {
  align-items: flex-end;
}

.meta-row {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 3px;
  padding: 0 0.3rem;
}

.msg.user .meta-row {
  flex-direction: row-reverse;
}

.meta-row strong {
  font-size: 0.8rem;
  font-weight: 600;
}

.time {
  font-size: 0.72rem;
  color: var(--muted-soft, #8b96ad);
}

.bubble {
  border: 1px solid var(--line-soft, #e4e9f1);
  border-radius: 16px;
  padding: 0.7rem 0.85rem;
  background: var(--surface-strong, #fff);
  box-shadow: 0 1px 3px rgba(21, 29, 46, 0.03);
}

.msg.user .bubble {
  background: var(--accent-light, #e0f5f0);
  border-color: var(--accent-muted, #b8ded6);
}

.msg.system .bubble {
  background: var(--surface, #fffef8);
  border-style: dashed;
}

.bubble p {
  margin: 0;
  font-size: 0.9rem;
  line-height: 1.55;
  color: var(--ink-soft, #2a3550);
}

.status-text {
  font-size: 0.86rem !important;
  color: var(--muted, #627191) !important;
}

/* Draft card */
.draft-card {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  margin-top: 0.5rem;
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--accent-muted, #b8ded6);
  border-radius: 12px;
  background: var(--accent-light, #e0f5f0);
  cursor: pointer;
  transition: all 160ms ease;
  width: 100%;
  text-align: left;
}

.draft-card:hover {
  border-color: var(--accent, #0d7c75);
  box-shadow: 0 2px 8px rgba(13, 124, 117, 0.12);
  transform: translateY(-1px);
}

.draft-version {
  font: 700 0.78rem/1 var(--font-display, "Space Grotesk", sans-serif);
  color: var(--accent, #0d7c75);
  background: rgba(13, 124, 117, 0.1);
  border-radius: 6px;
  padding: 0.2rem 0.45rem;
}

.draft-title {
  flex: 1;
  font-size: 0.86rem;
  font-weight: 500;
  color: var(--ink-soft, #2a3550);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.draft-hint {
  font-size: 0.74rem;
  color: var(--muted-soft, #8b96ad);
  flex-shrink: 0;
}

/* Issue chips */
.issue-preview {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.5rem;
}

.issue-chip {
  font-size: 0.74rem;
  border-radius: 6px;
  padding: 0.15rem 0.45rem;
  border: 1px solid var(--line-soft, #e4e9f1);
  background: var(--surface, #fffef8);
  color: var(--muted, #627191);
}

.issue-chip.critical {
  border-color: rgba(180, 35, 24, 0.3);
  background: var(--danger-light, #fef0ee);
  color: var(--danger, #b42318);
}

.issue-chip.major {
  border-color: rgba(181, 121, 31, 0.3);
  background: var(--signal-light, #fef3e0);
  color: var(--signal, #b5791f);
}

/* Quality scores */
.quality-scores {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.45rem;
}

.score-badge {
  font-size: 0.74rem;
  border-radius: 6px;
  padding: 0.15rem 0.5rem;
  border: 1.5px solid #ccc;
  background: var(--surface-strong, #fff);
  color: var(--muted, #627191);
}

.score-badge strong {
  color: var(--ink-soft, #2a3550);
  margin-left: 0.15rem;
}

/* Debate phases */
.debate-phases {
  margin-top: 0.5rem;
  font-size: 0.82rem;
}

.debate-phases summary {
  cursor: pointer;
  color: var(--accent, #0d7c75);
  font-weight: 500;
  user-select: none;
  padding: 0.2rem 0;
}

.debate-phases summary:hover {
  text-decoration: underline;
}

.phase-list {
  margin-top: 0.4rem;
  padding-left: 0.3rem;
  border-left: 2px solid var(--line-soft, #e4e9f1);
}

.phase-item {
  display: flex;
  gap: 0.5rem;
  padding: 0.3rem 0.5rem;
  align-items: baseline;
}

.phase-label {
  font-weight: 600;
  font-size: 0.78rem;
  color: var(--ink-soft, #2a3550);
  white-space: nowrap;
  min-width: 5.5em;
}

.phase-detail {
  font-size: 0.78rem;
  color: var(--muted, #627191);
}

/* Debate messages */
.msg.debate .bubble {
  background: linear-gradient(135deg, #fffef8 0%, #fef9ec 100%);
  border-color: #f0ddb8;
  border-left: 3px solid var(--accent, #0d7c75);
}

.debate-text {
  font-size: 0.88rem !important;
  line-height: 1.55;
  color: var(--ink-soft, #2a3550) !important;
}

@media (max-width: 760px) {
  .bubble-wrap {
    max-width: 85%;
  }
}
</style>
