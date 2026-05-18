<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId ?? ""));

const navItems = computed(() => {
  if (!projectId.value) return [];
  return [
    { label: "总览", to: `/projects/${projectId.value}` },
    { label: "论文库", to: `/projects/${projectId.value}/papers` },
    { label: "证据板", to: `/projects/${projectId.value}/evidence` },
    { label: "草稿", to: `/projects/${projectId.value}/drafts` },
    { label: "终稿", to: `/projects/${projectId.value}/final` },
    { label: "审查", to: `/projects/${projectId.value}/review` }
  ];
});
</script>

<template>
  <div class="layout">
    <aside class="rail">
      <RouterLink class="brand" to="/">
        <span class="mark">PF</span>
        <span class="word">
          <strong>PaperForge 文铸</strong>
          <small>Evidence-grounded Writing Console</small>
        </span>
      </RouterLink>

      <nav v-if="navItems.length" class="menu">
        <RouterLink v-for="item in navItems" :key="item.to" class="menu-item" :to="item.to">
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
      <div v-else class="empty-state">创建项目后，这里会出现完整的写作流程导航。</div>

      <div class="global-nav">
        <RouterLink class="menu-item" to="/llm-settings">
          <span>模型配置</span>
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
  grid-template-columns: clamp(232px, 23vw, 290px) minmax(0, 1fr);
}

.rail {
  position: sticky;
  top: 0;
  align-self: start;
  height: 100vh;
  overflow-y: auto;
  padding: 1.3rem 1rem;
  color: #ebfffd;
  border-right: 1px solid rgba(255, 255, 255, 0.1);
  background:
    radial-gradient(140px 120px at 88% 12%, rgba(235, 189, 108, 0.18) 0%, transparent 75%),
    linear-gradient(180deg, #0f1725 0%, #122336 60%, #183148 100%);
}

.brand {
  text-decoration: none;
  color: inherit;
  display: flex;
  gap: 0.75rem;
  align-items: center;
  margin-bottom: 1.4rem;
}

.mark {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  font: 700 1rem/1 "Space Grotesk", "Noto Sans SC", sans-serif;
  color: #102239;
  background: linear-gradient(140deg, #c8f2ed 0%, #fedca7 100%);
}

.word {
  display: grid;
}

.word strong {
  letter-spacing: 0.02em;
}

.word small {
  color: #b8d6e4;
  font-size: 0.74rem;
}

.menu {
  display: grid;
  gap: 0.46rem;
}

.menu-item {
  text-decoration: none;
  color: #d8eef9;
  border: 1px solid rgba(153, 197, 220, 0.16);
  background: rgba(97, 138, 165, 0.1);
  border-radius: 12px;
  padding: 0.56rem 0.7rem;
  transition: transform 160ms ease, background 160ms ease, color 160ms ease;
}

.menu-item:hover {
  transform: translateX(3px);
  background: rgba(111, 169, 203, 0.24);
}

.menu-item.router-link-active {
  color: #ffffff;
  border-color: rgba(178, 230, 240, 0.44);
  background: linear-gradient(90deg, rgba(34, 140, 155, 0.5) 0%, rgba(33, 103, 152, 0.35) 100%);
}

.empty-state {
  border: 1px dashed rgba(172, 201, 219, 0.45);
  border-radius: 12px;
  padding: 0.8rem;
  color: #c6ddeb;
  font-size: 0.9rem;
}

.global-nav {
  margin-top: auto;
  padding-top: 1rem;
  border-top: 1px solid rgba(153, 197, 220, 0.16);
}

.main {
  padding: 1.1rem 1.1rem 1.8rem;
}

.canvas {
  animation: rise-in 280ms ease;
}

@media (max-width: 760px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .rail {
    position: static;
    min-height: auto;
    border-right: 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  }
}
</style>
