# PaperForge 项目现状说明（供 ChatGPT 网页版研究模式）

更新时间：2026-05-22  
仓库路径：`e:\github\PaperForge`  
参考提交：`e375c1c`（当前工作区为进行中状态，存在未提交改动）  
文档目标：在不读取全仓库的前提下，让 ChatGPT 研究模式快速理解项目现状并给出下一阶段优化设计。

---

## 1. 项目定位（一句话）

PaperForge（文铸）是一个“证据驱动 + 引用核验 + 多轮审查 + 人工终审”的研究写作系统，目标是产出 **publication-preparation** 级稿件，而不是自动投稿系统。

---

## 2. 当前真实实现快照（As-Is）

## 2.1 技术栈与运行形态

- 后端：FastAPI + SQLAlchemy + Alembic
- 前端：Vue 3 + Vite + TypeScript
- 默认数据库：SQLite（`backend/data/paperforge.db`）
- 可选基础设施：PostgreSQL / Redis / Qdrant / MinIO / GROBID（通过 `docker-compose` 的 `infra` profile）
- 启动端口：前端 `5174`，后端 `8010`
- 关键说明：项目默认可在“无 Redis、无 Celery/RQ、无 LangGraph”模式运行

## 2.2 主流程能力（已落地）

已能跑通的主链路（同步版 + 异步入口）：

1. 检索论文候选（OpenAlex / Crossref / arXiv）
2. 自动选文（带相关度与 facet 规则）
3. 下载或复用本地 PDF
4. 解析 PDF（GROBID 优先，失败回退 pypdf / PyMuPDF）
5. chunk 切分 + Evidence Card 构建
6. 生成 Draft
7. 规则 + LLM 审查
8. 自动修订（最多 3 轮，停机阈值）
9. 导出 markdown/docx/pdf/bibtex/evidence_map/review_report/quality_report

对应入口：

- `POST /api/projects/{project_id}/run-auto-workflow`
- `POST /api/projects/{project_id}/run-auto-workflow-async`

## 2.3 前端页面（已实现）

- `/` ProjectList
- `/projects/:projectId` ProjectDetail
- `/projects/:projectId/papers` PaperLibrary
- `/projects/:projectId/evidence` EvidenceBoard
- `/projects/:projectId/drafts` DraftEditor
- `/projects/:projectId/review` ReviewPanel
- `/projects/:projectId/final` FinalDocument
- `/llm-settings` LLMSettings（多 Provider 配置、激活、测速、编辑、删除）

## 2.4 质量门禁（已在代码中执行）

`publication_prepared` 判定条件：

- `critical_issues == 0`
- `unsupported_claims == 0`
- `unresolved_citations == 0`
- `evidence_coverage >= 0.90`
- `citation_validity >= 0.90`
- `logic_score >= 0.80`
- `style_score >= 0.80`

---

## 3. 架构关键点（代码事实）

## 3.1 工作流编排状态

- 已有 LangGraph 版本编排：`backend/app/services/workflow/graph.py`
- 运行时逻辑：`runner.py` 优先尝试 LangGraph，导入失败则回退 inline 流程
- 当前环境验证：`langgraph` 未安装，因此默认走 inline fallback

## 3.2 任务系统状态

- 当前不是 Redis 队列，而是 `task_registry.py`（内存 + `backend/data/tasks.json` 持久化）
- 优点：重启后可读取历史任务
- 限制：多实例一致性、真正分布式消费能力不足

## 3.3 检索与召回状态

- 搜索源：OpenAlex / Crossref / arXiv（Europe PMC 尚未接入）
- `retrieval_service.py` 已是“向量 + 词法”混合策略
- Qdrant 异常时会回退词法检索，流程可继续

## 3.4 PDF 获取与安全边界

- 支持 DOI -> OpenAlex/Unpaywall/Crossref fallback
- 有 SSRF 私网地址拦截、MIME/后缀校验、上传大小限制（默认 50MB）
- TLS 降级默认关闭，需显式开关 `PAPERFORGE_ALLOW_TLS_DOWNGRADE`

## 3.5 存储层状态

- 已抽象 `storage.py`（Local / S3-MinIO）
- 默认本地存储
- S3 路径已支持，但 `minio` Python 包未在 `requirements.txt` 中声明（启用 S3 前需补依赖）

## 3.6 可观测性状态

- 有请求日志中间件（结构化 JSON）
- 有进程内 metrics（`/metrics`、`/api/metrics-detail`、`/dashboard`）
- 已覆盖请求计数、任务统计、外部 API tagged counters、步骤耗时

---

## 4. 与技术方案 v2 的差距（To-Be Gap）

| 目标（v2） | 当前实现 | 差距判断 |
|---|---|---|
| PostgreSQL + Alembic 严格迁移 | 默认 SQLite，且迁移链存在已验证问题 | 高 |
| Redis + Celery/RQ | 任务为进程内 + JSON 持久化 | 高 |
| LangGraph 作为主编排 | 有代码但依赖未装，默认 fallback | 中高 |
| Qdrant 语义召回 | 已接入 + fallback，但工程化能力待加强 | 中 |
| GROBID 主解析链路 | 已接入且优先调用，但降级路径占主导 | 中 |
| 生产级分布式稳定性 | 单进程可用，水平扩展能力不足 | 高 |

---

## 5. 已验证风险与复现结论

## 5.1 Alembic 迁移链错误（已复现）

在临时 SQLite 上执行：

```powershell
$env:DATABASE_URL='sqlite:///./tmp_migration_check.db'
..\.conda\envs\paperforge\python.exe -m alembic upgrade head
```

结果：失败，错误为 `duplicate column name: strategy_mode`。  
结论：`llm_configs` 迁移链存在重复加列，需先修复迁移链再谈“全新环境一键升级”。

## 5.2 异步工作流测试现状

- `test_run_auto_workflow_async_returns_task_and_completes` 被标记 `skip`
- 原因注释：SQLite + Thread 并发限制，建议在 Redis/RQ 任务后端下验证

## 5.3 队列与并发模型

- `run-auto-workflow-async` 当前仍由 `Thread` 启动
- 在高并发、重启恢复、跨进程调度方面不具备生产级保障

---

## 6. 你希望 ChatGPT 研究模式重点解决的问题

请围绕以下问题输出“可执行设计”而非泛泛建议：

1. 如何将当前任务系统平滑升级到 `Redis + RQ/Celery`，并保持现有 API 兼容。
2. 如何修复并重排 Alembic 迁移链，避免历史环境与全新环境冲突。
3. 如何让 LangGraph 版本成为主路径，并保留可控 fallback。
4. 如何定义“证据质量门禁”的回归测试基线（含 golden/bad 样本）。
5. 如何拆分 workflow 模块边界，降低单文件复杂度并提升可测性。
6. 如何规划 2-4 周、按风险优先级落地的技术路线图（含验收标准）。

---

## 7. 建议研究模式输出格式（你可要求它按这个结构回答）

1. **架构总图（现状 -> 目标）**
2. **分阶段改造计划（P0/P1/P2）**
3. **每阶段改动清单**：模块、接口、数据迁移、回滚策略
4. **测试与验收方案**：单测/集成/迁移演练/压测
5. **风险矩阵**：失败模式、监控指标、应急预案
6. **最小可落地 PR 切分建议**：每个 PR 的范围与顺序

---

## 8. 最小附件清单（给 ChatGPT，避免上传过多文件）

如果你不想上传整个仓库，建议至少提供以下文件：

- `PaperForge_Codex_技术方案_v2.md`
- `docs/tech-plan-optimization.md`
- `README.md`
- `docker-compose.yml`
- `backend/requirements.txt`
- `backend/app/main.py`
- `backend/app/database.py`
- `backend/app/api/routes/workflow.py`
- `backend/app/api/routes/papers.py`
- `backend/app/api/routes/llm_config.py`
- `backend/app/services/workflow/runner.py`
- `backend/app/services/workflow/graph.py`
- `backend/app/services/workflow/ingest.py`
- `backend/app/services/search_service.py`
- `backend/app/services/retrieval_service.py`
- `backend/app/services/task_registry.py`
- `backend/app/services/review_service.py`
- `backend/alembic/versions/*.py`（4 个迁移文件）
- `backend/tests/test_workflow_pipeline.py`
- `backend/tests/test_quality_gate.py`
- `frontend/src/router.ts`
- `frontend/src/views/LLMSettings.vue`

---

## 9. 可直接粘贴到 ChatGPT 研究模式的提示词

```text
你现在是我的架构研究顾问。下面是 PaperForge 当前系统的“真实状态”摘要（不是理想方案）：

1) 系统定位
- 证据驱动研究写作系统，目标是 publication-preparation，不是自动投稿。

2) 已实现主链路
- 检索 -> 选文 -> 下载/解析 PDF -> chunk -> evidence -> draft -> review -> revise -> export。
- 关键接口：/run-auto-workflow（同步）和 /run-auto-workflow-async（异步入口）。

3) 当前关键实现现实
- 默认 SQLite。
- 任务系统是进程内 + tasks.json 持久化，不是 Redis 队列。
- 有 LangGraph 编排代码，但当前环境未安装 langgraph，默认 fallback。
- 检索源为 OpenAlex/Crossref/arXiv；召回为 Qdrant + lexical fallback。
- GROBID 已接入，失败时回退 pypdf/PyMuPDF。

4) 已验证问题
- Alembic upgrade head 在全新库失败：duplicate column name strategy_mode（llm_configs 迁移链重复加列）。
- async workflow 测试在 SQLite + Thread 下被 skip（并发限制）。

请你输出：
A. 现状->目标架构图（文字版）
B. 2-4 周分阶段改造路线（P0/P1/P2）
C. 每阶段的代码改造清单（模块、接口、数据迁移、回滚）
D. 风险矩阵与监控指标
E. 按“最小可落地 PR”粒度拆分的执行顺序

要求：务必结合上述现状约束，不要给脱离现有代码的理想化方案。
```

---

## 10. 备注

- 本文档优先记录“现在真实能跑什么、哪里会失败、下一步应先解决什么”。
- 若与旧方案文档存在冲突，以本说明中的“已验证事实”和实际代码为准。

