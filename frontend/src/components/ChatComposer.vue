<script setup lang="ts">
import { ref } from "vue";

const emit = defineEmits<{
  (e: "command", cmd: string): void;
  (e: "action", action: string): void;
}>();

const inputText = ref("");

const quickActions = [
  { id: "auto", label: "全自动流程", primary: true },
  { id: "search", label: "检索论文" },
  { id: "evidence", label: "构建证据" },
  { id: "draft", label: "生成草稿" },
  { id: "review", label: "审查草稿" },
  { id: "revise", label: "生成修订" }
];

function sendCommand() {
  const text = inputText.value.trim();
  if (!text) return;
  emit("command", text);
  inputText.value = "";
}

function triggerAction(action: string) {
  emit("action", action);
}
</script>

<template>
  <footer class="composer">
    <div class="actions-row">
      <button
        v-for="act in quickActions"
        :key="act.id"
        type="button"
        class="action-chip"
        :class="{ primary: act.primary }"
        @click="triggerAction(act.id)"
      >
        {{ act.label }}
      </button>
    </div>

    <div class="input-row">
      <input
        v-model="inputText"
        type="text"
        placeholder="输入指令，如「帮我修改引言部分」「重新检索关于XX的论文」…"
        @keydown.enter="sendCommand"
      />
      <button type="button" class="send-btn" :disabled="!inputText.trim()" @click="sendCommand">
        发送
      </button>
    </div>
  </footer>
</template>

<style scoped>
.composer {
  border-top: 1px solid var(--line, #d4dbe8);
  background: var(--surface, #fffef8);
  padding: 0.65rem 0.9rem 0.8rem;
  display: grid;
  gap: 0.55rem;
  flex-shrink: 0;
}

.actions-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.action-chip {
  border: 1px solid var(--line-soft, #e4e9f1);
  border-radius: 99px;
  padding: 0.32rem 0.68rem;
  background: var(--surface-strong, #fff);
  color: var(--ink-soft, #2a3550);
  font-size: 0.78rem;
  cursor: pointer;
  transition: all 150ms ease;
  white-space: nowrap;
}

.action-chip:hover {
  border-color: var(--accent-muted, #b8ded6);
  background: var(--accent-light, #e0f5f0);
}

.action-chip.primary {
  border-color: var(--accent, #0d7c75);
  background: var(--accent, #0d7c75);
  color: #fff;
  font-weight: 500;
}

.action-chip.primary:hover {
  background: var(--accent-strong, #09625c);
}

.input-row {
  display: flex;
  gap: 0.5rem;
  align-items: center;
}

.input-row input {
  flex: 1;
  border: 1px solid var(--line, #d4dbe8);
  border-radius: 12px;
  padding: 0.55rem 0.8rem;
  font: inherit;
  font-size: 0.9rem;
  background: var(--surface-strong, #fff);
  color: var(--ink, #151d2e);
  transition: border-color 150ms ease, box-shadow 150ms ease;
}

.input-row input:focus {
  border-color: var(--accent, #0d7c75);
  box-shadow: 0 0 0 3px rgba(13, 124, 117, 0.1);
  outline: none;
}

.input-row input::placeholder {
  color: var(--muted-soft, #8b96ad);
}

.send-btn {
  border: 0;
  border-radius: 10px;
  padding: 0.52rem 0.9rem;
  background: var(--accent, #0d7c75);
  color: #fff;
  font-size: 0.88rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 150ms ease;
  flex-shrink: 0;
}

.send-btn:hover:not(:disabled) {
  background: var(--accent-strong, #09625c);
}

.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
