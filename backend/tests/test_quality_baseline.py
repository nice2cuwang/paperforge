"""端到端质量基线（B 方案，2026-09-02）：固定主题包 × hermetic 全管线。

CI 无 LLM/向量/网络也能全绿（模板/词法降级路径），因此本基线度量的是
“确定性回归护栏”而非模型写作水平：

- 引用可追溯：带 ``<!-- evidence: id -->`` 注释的草稿版本里，每个 id 都
  能解析回该项目的证据卡（可审计链完整）；
- 终稿卫生：面向读者的终稿无 REPLACE_ME、无残留证据注释、正文非空；
- 修订收敛：``revision_rounds_executed <= MAX_REVISION_ROUNDS(3)`` 且正常退出；
- 契约键：result 顶层键齐全（含 evidence_gap 新增键），quality_gate 存在。

阈值按“不回归”校准（宽松），随质量演进收紧；接入真实 LLM 本地跑时才有
模型级区分度，CI 只保证确定性护栏。零外部调用由 hermetic_env 保证。
"""

import re

import pytest

import hermetic_env
from app.models import EvidenceCard
from sqlalchemy import select


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch):
    """本模块全部用例离线运行（与 test_workflow_pipeline 共用实现）。"""
    hermetic_env.install_hermetic(monkeypatch)


def _pdf_bytes(text: str) -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nstream\n"
        + text.encode("utf-8")
        + b"\nendstream\n"
    )


def _create_project(client, project: dict) -> str:
    response = client.post("/api/projects", json=project)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _add_local_paper(client, project_id: str, title: str, body: str) -> None:
    paper_res = client.post(
        f"/api/projects/{project_id}/papers",
        json={
            "title": title,
            "authors": ["Baseline Author"],
            "source": "upload",
            "selected": True,
        },
    )
    assert paper_res.status_code == 201
    paper_id = paper_res.json()["id"]
    upload_res = client.post(
        f"/api/projects/{project_id}/papers/upload",
        data={"paper_id": paper_id},
        files={"file": ("baseline.pdf", _pdf_bytes(body), "application/pdf")},
    )
    assert upload_res.status_code == 200, upload_res.text


def _run_workflow(client, project_id: str, query: str) -> dict:
    auto_res = client.post(
        f"/api/projects/{project_id}/run-auto-workflow",
        json={
            "query": query,
            "max_results": 5,
            "auto_select_limit": 5,
            "chunk_size": 300,
            "max_cards": 30,
            "auto_export": False,
        },
    )
    assert auto_res.status_code == 200, auto_res.text
    return auto_res.json()


TOPICS = [
    {
        "id": "zh_rag_policy",
        "project": {
            "title": "检索增强生成在问答系统中的应用评测",
            "research_question": "检索增强生成是否能提升问答系统的准确率与事实一致性？",
            "article_type": "policy_report",
            "language": "zh",
            "citation_style": "GB/T 7714",
        },
        "paper_title": "检索增强生成问答评测论文",
        "body": "\n".join(
            [
                "检索增强生成通过引入外部知识库显著提升了问答系统的准确率与事实一致性。",
                "多项评测显示检索增强生成相比朴素大模型在事实一致性上平均提升约两成。",
                "检索质量不佳时增强生成可能放大噪声并降低答案可信度，需要重排序模块缓解。",
                "混合检索策略结合向量召回与关键词召回被证明能覆盖更多长尾问题。",
                "评估框架的标准化与多模态检索融合是当前研究的主要空白。",
                "行业报告建议在部署前对检索语料做质量控制并建立人工抽检机制。",
            ]
        ),
    },
    {
        "id": "en_ai_productivity",
        "project": {
            "title": "AI Productivity Survey",
            "research_question": "How does AI affect knowledge worker productivity?",
            "article_type": "policy_report",
            "language": "en",
            "citation_style": "APA",
        },
        "paper_title": "AI productivity survey evidence",
        "body": "\n".join(
            [
                "Empirical survey evidence shows generative AI tools raise knowledge worker output by about twenty percent on average.",
                "Randomized controlled trials report faster task completion for routine writing and summarization tasks.",
                "Experienced workers gain less than novices in quality terms, suggesting skill ceilings in AI-assisted work.",
                "Organizational factors such as training and prompt review processes moderate the measured productivity gains.",
                "Open questions include measurement bias in self-reported productivity and long-run skill atrophy risks.",
            ]
        ),
    },
    {
        "id": "zh_llm_medical_review",
        "project": {
            "title": "大语言模型医疗问诊的安全边界综述",
            "research_question": "大语言模型在医疗问诊场景中的安全边界在哪里？",
            "article_type": "literature_review",
            "language": "zh",
            "citation_style": "GB/T 7714",
        },
        "paper_title": "医疗问诊大模型安全综述",
        "body": "\n".join(
            [
                "大语言模型在医疗问诊中可能输出看起来专业但缺乏依据的断言，构成事实性幻觉风险。",
                "系统综述指出设置明确的免责边界与转诊规则可以显著降低误导患者的概率。",
                "随机对照试验显示医生参与复核的混合流程在安全性与效率间取得较好平衡。",
                "高质量医学语料的检索增强生成能减少幻觉但无法完全消除领域外风险。",
                "评测基准应包含罕见病与禁忌证场景以检验模型的拒绝能力与过度自信倾向。",
            ]
        ),
    },
]


@pytest.mark.parametrize("topic", TOPICS, ids=[t["id"] for t in TOPICS])
def test_end_to_end_quality_baseline(client, test_session_factory, topic):
    project_id = _create_project(client, topic["project"])
    _add_local_paper(client, project_id, topic["paper_title"], topic["body"])
    payload = _run_workflow(client, project_id, topic["project"]["research_question"])

    # ── 运行契约：正常走完 + 修订收敛 ──
    assert payload["selected_count"] >= 1
    assert payload["reused_local_pdf_count"] >= 1
    assert payload["parsed_count"] >= 1
    assert payload["evidence_count"] >= 1 or payload["metadata_fallback_evidence_count"] >= 1
    assert payload["draft_id"]
    assert payload["revised_draft_id"]
    assert payload["revision_rounds_executed"] <= 3
    assert "publication_prepared" in payload
    assert "quality_gate" in payload
    assert isinstance(payload["gap_added_count"], int)
    assert isinstance(payload["low_evidence_sections"], list)

    # ── 终稿卫生：导出后的读者终稿 ──
    drafts_res = client.get(f"/api/projects/{project_id}/drafts")
    assert drafts_res.status_code == 200
    drafts = drafts_res.json()
    revised = next(item for item in drafts if item["id"] == payload["revised_draft_id"])
    final_md = revised["content_md"]
    assert len(final_md.strip()) > 200
    assert "REPLACE_ME" not in final_md
    assert "<!-- evidence:" not in final_md

    # ── 引用可追溯：带证据注释的历史版本里每个 id 都解析回证据卡 ──
    marker_ids: list[str] = []
    for row in drafts:
        found = re.findall(r"<!-- evidence:\s*([0-9a-fA-F-]{36}) -->", row["content_md"])
        marker_ids.extend(found)
    db = test_session_factory()
    try:
        card_ids = set(
            db.scalars(
                select(EvidenceCard.id).where(EvidenceCard.project_id == project_id)
            ).all()
        )
    finally:
        db.close()
    missing = [cid for cid in marker_ids if cid not in card_ids]
    assert not missing, f"unresolvable evidence ids: {missing}"
    assert marker_ids, "no evidence-tagged draft version produced (traceability chain empty)"

    # ── 质量门契约（阈值初版宽松，仅防字段回归）──
    gate = payload["quality_gate"]
    assert isinstance(gate.get("evidence_coverage"), (int, float))
    assert isinstance(gate.get("overall_score"), (int, float))
