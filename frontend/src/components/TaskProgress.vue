<script setup lang="ts">
import { computed } from "vue";

import type { TaskPayload } from "../types";

const props = defineProps<{ task: TaskPayload | null }>();

type FailureDetail = {
  code?: string;
  title?: string;
  message?: string;
  summary?: Record<string, unknown>;
  skipped_titles?: string[];
  failed_items?: Array<{ title?: string; error?: string }>;
  next_actions?: string[];
};

const tone = computed(() => {
  if (!props.task) return "idle";
  if (props.task.status === "failed") return "failed";
  if (props.task.status === "completed") return "done";
  return "running";
});

const statusText = computed(() => {
  if (!props.task) return "";
  if (props.task.status === "failed") return "Failed";
  if (props.task.status === "completed") return "Completed";
  return "Running";
});

const failureDetail = computed<FailureDetail | null>(() => {
  if (!props.task || props.task.status !== "failed") return null;
  const result = props.task.result;
  if (!result || typeof result !== "object") return null;
  return result as FailureDetail;
});

const summaryEntries = computed(() => {
  const summary = failureDetail.value?.summary;
  if (!summary || typeof summary !== "object") return [];
  return Object.entries(summary).filter(([, value]) => value !== null && value !== undefined);
});
</script>

<template>
  <section v-if="task" class="task" :class="tone">
    <header class="head">
      <div class="id">Task {{ task.task_id.slice(0, 8) }}</div>
      <span class="state">{{ statusText }}</span>
    </header>

    <p class="step">{{ task.current_step }}</p>

    <div class="bar">
      <div class="fill" :style="{ width: `${task.progress}%` }" />
    </div>
    <div class="percent">{{ task.progress }}%</div>

    <section v-if="failureDetail" class="failure-panel">
      <h4>{{ failureDetail.title || "Task failed" }}</h4>
      <p class="failure-message">{{ failureDetail.message || "No failure message returned by backend." }}</p>
      <p v-if="failureDetail.code" class="failure-code">Code: {{ failureDetail.code }}</p>

      <ul v-if="summaryEntries.length" class="summary-list">
        <li v-for="[key, value] in summaryEntries" :key="key">{{ key }}: {{ value }}</li>
      </ul>

      <div v-if="failureDetail.skipped_titles && failureDetail.skipped_titles.length > 0" class="failure-group">
        <h5>Skipped papers (no PDF)</h5>
        <ul>
          <li v-for="title in failureDetail.skipped_titles.slice(0, 6)" :key="title">{{ title }}</li>
        </ul>
      </div>

      <div v-if="failureDetail.failed_items && failureDetail.failed_items.length > 0" class="failure-group">
        <h5>Failed papers</h5>
        <ul>
          <li v-for="item in failureDetail.failed_items.slice(0, 6)" :key="`${item.title}-${item.error}`">
            {{ item.title }}: {{ item.error }}
          </li>
        </ul>
      </div>

      <div v-if="failureDetail.next_actions && failureDetail.next_actions.length > 0" class="failure-group">
        <h5>Next actions</h5>
        <ol>
          <li v-for="(action, idx) in failureDetail.next_actions" :key="`${idx}-${action}`">{{ action }}</li>
        </ol>
      </div>
    </section>

    <ul class="logs">
      <li v-for="(line, idx) in task.logs.slice(-8)" :key="idx">{{ line }}</li>
    </ul>
  </section>
</template>

<style scoped>
.task {
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 0.9rem;
  background: var(--surface-strong);
  display: grid;
  gap: 0.56rem;
}

.task.running {
  box-shadow: 0 8px 26px rgba(17, 76, 103, 0.1);
}

.task.done {
  border-color: rgba(20, 124, 95, 0.35);
}

.task.failed {
  border-color: rgba(180, 35, 24, 0.4);
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.id {
  font-family: "Space Grotesk", "Noto Sans SC", sans-serif;
  font-weight: 600;
}

.state {
  border-radius: 999px;
  font-size: 0.82rem;
  padding: 0.16rem 0.58rem;
  color: #17344c;
  background: #dff4ef;
}

.task.failed .state {
  background: #fde8e6;
  color: #8d1d14;
}

.task.done .state {
  background: #e4f7dd;
  color: #205420;
}

.step {
  margin: 0;
  color: #35506b;
}

.bar {
  height: 9px;
  overflow: hidden;
  border-radius: 999px;
  background: #e3e7ef;
}

.fill {
  height: 100%;
  background: linear-gradient(90deg, #0f7f78 0%, #db9b39 100%);
}

.percent {
  font-size: 0.82rem;
  color: #5c6882;
}

.logs {
  margin: 0;
  padding-left: 1rem;
  color: #465672;
  font-size: 0.88rem;
}

.failure-panel {
  border: 1px solid rgba(180, 35, 24, 0.28);
  border-radius: 12px;
  background: #fff4f2;
  padding: 0.7rem 0.8rem;
  display: grid;
  gap: 0.4rem;
}

.failure-panel h4,
.failure-panel h5 {
  margin: 0;
}

.failure-message {
  margin: 0;
  color: #57201b;
}

.failure-code {
  margin: 0;
  color: #7f231b;
  font-size: 0.86rem;
}

.summary-list,
.failure-group ul,
.failure-group ol {
  margin: 0;
  padding-left: 1rem;
}

.failure-group {
  display: grid;
  gap: 0.2rem;
}
</style>
