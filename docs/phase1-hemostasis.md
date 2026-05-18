# Phase 1 止血：安全与数据完整性修复

> 目标：消除致命缺陷，让系统不再静默破坏数据
> 预计时间：3-5 天
> 负责人：AI Agent

---

## 任务总览

| 编号 | 任务 | 优先级 | 预计时间 | 文件 |
|:---:|------|:------:|:--------:|------|
| 1.1 | 修复段落索引漂移 | P0 | 4h | `backend/app/services/review_service.py` |
| 1.2 | 前端添加 DOMPurify | P0 | 1h | `frontend/src/views/DraftEditor.vue`, `FinalDocument.vue`, `LLMSettings.vue` |
| 1.3 | 修复 PDF 导出中文 | P0 | 3h | `backend/app/services/export_service.py` |
| 1.4 | 为外键添加 DB 索引 | P0 | 1h | `backend/app/models/*.py` |
| 1.5 | draft version 唯一约束 | P0 | 1h | `backend/app/models/draft.py`, `backend/app/api/routes/drafts.py` |
| 1.6 | 移除 create_all() | P0 | 0.5h | `backend/app/main.py` |
| 1.7 | API Key 返回脱敏 | P0 | 1h | `backend/app/api/routes/llm_config.py`, `backend/app/schemas/llm_config.py` |
| 1.8 | 统一错误处理 | P1 | 2h | `backend/app/api/routes/workflow.py`, `backend/app/services/review_service.py`, `writing_service.py` |

---

## 1.1 修复段落索引漂移

### 问题
`review_draft_with_metrics()` 和 `revise_draft()` 使用不同的段落分割逻辑：
- **review**：只给 claim blocks（非注释/元数据行）分配 `paragraph-N` 索引
- **revision**：按 `\n\n` 分割，元数据行也被计入段落

→ 同一个 `paragraph-3` 在 review 和 revision 中指向不同的文本块

### 修复方案
**方案 A（推荐）**：统一使用相同的段落分割逻辑
- 提取一个公共的 `_split_paragraphs(content_md)` 函数
- review 和 revision 都调用它
- 确保索引一一对应

**方案 B**：用 evidence 注释锚点替代数字索引
- 给每个段落注入不可见的 HTML 注释作为锚点
- issue 引用锚点而不是段落号
- 更鲁棒，但改动较大

### 验收标准
- 运行 review + revision 后，evidence 注释位置与原文一致
- 添加一个单元测试覆盖此场景

---

## 1.2 前端添加 DOMPurify

### 问题
`v-html` 直接渲染用户/LLM 提供的 Markdown/HTML，无过滤：
- `DraftEditor.vue` 预览模式
- `FinalDocument.vue` 预览模式
- `LLMSettings.vue` 的 SVG logo

→ XSS 攻击向量（如 `<img src=x onerror=alert(1)>`）

### 修复方案
1. `npm install dompurify`
2. 在 `DraftEditor.vue` 和 `FinalDocument.vue` 的 `renderedHtml` computed 中包装：
   ```ts
   import DOMPurify from "dompurify";
   const renderedHtml = computed(() => {
     const raw = marked.parse(...) as string;
     return DOMPurify.sanitize(raw);
   });
   ```
3. `LLMSettings.vue` 的 `v-html="preset.logo_svg"` 同样 sanitize

### 验收标准
- XSS payload（`<script>alert(1)</script>`）被过滤
- 正常 Markdown（表格、代码块、链接）正常渲染

---

## 1.3 修复 PDF 导出中文

### 问题
`export_service.py` 使用 `fpdf`，强制 latin-1 编码：
```python
pdf.multi_cell(0, 7, txt=line.encode("latin-1", errors="replace").decode("latin-1"))
```
→ 中文全部变成 `?`

### 修复方案
**方案 A**：使用 `fpdf2` + 中文字体（如 Noto Sans CJK）
- `pip install fpdf2`
- 下载 `.ttf` 字体文件
- 注册字体后使用

**方案 B**：使用 `reportlab` + 中文字体
- 更成熟，但依赖更重

**方案 C**：使用 WeasyPrint（HTML→PDF）
- 效果最好，但依赖复杂

### 推荐方案 A
1. `pip install fpdf2`
2. 下载 `NotoSansCJKsc-Regular.otf` 放入 `backend/data/fonts/`
3. 修改 `export_pdf()`：
   ```python
   from fpdf import FPDF
   pdf = FPDF()
   pdf.add_font("Noto", "", "path/to/NotoSansCJKsc-Regular.otf", uni=True)
   pdf.set_font("Noto", "", 12)
   ```

### 验收标准
- 导出含中文的 draft，PDF 中文字正常显示

---

## 1.4 为外键添加 DB 索引

### 问题
所有外键和常用查询列无索引 → 全表扫描

### 修复列表
| 模型 | 字段 | 原因 |
|------|------|------|
| `paper.py` | `project_id` | FK，几乎所有查询都带 |
| `paper_chunk.py` | `paper_id` | FK，chunk 查询 |
| `draft.py` | `project_id` | FK，draft 列表 |
| `evidence_card.py` | `project_id`, `paper_id` | FK，evidence 查询 |
| `review_issue.py` | `project_id`, `draft_id` | FK，review 查询 |
| `llm_config.py` | `is_active` | 每次 LLM 调用都查 |
| `paper.py` | `selected` | workflow 中频繁过滤 |
| `paper.py` | `parse_status` | 统计/过滤常用 |

### 修复方式
```python
project_id: Mapped[str] = mapped_column(ForeignKey(...), index=True)
```

### 后续
- 生成 Alembic migration
- 执行 migration

---

## 1.5 Draft Version 唯一约束

### 问题
`_ensure_unique_version()` 是 SELECT-then-INSERT，无 DB 级唯一约束 → Race Condition

### 修复
1. 模型添加：
   ```python
   __table_args__ = (UniqueConstraint("project_id", "version", name="uq_draft_project_version"),)
   ```
2. 路由 `_ensure_unique_version()` 改为依赖 DB 约束：
   - 尝试 INSERT
   - 捕获 IntegrityError → 重新生成 version

### 验收标准
- 并发创建 draft 不重复版本号

---

## 1.6 移除 Base.metadata.create_all()

### 问题
`main.py` 启动时自动建表，绕过 Alembic → schema 漂移风险

### 修复
```python
# 移除或注释掉
# Base.metadata.create_all(bind=engine)
```

### 替代方案
- 开发环境：保留但加 `if os.getenv("ENV") == "dev":`
- 生产环境：强制使用 Alembic

---

## 1.7 API Key 返回脱敏

### 问题
`GET /api/llm/configs` 和 `GET /api/llm/config/{id}` 返回明文 API Key

### 修复
1. `LLMConfigRead` schema 添加 `api_key` 的 field_serializer：
   ```python
   @field_serializer("api_key")
   def mask_api_key(self, value: str | None) -> str | None:
       if value and len(value) > 8:
           return value[:4] + "****" + value[-4:]
       return value
   ```
2. PATCH/POST 时正常保存（不脱敏）

### 验收标准
- GET 返回 `sk-****xxxx`
- PATCH/POST 仍可正常更新完整 key

---

## 1.8 统一错误处理

### 问题
多处 `except Exception: pass` 吞掉错误，静默失败：
- `workflow.py: _safe_get_json`
- `review_service.py: _llm_review`
- `writing_service.py: _llm_write_section`

### 修复原则
1. **绝不裸 catch Exception**
2. **必须记录日志**：`logger.exception(...)` 或 `task.logs.append(...)`
3. **向用户反馈**：通过 task result 或 API 错误返回具体原因
4. **区分可恢复/不可恢复**：
   - 可恢复（网络超时、429）→ 重试
   - 不可恢复（API Key 无效、格式错误）→ 立即报错

### 修复范围
| 文件 | 函数 | 当前行为 | 修复后 |
|------|------|---------|--------|
| `workflow.py` | `_safe_get_json` | 返回 None | 返回 None，但记录警告日志 |
| `review_service.py` | `_llm_review` | 返回 [] | 返回 []，task log 记录失败原因 |
| `writing_service.py` | `_llm_write_section` | fallback 模板 | fallback 前记录 LLM 失败原因 |

---

## 执行检查清单

- [ ] 所有修改通过 `vue-tsc --noEmit`
- [ ] 后端通过 `python -m py_compile`
- [ ] Alembic migration 生成并执行
- [ ] Docker 容器重启后功能正常
- [ ] 手动测试：review → revision → 检查 evidence 位置
- [ ] 手动测试：导出含中文的 PDF
- [ ] 手动测试：XSS payload 被过滤
