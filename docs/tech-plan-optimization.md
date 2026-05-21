# PaperForge 项目优化建议（对齐技术方案 v2）

更新时间：2026-05-20  
对照基准：`PaperForge_Codex_技术方案_v2.md`  
评估范围：`backend/app`、`frontend/src`、`docker-compose.yml`、`backend/alembic`、`backend/tests`

## 1. 现状总评

你当前项目已经把 MVP 主链路跑通了（检索→下载/解析→证据卡→草稿→审查→修订→导出），并且完成了一批关键止血项（XSS 过滤、Draft 唯一约束、索引、API Key 脱敏等）。

下一阶段的主要问题不是“功能缺失”，而是“架构与稳定性欠债”：

1. 工作流实现过于集中：`backend/app/api/routes/workflow.py` 约 2054 行，维护成本和回归风险高。
2. 基础设施仍是轻量替代实现：检索召回、任务系统、PDF 解析链路尚未完全对齐技术方案 v2 的目标组件（Qdrant / Redis+Queue / GROBID）。
3. 数据库迁移链路有隐患：Alembic 对 `llm_configs` 的基线覆盖不完整，且启动时仍存在 dev `create_all` 兜底。

---

## 2. 与技术方案 v2 的关键差距

| 方案目标 | 当前实现 | 差距级别 | 优先级 |
|---|---|---|---|
| PostgreSQL + Alembic 严格迁移 | 默认 SQLite；dev 启动 `create_all`；`llm_configs` 初始迁移链不完整 | 高 | P0 |
| Redis + Celery/RQ 任务队列 | 进程内 `task_registry` + `Thread` 异步 | 高 | P0 |
| Qdrant 向量检索 | `retrieval_service.py` 词频余弦检索（36 行轻量实现） | 高 | P1 |
| GROBID + TEI 解析 | 当前是 `pypdf/PyMuPDF` + `save_tei_placeholder` | 中高 | P1 |
| Agent 编排（LangGraph） | 大量流程逻辑写在单路由文件 | 中高 | P1/P2 |
| 生产级可观测性 | 缺少统一结构化日志、任务/步骤指标、告警 | 中 | P1 |

---

## 3. P0 优化（1-2 周，先做）

### P0-1：修复数据库迁移基线（最高优先）

**现状证据**
1. `backend/alembic/versions/20260515_0001_init_schema.py` 未创建 `llm_configs` 表。  
2. 后续迁移却直接对 `llm_configs` 做 `alter/index`。  
3. `backend/app/main.py` 在 dev 模式仍执行 `Base.metadata.create_all(...)`。

**优化方案**
1. 修正 Alembic 元数据收集：`backend/alembic/env.py` 明确纳入 `LLMConfig` 模型。
2. 追加补丁迁移：保证“全新空库执行 `alembic upgrade head`”可完整建表。
3. 把 `create_all` 只作为本地临时开关，不作为默认行为。
4. CI 增加“空库迁移验收”步骤。

**验收标准**
1. 全新数据库 `alembic upgrade head` 成功。  
2. 禁用 `create_all` 后，系统仍可正常启动并通过现有测试。  
3. 迁移链在 SQLite/PostgreSQL 下都可回放。

### P0-2：把自动工作流从单路由拆分为编排服务

**现状证据**  
`workflow.py` 约 2054 行，承担检索、下载、解析、选文、构证据、写作、审查、修订、导出全部职责。

**优化方案**
1. 新建 `app/services/workflow/`（按步骤拆模块：search、selection、ingest、evidence、writing、review、export）。
2. 路由层只做参数校验与响应，流程状态由 `WorkflowContext` 对象承载。
3. 将错误码与用户可读错误统一成标准结构体（已有 detail dict 机制可复用）。

**验收标准**
1. `workflow.py` 控制在 500 行以内。  
2. 各步骤可独立单测（mock 外部依赖）。  
3. 自动工作流回归测试全通过。

### P0-3：任务系统持久化（替换进程内内存注册器）

**现状证据**  
`task_registry.py` 是进程内 dict + lock；服务重启/多副本后任务状态不可追踪。

**优化方案**
1. 按技术方案切到 `Redis + RQ/Celery`。  
2. 任务状态入 Redis（或 DB），`/api/tasks/{task_id}` 读取持久状态。  
3. 异步工作流从 `Thread` 改为队列 worker 消费。

**验收标准**
1. 后端重启后仍可查询历史任务状态。  
2. 并发启动 20+ 工作流任务时无状态丢失。  
3. 任务失败原因可追踪到步骤级。

### P0-4：补齐输入/下载安全边界

**现状风险**
1. 上传接口一次性 `await file.read()`，缺少文件大小上限。  
2. 下载流程允许 TLS 降级重试（`verify_tls=False`），且缺乏域名白名单策略。  
3. 缺少针对异常 URL 的 SSRF 防护策略。

**优化方案**
1. 加 `MAX_UPLOAD_SIZE_MB`、MIME/扩展名双校验、超限快速拒绝。  
2. 对外部下载加入 allowlist（可信学术源）+ 私网地址拦截。  
3. TLS 降级开关改为显式配置，默认关闭。

**验收标准**
1. 超限上传被拒绝且有明确错误码。  
2. 私网地址/非法 URL 下载请求被阻断。  
3. 下载失败日志可明确到 URL 与失败类型。

### P0-5：修复明显可用性缺陷并统一日志

**现状证据**
`backend/app/services/writing_service.py` 使用 `logger.warning/exception`，但文件未定义 `logger`。

**优化方案**
1. 立即修复 logger 定义并补单测。  
2. 将外部调用失败日志统一为结构化字段（task_id、paper_id、provider、step、error_code）。  
3. 禁止静默吞错，保留可恢复重试策略。

**验收标准**
1. 写作 fallback 场景不再触发 `NameError`。  
2. 日志可按 task_id 全链路检索。  
3. 关键外部依赖失败均有可读错误。

---

## 4. P1 优化（2-4 周，能力对齐）

### P1-1：检索与召回升级到 Qdrant（替代纯词频）

**目标**  
实现“向量召回 + 关键词兜底 + 重排”，替代当前纯 lexical 方案。

**落地步骤**
1. 增加 embedding provider 封装。  
2. chunk 入库 Qdrant，维护 `vector_id`。  
3. `retrieve-chunks` 改为 hybrid：向量召回 + 关键词召回 + rerank。

**验收指标**
1. 对同一 query 的证据命中率较当前提升（可先用人工评测集）。  
2. 接口返回稳定包含 score、paper_id、page。

### P1-2：接入 GROBID 解析主链路

**目标**  
从“占位 TEI”升级到“真实 TEI + 结构化章节/参考文献解析”。

**落地步骤**
1. 新增 `grobid_client` 与失败降级策略（保留当前 `pypdf/PyMuPDF` 兜底）。  
2. 将 section、citation、reference 映射到 chunk metadata。  
3. 提高 Evidence Card 的页码与引用可追溯性。

### P1-3：LLM 能力层继续分层

**现状**  
`llm_service` 已做 Strategy 模式，但仍以 OpenAI-compatible 为主，非兼容 provider 支持有限。

**优化方案**
1. Provider 适配层与业务 prompt 层彻底分离。  
2. 为每个 provider 建立 capability matrix（JSON、reasoning、tool-call、max context）。  
3. 失败重试按 provider 级策略和错误码策略执行。

### P1-4：质量门禁工程化

**目标**  
将 `publication_prepared` 从“运行时判断”升级为“可审计质量记录”。

**落地步骤**
1. 每轮审查保存指标快照（coverage、citation_validity、logic/style score）。  
2. 引入回归基准文档，验证修改不会降低门禁稳定性。  
3. 导出包附带质量门禁 JSON 报告。

---

## 5. P2 优化（4-8 周，架构升级）

### P2-1：引入 LangGraph 编排
把当前“单函数串行流程”迁移到“节点化有状态图”，支持失败重试、分支回退、人工中断恢复。

### P2-2：对象存储与归档策略
将本地 `backend/data/storage` 与 `exports` 升级为 MinIO/对象存储，补齐生命周期管理和审计元数据。

### P2-3：可观测性与运营指标
增加任务耗时、外部 API 成功率、解析成功率、证据卡产出率、publication gate 通过率仪表盘。

---

## 6. 建议执行顺序（建议）

1. 先做 **P0-1/P0-3**：把“数据与任务可靠性”打牢。  
2. 再做 **P0-2/P0-5**：降低主流程维护成本并提升可调试性。  
3. 之后推进 **P1-1/P1-2**：这是从 MVP 到“出版准备级”质量提升的关键杠杆。  
4. 最后落地 **P2**：把系统从“可用”升级为“可扩展可运营”。

---

## 7. 本周可立即执行的 7 个小动作

1. 修复 `writing_service.py` 的 `logger` 未定义问题。  
2. 为上传接口加入文件大小上限配置。  
3. 在下载流程加入私网地址拦截。  
4. 在 Alembic 环境中纳入 `LLMConfig` 并补迁移。  
5. CI 增加 `alembic upgrade head` 空库校验。  
6. 抽出 `_execute_auto_workflow` 的第一层子模块（search + select）。  
7. 为 `/api/tasks/{task_id}` 增加任务过期/清理策略说明与实现。

