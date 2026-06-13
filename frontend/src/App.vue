<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId ?? ""));

const navItems = computed(() => {
  if (!projectId.value) return [];
  return [
    { label: "对话", step: "💬", to: `/projects/${projectId.value}/chat` },
    { label: "总览", step: "1", to: `/projects/${projectId.value}` },
    { label: "论文库", step: "2", to: `/projects/${projectId.value}/papers` },
    { label: "证据板", step: "3", to: `/projects/${projectId.value}/evidence` },
    { label: "草稿", step: "4", to: `/projects/${projectId.value}/drafts` },
    { label: "终稿", step: "5", to: `/projects/${projectId.value}/final` },
    { label: "审查", step: "6", to: `/projects/${projectId.value}/review` }
  ];
});
</script>

<template>
  <div class="layout">
    <aside class="rail">
      <RouterLink class="brand" to="/">
        <span class="mark">PF</span>
        <span class="word">
          <strong>PaperForge</strong>
          <small>证据锚定写作工作台</small>
        </span>
      </RouterLink>

      <nav v-if="navItems.length" class="menu">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          class="menu-item"
          :to="item.to"
        >
          <span class="step-num">{{ item.step }}</span>
          <span class="step-label">{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div v-else class="empty-hint">
        <span class="hint-icon">&#9670;</span>
        创建项目后，这里会出现完整的写作流程导航。
      </div>

      <div class="global-nav">
        <RouterLink class="menu-item global" to="/llm-settings">
          <span class="step-num">&#9881;</span>
          <span class="step-label">模型配置</span>
        </RouterLink>
      </div>
    </aside>

    <main class="main">
      <div class="canvas">
        <RouterView />
      </div>
    </main>
  </div>
</template>

<style scoped>
.layout {
  min-height: 100vh;
  display: grid;
  grid-template-columns: clamp(220px, 22vw, 272px) minmax(0, 1fr);
}

.rail {
  position: sticky;
  top: 0;
  align-self: start;
  height: 100vh;
  overflow-y: auto;
  padding: 1.2rem 0.9rem;
  display: flex;
  flex-direction: column;
  color: #e4f3f0;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  background:
    radial-gradient(120px 100px at 90% 10%, rgba(220, 170, 90, 0.12) 0%, transparent 75%),
    linear-gradient(180deg, #0e1620 0%, #111f30 55%, #162940 100%);
}

.brand {
  text-decoration: none;
  color: inherit;
  display: flex;
  gap: 0.7rem;
  align-items: center;
  margin-bottom: 1.6rem;
  padding: 0.2rem 0.15rem;
}

.mark {
  width: 42px;
  height: 42px;
  border-radius: 13px;
  display: grid;
  place-items: center;
  font: 700 0.92rem/1 var(--font-display);
  color: #0e1620;
  background: linear-gradient(140deg, #b8ede5 0%, #f5d690 100%);
  flex-shrink: 0;
}

.word {
  display: grid;
  gap: 1px;
}

.word strong {
  font-size: 1.02rem;
  letter-spacing: 0.03em;
  font-family: var(--font-display);
}

.word small {
  color: #9bbac9;
  font-size: 0.72rem;
  letter-spacing: 0.02em;
}

.menu {
  display: grid;
  gap: 3px;
}

.menu-item {
  text-decoration: none;
  color: #c8e0ed;
  border: 1px solid transparent;
  background: rgba(80, 120, 150, 0.06);
  border-radius: 11px;
  padding: 0.52rem 0.65rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  transition: all 160ms ease;
  font-size: 0.92rem;
}

.menu-item:hover {
  background: rgba(100, 155, 185, 0.14);
  transform: translateX(2px);
}

.menu-item.router-link-active {
  color: #ffffff;
  border-color: rgba(160, 215, 225, 0.3);
  background: linear-gradient(90deg, rgba(20, 120, 130, 0.4) 0%, rgba(25, 90, 130, 0.25) 100%);
}

.step-num {
  width: 22px;
  height: 22px;
  border-radius: 7px;
  display: grid;
  place-items: center;
  font: 600 0.7rem/1 var(--font-display);
  background: rgba(160, 200, 220, 0.12);
  flex-shrink: 0;
}

.router-link-active .step-num {
  background: rgba(180, 240, 230, 0.25);
}

.empty-hint {
  border: 1px dashed rgba(150, 185, 205, 0.3);
  border-radius: 12px;
  padding: 0.9rem 0.75rem;
  color: #a8c6d6;
  font-size: 0.86rem;
  line-height: 1.5;
  display: grid;
  gap: 0.4rem;
}

.hint-icon {
  font-size: 0.7rem;
  opacity: 0.5;
}

.global-nav {
  margin-top: auto;
  padding-top: 1rem;
  border-top: 1px solid rgba(140, 180, 200, 0.1);
}

.menu-item.global {
  color: #a8c6d6;
}

.main {
  padding: 1.4rem 1.6rem 2rem;
  position: relative;
  z-index: 1;
}

.canvas {
  animation: rise-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
}

@media (max-width: 760px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .rail {
    position: static;
    min-height: auto;
    height: auto;
    border-right: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    flex-direction: row;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.8rem 1rem;
  }

  .brand {
    margin-bottom: 0;
  }

  .menu {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 4px;
  }

  .global-nav {
    margin-top: 0;
    padding-top: 0;
    border-top: 0;
    margin-left: auto;
  }

  .empty-hint {
    display: none;
  }
}
</style>
