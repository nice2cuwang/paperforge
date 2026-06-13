import { createRouter, createWebHistory } from "vue-router";

import ChatWorkspace from "./views/ChatWorkspace.vue";
import DraftEditor from "./views/DraftEditor.vue";
import EvidenceBoard from "./views/EvidenceBoard.vue";
import FinalDocument from "./views/FinalDocument.vue";
import PaperLibrary from "./views/PaperLibrary.vue";
import ProjectDetail from "./views/ProjectDetail.vue";
import ProjectList from "./views/ProjectList.vue";
import LLMSettings from "./views/LLMSettings.vue";
import ReviewPanel from "./views/ReviewPanel.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "projects", component: ProjectList },
    { path: "/projects/:projectId", name: "project-detail", component: ProjectDetail, props: true },
    { path: "/projects/:projectId/chat", name: "chat-workspace", component: ChatWorkspace, props: true },
    { path: "/projects/:projectId/papers", name: "paper-library", component: PaperLibrary, props: true },
    { path: "/projects/:projectId/evidence", name: "evidence-board", component: EvidenceBoard, props: true },
    { path: "/projects/:projectId/drafts", name: "draft-editor", component: DraftEditor, props: true },
    { path: "/projects/:projectId/final", name: "final-document", component: FinalDocument, props: true },
    { path: "/projects/:projectId/review", name: "review-panel", component: ReviewPanel, props: true },
    { path: "/llm-settings", name: "llm-settings", component: LLMSettings }
  ]
});

export default router;
