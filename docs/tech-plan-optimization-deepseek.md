# PaperForge 深度技术优化方案

> 版本：v1.0
> 日期：2026-05-21
> 基准对照：`PaperForge_Codex_技术方案_v2.md` + `docs/tech-plan-optimization.md`
> 评估范围：全栈架构、数据链路、AI 管线、工程化、可观测性

---

## 1. 执行摘要

当前 PaperForge 已完成 MVP 闭环（检索→解析→证据→写作→审查→导出），并在第一阶段止血中修复了数据完整性、XSS 防护、API Key 脱敏等致命缺陷。**但项目距离"出版准备级"生产系统仍有显著架构鸿沟**。

本方案不是对 `tech-plan-optimization.md` 的简单重复，而是**从架构视角重新审视技术债务**，识别那些"当前能跑，但规模扩大后必出问题"的深层隐患，并给出可落地的演进路径。

---

## 2. 深层架构差距分析

### 2.1 数据库层：SQLite 默认路径是生产陷阱

**现状**：`database.py` 默认回退到 SQLite，且 `check_same_thread=False` + `NullPool` 的组合在并发场景下存在隐性风险。

**深层问题**：
- SQLite 的 WAL 模式未显式启用，高并发写入时易出现 `database is locked`
- `NullPool` 意味着每个请求都新建连接，没有连接复用
-  Alembic 迁移链虽已补齐索引和约束，但 `llm_configs` 表**并非在 init migration 中创建**，而是依赖 `607cc55778da` 的 `batch_alter_table`。在 PostgreSQL 上这要求表已存在，但全新部署时 `llm_configs` 并不存在——**迁移链对新库仍有断裂风险**
- 没有读写分离设计，所有查询都走主库

**与方案差距**：技术方案 v2 要求"PostgreSQL + Alembic 严格迁移"，当前是"SQLite 默认 + PostgreSQL 可选"，心理上已经降级。

---

### 2.2 任务系统：文件级持久化是中间态，非终态

**现状**：`task_registry.py` 从纯内存演进为 JSON 文件持久化，已能 survive 进程重启。

**深层问题**：
- 文件锁仅依赖 `threading.RLock`，**多进程（如 gunicorn workers）并发写会丢数据**
- 每写一次都全量序列化整个字典，任务量增长后 I/O 开销线性上升
- 没有任务优先级、没有延迟队列、没有死信队列
- 自动工作流使用 `threading.Thread` 启动后台任务，**线程异常不会触发外部告警**，且无法跨节点分发

**与方案差距**：技术方案 v2 要求 Redis + Celery/RQ，当前实现是"用文件模拟队列"，无法支撑水平扩展。

---

### 2.3 检索层：Embedding 质量是隐性天花板

**现状**：`sentence-transformers/all-MiniLM-L6-v2`（384维）+ Qdrant 向量召回 + 词频余弦兜底，已实现 hybrid retrieval。

**深层问题**：
- MiniLM-L6 对学术文本的语义理解有限，尤其是长文档、专业术语、跨语言场景
- **没有重排序（rerank）层**：Qdrant 的向量相似度是粗排，顶部结果未必最相关
- Chunk 切分策略简单（固定长度），未考虑学术文本的结构特征（章节边界、段落完整性）
- 没有查询扩展（query expansion）或假设性文档嵌入（HyDE）
- `vector_id` 在 chunk 模型中存在但**没有与 Qdrant 的 id 建立强一致性约束**（如事务级同步）

**与方案差距**：技术方案 v2 暗示了"高质量向量召回 + 重排"，当前实现停留在"有向量检索即可"的 MVP 级别。

---

### 2.4 LLM 管线：缺少工程化控制面

**现状**：`llm_service.py` 的 Strategy 模式已支持多 provider、retry、capability matrix，代码质量较高。

**深层问题**：
- **没有 prompt 版本管理**：prompt 硬编码在 `review_service.py`、`writing_service.py` 等各处，迭代时无法 A/B 测试或回滚
- **没有输出缓存**：相同/相似 query 每次都调 API，成本高且延迟大
- **没有 fallback chain**：当主 provider 失败时，没有自动切到备用 provider 的机制（只有单 provider retry）
- **没有流式输出支持**：写作任务可能产生长文本，用户只能等待全部生成
- `_get_active_config()` 每次调用都新建 DB session，高频 LLM 调用时造成不必要的连接开销
- `chat_completion_text` 在出错时返回空字符串，**调用方无法区分"模型返回空"和"调用失败"**

---

### 2.5 工作流：单文件 1962 行是架构异味

**现状**：`workflow.py` 虽已抽离 `search_select.py`，但仍是超大文件。

**深层问题**：
- 路由层直接编排业务逻辑，违反" thin controller, fat service "原则
- 状态转换没有显式状态机：工作流的步骤跳转通过 `Thread` + `set_progress` 隐式完成，**没有幂等性保证**
- 错误处理呈"补丁状"：每个步骤各自 try-catch，但全局回滚/补偿机制缺失
- 自动工作流一旦启动无法优雅中断，没有 cancellation token 机制
- 大量辅助函数（`_paper_to_dict`、`_evidence_to_dict` 等）应使用 Pydantic schema 序列化，而非手写

---

### 2.6 安全：边界已建立，深度不足

**现状**：已有 SSRF 防护（私网拦截、域名白名单）、TLS 降级可控、XSS 过滤（DOMPurify）、API Key 脱敏。

**深层问题**：
- **没有 rate limiting**：`workflow.py` 的搜索、LLM 调用接口可被恶意刷取
- **没有内容安全扫描**：上传的 PDF 可能包含恶意 payload（虽然 PDF 解析后转为文本，但解析库本身可能有漏洞）
- **没有审计日志**：谁、何时、调用了哪个工作流、消耗了多少 token，没有持久化记录
- CORS 配置允许 `*` 级别的 origins，在生产环境过于宽松
- 下载重试时 `verify_tls=False` 的开关存在，但配置名 `PAPERFORGE_ALLOW_TLS_DOWNGRADE` 容易被误启用

---

### 2.7 可观测性：近乎空白

**现状**：有基础的 `logging.getLogger(__name__)` 调用，task_registry 有结构化日志接口。

**深层问题**：
- 没有 Metrics（Prometheus 格式）：请求延迟、QPS、LLM token 消耗、外部 API 成功率、队列深度
- 没有 Distributed Tracing：一个工作流跨越多步骤、多外部调用，无法追踪端到端耗时
- 没有 Health Check 深度探测：`/health` 只返回 `ok`，不检查 DB、Qdrant、LLM provider 可用性
- 没有 Alerting 规则：任务失败率突增、磁盘满、LLM 响应超时没有告警通道

---

### 2.8 前端：状态管理与错误处理薄弱

**现状**：Vue 3 + Composition API + TypeScript，代码风格整洁。

**深层问题**：
- **没有全局状态管理**：所有页面各自 `apiRequest`，数据在组件间通过 props/events 或重新加载传递，效率低且容易不一致
- **没有请求层缓存/去重**：频繁切换 tab 会导致重复请求
- **没有错误边界**：任意 API 错误只显示在页面底部 `error` 文本，没有统一错误处理中间件
- `api.ts` 的 `fetch` 没有 timeout 控制，网络异常时会挂起
- 前端没有接入 source map 和错误上报（Sentry 类服务）

---

### 2.9 DevOps 与工程化

**现状**：Docker Compose 热更新、pytest 单测、Alembic 迁移。

**深层问题**：
- **测试覆盖率未知**：没有 coverage 报告
- **没有集成测试**：所有测试都是基于 `TestClient` 的单元/流程测试，没有真实 DB、真实 Qdrant、真实 LLM 的集成验证
- **没有 Lint/Format 门禁**：`backend/` 没有看到 black/ruff/mypy 配置，`frontend/` 没有 eslint/prettier 配置
- **没有依赖扫描**：`requirements.txt` 没有锁定版本哈希，存在供应链攻击风险
- **CI/CD 空白**：没有看到 GitHub Actions / GitLab CI 配置
- **没有性能基准**：工作流各步骤的 p50/p95/p99 耗时未知

---

## 3. 深度优化路线图

### Phase A：基础设施硬化（2 周）

目标：消除"规模扩大后必出问题"的架构隐患。

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| A-1 | 强制 PostgreSQL 路径，移除 SQLite 生产默认 | 高 | `database.py` 默认抛错而非回退 SQLite；开发环境用 `DATABASE_URL` 显式指定 SQLite；WAL 模式启用 |
| A-2 | 修复 Alembic 基线断裂 | 高 | 重写 init migration 包含 `llm_configs` 完整 schema；或新增 pre-init migration 专门建 llm_configs 表 |
| A-3 | 任务系统升级到 Redis + RQ | 高 | 新增 `rq` worker 容器；`task_registry` 改写为 Redis backend；保留现有接口做 facade |
| A-4 | 连接池与 DB 性能 | 中 | PostgreSQL 启用 `QueuePool`；SQLAlchemy 添加 `pool_pre_ping=True`；慢查询日志 |

---

### Phase B：AI 管线提质（3 周）

目标：从"有 AI 功能"到"AI 效果可度量、可迭代"。

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| B-1 | Embedding 模型升级 | 中 | 评估 `BAAI/bge-large-en-v1.5` 或 `intfloat/multilingual-e5-large`；支持多语言学术文本；增加 rerank 模型（cross-encoder） |
| B-2 | Prompt 工程化管理 | 中 | 新建 `backend/prompts/` 目录，按版本管理；每个 prompt 有 ID、版本、A/B 标签；支持运行时热加载 |
| B-3 | LLM 输出缓存层 | 低 | 对确定性 prompt（如格式转换、结构化提取）加入 Redis/SQLite 缓存；缓存键 = hash(prompt+model+params) |
| B-4 | Provider 故障自动转移 | 中 | LLMConfig 支持`is_active` 多配置；主 provider 失败时按优先级 fallback；记录 fallback 事件 |
| B-5 | Chunk 策略学术化 | 中 | 接入 GROBID 的 section 信息做语义切分；保留段落完整性；header 信息注入 chunk metadata |

---

### Phase C：工作流重构（3 周）

目标：从"单文件脚本"到"可编排、可观测、可恢复的状态机"。

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| C-1 | 引入显式状态机 | 高 | 新建 `WorkflowStateMachine` 类；状态 = `idle → searching → selecting → downloading → parsing → chunking → retrieving → evidencing → outlining → writing → reviewing → revising → exporting → completed/failed`；每次状态转换持久化到 DB |
| C-2 | 工作流幂等性 | 高 | 每个步骤输入输出 checksum 化；相同输入跳过重复执行；支持"从某步骤恢复" |
| C-3 | 服务层彻底拆分 | 中 | `workflow.py` 只保留路由；业务逻辑按步骤拆分到 `services/workflow/steps/*.py`；每个 step 有统一接口 `execute(ctx: WorkflowContext) -> StepResult` |
| C-4 | 取消 Thread 模型 | 高 | 所有异步工作流转由 RQ worker 执行；前端通过 SSE/WebSocket 推送进度；支持任务取消（job.cancel） |
| C-5 | 全局补偿与回滚 | 中 | 某步骤失败时，已创建的 draft/evidence 标记为 `orphaned` 或自动清理；避免僵尸数据 |

---

### Phase D：可观测性与运营（2 周）

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| D-1 | 结构化日志 + Trace | 低 | 统一 JSON 格式日志；每个请求/workflow 生成 trace_id；传播到所有外部调用 |
| D-2 | Metrics 暴露 | 低 | `/metrics` 端点暴露 Prometheus 指标；关键指标：http_duration、llm_latency、token_usage、task_queue_depth、external_api_success_rate |
| D-3 | 深度健康检查 | 低 | `/health` 检查 DB、Qdrant、Redis、LLM provider（轻量 probe）；`/ready` 检查 worker 是否可接受新任务 |
| D-4 | 审计日志表 | 中 | 新建 `audit_logs` 表记录：用户操作、API 调用、资源消耗（token、存储）；保留 90 天 |

---

### Phase E：前端工程化（2 周）

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| E-1 | 引入 Pinia 状态管理 | 中 | 全局 `projectStore`、`taskStore`；缓存项目列表、论文列表；减少重复请求 |
| E-2 | API 层中间件 | 低 | 统一 timeout、retry、错误 toast、loading 状态；请求去重（相同 pending 请求复用 Promise） |
| E-3 | 代码规范 | 低 | 配置 eslint + prettier + vue-tsc strict；husky pre-commit 钩子 |
| E-4 | 错误边界 + 骨架屏 | 低 | 全局 `ErrorBoundary` 组件；长加载页面使用 skeleton screen |

---

### Phase F：安全与治理（1-2 周）

| 编号 | 任务 | 风险等级 | 关键改动 |
|------|------|---------|---------|
| F-1 | Rate Limiting | 中 | 基于 Redis 的滑动窗口限流；按 IP + 用户维度；搜索/LLM 接口更严格 |
| F-2 | 输入验证强化 | 低 | Pydantic v2 strict mode；上传文件魔数校验（不仅是扩展名）；最大文件大小强制 |
| F-3 | 依赖锁定 | 低 | `requirements.txt` → `requirements.lock`（含哈希）；`package-lock.json` 已存在，确保 CI 使用 `--frozen-lockfile` |
| F-4 | CORS 生产硬化 | 低 | 生产环境只允许明确配置的 origin；禁用 credentials 的通配符 |

---

## 4. 关键技术决策建议

### 4.1 是否现在引入 LangGraph？

**建议：暂缓**

当前工作流是线性管道（pipeline），没有复杂分支、循环、人工干预节点。LangGraph 的学习成本和运行时开销在此阶段不划算。**先做完 Phase C 的状态机重构**，当确实出现"条件分支 + 人工审核 + 回退"需求时，再评估 LangGraph 或自研轻量 DAG 引擎。

### 4.2 向量数据库是否要从 Qdrant 升级到 Milvus/Pinecone？

**建议：保持 Qdrant**

Qdrant 在学术场景下完全够用，自托管成本低，且已集成。当前瓶颈在 embedding 质量和 rerank，不在向量数据库本身。

### 4.3 任务队列选 RQ 还是 Celery？

**建议：RQ**

- PaperForge 的任务模型简单，没有复杂的工作流编排需求
- RQ 基于 Redis，学习曲线低，与 FastAPI 集成轻量
- Celery 更重，需要额外维护 broker/result backend/beat
- 如果未来需要定时任务，可再引入 `rq-scheduler`

### 4.4 缓存层选型

**建议：Redis 多用途**

任务队列、LLM 输出缓存、Rate Limiting、Session 都共用同一个 Redis 实例（不同 DB index 或 key prefix）。避免引入额外组件增加运维负担。

---

## 5. 立即执行的 10 个动作（本周）

以下动作风险低、收益高，可在不改变架构的前提下立即落地：

1. **修复 Alembic 新库断裂**：在 `20260515_0001_init_schema.py` 中补建 `llm_configs` 表（完整字段），使全新 `alembic upgrade head` 通过。
2. **为 `/health` 增加深度探测**：检查 DB 连接和 Qdrant 可用性，返回 `{db: ok, qdrant: ok}`。
3. **统一 JSON 日志格式**：在 `main.py` 配置 `logging.Formatter` 输出 JSON，包含 `trace_id`、`timestamp`、`level`、`message`、`module`。
4. **为 LLM 调用增加输出缓存**：Redis/SQLite 缓存 `hash(prompt)` → `response`，TTL 24h，仅对 temperature=0 的调用生效。
5. **前端 API 层加 timeout**：`api.ts` 的 `fetch` 包装 `AbortController`，默认 30s 超时。
6. **workflow.py 辅助函数迁移**：将 `_paper_to_dict`、`_evidence_to_dict` 等替换为 Pydantic `model_validate`。
7. **增加 `task_registry` 多进程安全**：至少将 `_save()` 改为原子文件写（已有 `os.replace`），并加文件级锁（`filelock` 库）。
8. **为 chunk 切分保留 section 信息**：`ingestion_service.py` 在调用 `chunk_text` 时传入 GROBID 解析出的 section headers，作为 chunk metadata。
9. **限制 CORS origins 环境化**：将 `allow_origins` 移入 `.env`，生产环境只放前端域名。
10. **补充 `pytest --cov` 基线**：运行一次覆盖率测试，记录当前百分比，作为后续改进基准。

---

## 6. 验收标准汇总

| 阶段 | 核心验收标准 |
|------|------------|
| Phase A | `docker compose --profile infra up` 后，系统完全跑在 PostgreSQL + Redis + Qdrant 上；`alembic upgrade head` 从零通过；并发 10 个自动工作流不丢状态 |
| Phase B | 引入 rerank 后，同一 query 的 top-5 命中率较纯向量提升 >15%（人工评测 20 条）；prompt 修改后无需重启服务即可生效 |
| Phase C | `workflow.py` < 400 行；每个步骤可独立单元测试；工作流中断后可从最近成功步骤恢复；支持 SSE 进度推送 |
| Phase D | Grafana 可查看任务耗时分布、LLM 成功率、外部 API 延迟；`/health` 异常时 k8s/docker 自动重启 |
| Phase E | 前端切换 tab 不再重复请求相同数据；API 错误有统一 toast 提示；代码提交前自动格式化 |
| Phase F | 压力测试下 1000 req/min 不被击穿；上传 100MB+ PDF 被明确拒绝；生产环境 CORS 不暴露通配符 |

---

## 7. 与技术方案 v2 的逐项对齐

| 方案目标 | 当前 | 做完本方案后 | 剩余差距 |
|---------|------|------------|---------|
| PostgreSQL + Alembic | SQLite 默认，PG 可选 | PG 强制，SQLite 仅 dev | 无 |
| Redis + 任务队列 | 文件持久化 | Redis + RQ | 无 |
| Qdrant 向量检索 | 已集成，但无 rerank | 向量 + rerank | 无 |
| GROBID + TEI | 四级降级，GROBID 优先 | GROBID section 信息注入 chunk | 无 |
| Agent 编排（LangGraph） | 单文件串行 | 显式状态机 | LangGraph 暂缓，需业务驱动 |
| 生产级可观测性 | 基础日志 | Metrics + Trace + Alert | 需接入外部监控系统 |
| 对象存储 | 本地磁盘 | 本地磁盘 | MinIO 可后续接入，非阻塞 |
| 出版质量门禁 | 简单规则 | 可审计指标快照 | 需积累数据和阈值调优 |

---

## 8. 风险与缓解

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|---------|
| Phase A 数据库切换导致数据丢失 | 中 | 高 | 切换前完整备份；编写 SQLite→PG 迁移脚本；先在 staging 验证 |
| RQ 引入增加本地开发复杂度 | 高 | 低 | `docker compose` 默认启动 Redis；保留内存模式做纯本地 fallback |
| Embedding 模型升级导致向量不兼容 | 中 | 中 | 新模型用新 collection name；支持双模型并行；逐步切换 |
| 工作流重构引入回归 | 高 | 高 | 重构前补充端到端测试（当前 `test_workflow_pipeline.py` 已覆盖）；每拆一个步骤就验证 |
| Prompt 外部化后版本混乱 | 中 | 中 | 强制定义 prompt schema（ID + version + model_compat）；CI 校验 JSON schema |

---

> 本方案由深度代码审查生成，覆盖架构、数据、AI、工程化、安全五个维度。建议按 Phase A → C → B → D → E → F 的顺序推进，先做硬化再做功能，避免在脆弱地基上继续加盖。
