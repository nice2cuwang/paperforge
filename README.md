# PaperForge 文铸

PaperForge 文铸是一个研究写作辅助系统，不是自动发表系统。  
系统生成内容必须由人类作者审校、修订并承担责任。  
系统不会绕过付费墙，不会伪造引用，不会自动投稿。

## 当前实现进度

- 阶段 1-2：项目骨架 + 数据模型 + CRUD
- 阶段 3：论文检索与去重（OpenAlex/Crossref/arXiv）
- 阶段 4：PDF 上传、下载、解析、切块（chunk）
- 阶段 5：项目内 chunk 检索（词向量近似检索替代实现）
- 阶段 6：Evidence Card 自动构建
- 阶段 7：大纲与初稿生成（Draft 版本化）
- 阶段 8：审查与修订（review issues + revised draft）
- 阶段 9：前端六页工作流
- 阶段 10：导出 Markdown / DOCX / PDF / BibTeX / evidence_map / review_report

## 技术栈

- 后端：FastAPI + SQLAlchemy + Alembic
- 前端：Vue 3 + Vite + TypeScript + Vue Router
- 运行方式：Docker Compose（开发热更新）

## 快速启动（Anaconda + Docker）

1. 进入 Anaconda 环境（示例）：

```powershell
E:\Anaconda\Scripts\activate.bat
conda activate e:\github\PaperForge\.conda\envs\paperforge
```

2. 启动服务（热更新）：

```powershell
docker compose up -d --build
```

3. 访问：

- 前端：`http://127.0.0.1:5174`
- 后端健康检查：`http://127.0.0.1:8010/health`

### 检索/下载网络故障排查（SEARCH_NO_CANDIDATES / DOWNLOAD_FAILED）

如果后端日志出现 `127.0.0.1:7890`，说明容器里用了错误代理地址。  
在 Docker 中应使用 `host.docker.internal` 访问宿主机代理。

1. 在项目根目录 `.env` 增加：

```env
PAPERFORGE_PROXY_URL=http://host.docker.internal:7890
PAPERFORGE_PROXY_HOST=host.docker.internal
```

说明：后端默认不读取系统 `HTTP_PROXY/HTTPS_PROXY`（避免容器误用 `127.0.0.1`）。  
如需显式启用系统代理，再加：

```env
PAPERFORGE_USE_SYSTEM_PROXY=true
```

2. 重启后端服务：

```powershell
docker compose up -d --build backend
```

## 一键全自动用法

方式一（前端）：

1. 创建项目并进入项目详情页 `/projects/:projectId`
2. 点击 `0) 一键全自动`
3. 系统自动执行：检索 → 自动纳入 → 下载/解析 → 证据卡 → 草稿 → 审查 → 修订 → 导出投稿包

方式二（API）：

```powershell
curl -X POST "http://127.0.0.1:8010/api/projects/{project_id}/run-auto-workflow" `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"你的研究问题\",\"max_results\":25,\"auto_select_limit\":12,\"chunk_size\":900,\"max_cards\":120,\"auto_export\":true}"
```

导出文件默认写入：`backend/data/exports/{project_id}`

## 主要 API

### 基础 CRUD

- `POST /api/projects`
- `GET /api/projects`
- `GET /api/projects/{project_id}`
- `PATCH /api/projects/{project_id}`
- `DELETE /api/projects/{project_id}`
- `POST /api/projects/{project_id}/papers`
- `GET /api/projects/{project_id}/papers`
- `POST /api/papers/{paper_id}/chunks`
- `GET /api/papers/{paper_id}/chunks`
- `POST /api/projects/{project_id}/evidence`
- `GET /api/projects/{project_id}/evidence`
- `POST /api/projects/{project_id}/drafts`
- `GET /api/projects/{project_id}/drafts`
- `POST /api/projects/{project_id}/review-issues`
- `GET /api/projects/{project_id}/review-issues`

### 工作流 API

- `POST /api/projects/{project_id}/search-papers`
- `POST /api/projects/{project_id}/papers/upload`
- `POST /api/papers/{paper_id}/select`
- `POST /api/papers/{paper_id}/download`
- `POST /api/papers/{paper_id}/parse`
- `POST /api/projects/{project_id}/download-selected-papers` (批量自动下载并可选自动解析)
- `POST /api/projects/{project_id}/run-auto-workflow` (一键全自动主流程，含可选自动导出)
- `POST /api/projects/{project_id}/run-auto-workflow-async` (异步启动全自动，实时查看 `/api/tasks/{task_id}` 进度日志)
- `POST /api/projects/{project_id}/retrieve-chunks`
- `POST /api/projects/{project_id}/build-evidence`
- `POST /api/projects/{project_id}/generate-outline`
- `POST /api/projects/{project_id}/generate-draft`
- `POST /api/projects/{project_id}/review-draft`
- `POST /api/projects/{project_id}/revise-draft`
- `GET /api/tasks/{task_id}`

### 导出 API

- `POST /api/projects/{project_id}/export/markdown`
- `POST /api/projects/{project_id}/export/docx`
- `POST /api/projects/{project_id}/export/pdf`
- `POST /api/projects/{project_id}/export/bibtex`
- `POST /api/projects/{project_id}/export/evidence-map`
- `POST /api/projects/{project_id}/export/review-report`

## 前端页面

- `/`：ProjectList
- `/projects/:projectId`：ProjectDetail
- `/projects/:projectId/papers`：PaperLibrary
- `/projects/:projectId/evidence`：EvidenceBoard
- `/projects/:projectId/drafts`：DraftEditor
- `/projects/:projectId/review`：ReviewPanel

## 测试

```powershell
cd backend
..\.conda\envs\paperforge\python.exe -m pytest -q tests
```
