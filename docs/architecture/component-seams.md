# Component seams（组件替换点）

PaperForge 全自动写作管线由 LangGraph 节点编排。为了二次开发与评测可以在不改动节点逻辑的前提下替换某一环节的实现，管线提供 **5 个文档化的轻替换点（seams）**——每个缝对应一个既有服务函数，节点调用处只做一层 `_seam(state, key, default)` 间接，不引入新的抽象层。

## 机制

- 替换实现随 **`initial_state` 的 `component_overrides` 字典**注入（键 = 下表缝名）。状态携带而非构建期闭包注入，因为节点是模块级函数、编译图单例可复用——状态方案并发安全、无全局残留。
- 二次开发入口：`app.services.workflow.runner._execute_auto_workflow(project_id, payload, db, task_id, component_overrides=...)`。API 路由与前端暂不暴露该参数。
- 不传（`None`）时，`_seam` 返回默认服务函数，管线行为与直接调用完全一致。
- 测试：`backend/tests/test_component_overrides.py`（缝语义 + 三个代表节点的接线验证 + 源码契约锁）。

## 五个替换点

| 缝键 | 默认实现 | 调用节点 | 签名（入参 → 返回） |
|---|---|---|---|
| `recall_chunks` | `retrieval_service.recall_chunks` | `build_evidence` / `evidence_gap` | `(query: str, project_id: str, top_k: int) → list[dict]`，命中按优排序、dict 含 `id`/`paper_id` |
| `plan_sections` | `writing_service.plan_article_sections` | `thesis_thread` | `(article_type, project_title, research_question, evidence_cards) → list[str]` 章节标题 |
| `build_draft` | `writing_service.build_draft_markdown` | `generate_draft` | `(project_title, research_question, article_type, citation_style, evidence_cards, thesis_statement, sections, figure_plans, conflict_groups, papers_off_topic, low_evidence_sections) → (content_md, sections)` |
| `review` | `review_service.debate_review_with_metrics` | `initial_review` / `review`（修订轮共用） | `(content_md, evidence_cards, article_type, *, task_id) → (issues: list[dict], metrics: dict)` |
| `generate_image` | `chart_service.generate_charts_from_evidence` | `generate_images` | `(cards, project_id, project_title, output_dir) → list[dict]` 图对象（`path`/`alt`/`section`…） |

### 缝的范围说明

- `recall_chunks` 只替换**向量召回**子环节（词法打分、0.08 门控、阈值过滤与 rescue 合并都在缝外，由节点维护）。默认实现 Qdrant/向量不可用时会**抛异常**，节点 catch 后走纯词法降级——替换实现应保持同样的"失败可被捕获"语义。
- `generate_image` 首批只覆盖**数据驱动图表**子环节；论文抽取图、social proof SVG 卡、装饰插图不在缝内。
- `review` 的默认实现含规则层（模式化检查）+ 多智能体辩论；替换实现直接返回 `(issues, metrics)` 即可，`metrics` 至少含 `overall_score`（修订循环的停滞/回滚判定依赖它）。

## 最小替换示例

给项目 A 提供一套纯规则大纲（不调用 LLM、不依赖网络），其余环节保持默认：

```python
from types import SimpleNamespace

from app.services.workflow.runner import _execute_auto_workflow


def rule_based_outline(article_type, project_title, research_question, evidence_cards):
    # 返回章标题列表；可自由用 evidence_cards 做定向编排
    return ["问题界定", "关键证据", "政策建议", "结论"]


payload = SimpleNamespace(
    query="…", max_results=5, auto_select_limit=5, keep_manual_selection=True,
    chunk_size=300, max_cards=30, auto_export=False,
)
result = _execute_auto_workflow(
    project_id="…", payload=payload, db=session, task_id="…",
    component_overrides={"plan_sections": rule_based_outline},
)
```

把缝键换成上表其他名字即可替换对应环节；同一字典可传多个缝。

## 降级约定（替换实现应遵守）

- 无 LLM 配置 / 离线环境下，默认实现有模板与词法回退，管线可全流程跑通；替换实现应当**自行处理**其所依赖外部能力不可用的情况（内部失败捕获或确定性回退），不应向节点抛未被预期的异常类型。
- 缝的输入输出契约以上表为准；节点不感知具体实现。
