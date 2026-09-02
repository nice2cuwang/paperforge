<script setup lang="ts">
import { computed, ref, watch } from "vue";

/**
 * Searchable model picker for the LLM settings modals.
 *
 * Replaces the native `<datalist>` (which filters options by the current
 * input text, so a filled field shows only the matching entry) and the old
 * wall of model chips. All catalog + live-fetched models live in one
 * filterable dropdown; free text is still allowed for custom model IDs.
 */

type Option = { id: string; label: string };

const props = defineProps<{
  modelValue: string;
  options: Option[];
  placeholder?: string;
  /** Forwarded to the inner input so the wrapping <label for="..."> works. */
  id?: string;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

const query = ref(props.modelValue);
const open = ref(false);
const activeIndex = ref(-1);
const inputEl = ref<HTMLInputElement | null>(null);

watch(
  () => props.modelValue,
  (v) => {
    if (v !== query.value) query.value = v;
  }
);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.options;
  return props.options.filter(
    (o) => o.id.toLowerCase().includes(q) || o.label.toLowerCase().includes(q)
  );
});

// Large providers can return 100+ models; rendering is cheap enough but the
// panel stays usable only with a cap.
const visible = computed(() => filtered.value.slice(0, 200));

/**
 * Open the dropdown programmatically - used after "刷新在线模型列表"
 * succeeds so the fetched list is immediately visible. With `clearQuery`
 * the current text filter is dropped (the full list shows); if nothing is
 * picked, blur restores the text from modelValue.
 */
function openPanel(clearQuery = false) {
  if (clearQuery) query.value = "";
  open.value = true;
  activeIndex.value = -1;
  inputEl.value?.focus();
}

defineExpose({ openPanel });

function onInput(e: Event) {
  const v = (e.target as HTMLInputElement).value;
  query.value = v;
  emit("update:modelValue", v);
  open.value = true;
  activeIndex.value = -1;
}

function pick(opt: Option) {
  query.value = opt.id;
  emit("update:modelValue", opt.id);
  open.value = false;
}

function onBlur() {
  // Delay so @mousedown on an option fires before the panel closes.
  window.setTimeout(() => {
    open.value = false;
    query.value = props.modelValue;
  }, 150);
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    open.value = false;
    return;
  }
  if (!open.value && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    open.value = true;
    return;
  }
  if (!open.value) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIndex.value = Math.min(activeIndex.value + 1, visible.value.length - 1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIndex.value = Math.max(activeIndex.value - 1, 0);
  } else if (e.key === "Enter" && activeIndex.value >= 0 && visible.value[activeIndex.value]) {
    e.preventDefault();
    pick(visible.value[activeIndex.value]);
  }
}
</script>

<template>
  <div class="combo">
    <input
      ref="inputEl"
      :id="id"
      type="text"
      class="combo-input"
      :value="query"
      :placeholder="placeholder || '输入以搜索模型，或直接填写模型 ID'"
      autocomplete="off"
      spellcheck="false"
      @input="onInput"
      @focus="open = true"
      @blur="onBlur"
      @keydown="onKeydown"
    />
    <div v-if="open" class="combo-panel" role="listbox">
      <div v-if="!visible.length" class="combo-empty">无匹配模型 — 将按输入的 ID 保存</div>
      <button
        v-for="(opt, i) in visible"
        :key="opt.id"
        type="button"
        class="combo-option"
        :class="{ active: i === activeIndex, selected: opt.id === modelValue }"
        role="option"
        @mousedown.prevent="pick(opt)"
        @mouseenter="activeIndex = i"
      >
        <span class="combo-option-id">{{ opt.id }}</span>
        <span v-if="opt.label !== opt.id" class="combo-option-label">{{ opt.label }}</span>
      </button>
      <div v-if="filtered.length > visible.length" class="combo-more">
        还有 {{ filtered.length - visible.length }} 个，输入关键字继续过滤
      </div>
    </div>
  </div>
</template>

<style scoped>
.combo {
  position: relative;
}

.combo-input {
  width: 100%;
}

.combo-panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 30;
  max-height: 260px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--border, #e5e2d9);
  border-radius: 10px;
  box-shadow: 0 10px 30px rgba(31, 41, 55, 0.14);
  padding: 4px;
}

.combo-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  width: 100%;
  text-align: left;
  padding: 6px 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
}

.combo-option.active {
  background: var(--accent-light, #f3efe4);
}

.combo-option.selected .combo-option-id {
  color: var(--accent-strong, #8a5a00);
  font-weight: 600;
}

.combo-option-id {
  font-size: 0.86rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

.combo-option-label {
  font-size: 0.74rem;
  color: var(--muted, #6b7280);
}

.combo-empty,
.combo-more {
  padding: 8px 10px;
  font-size: 0.78rem;
  color: var(--muted, #6b7280);
}
</style>
