"""Built-in HTML dashboard and metrics-detail JSON endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from app.middleware.metrics import get_metrics_detail

router = APIRouter(tags=["dashboard"])


@router.get("/api/metrics-detail")
def metrics_detail():
    """Return full metrics snapshot as JSON for the dashboard."""
    return JSONResponse(get_metrics_detail())


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperForge 运营面板</title>
<style>
  :root { --bg:#0d1117; --card:#161b22; --border:#30363d; --text:#c9d1d9; --green:#3fb950; --red:#f85149; --yellow:#d2991d; --blue:#58a6ff; --purple:#bc8cff; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif; background:var(--bg); color:var(--text); padding:24px; max-width:1400px; margin:0 auto; }
  h1 { font-size:20px; margin-bottom:8px; }
  h2 { font-size:16px; margin:24px 0 12px; color:var(--text); }
  .sub { color:#8b949e; margin-bottom:24px; font-size:13px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; margin-bottom:24px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px; }
  .card .label { font-size:11px; color:#8b949e; text-transform:uppercase; letter-spacing:.05em; }
  .card .value { font-size:24px; font-weight:600; margin:2px 0; }
  .card .detail { font-size:11px; color:#8b949e; }
  .green { color:var(--green); }
  .red { color:var(--red); }
  .yellow { color:var(--yellow); }
  .blue { color:var(--blue); }
  .purple { color:var(--purple); }

  /* Histogram bars */
  .hist-wrap { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px; }
  .hist-row { display:flex; align-items:center; margin:4px 0; }
  .hist-label { width:80px; font-size:12px; color:#8b949e; text-align:right; margin-right:10px; flex-shrink:0; }
  .hist-bar-bg { flex:1; background:var(--border); border-radius:4px; height:20px; position:relative; overflow:hidden; }
  .hist-bar-fill { background:var(--blue); border-radius:4px; height:100%; transition:width .5s; min-width:2px; }
  .hist-count { width:50px; font-size:12px; color:#8b949e; text-align:right; margin-left:8px; flex-shrink:0; }

  /* Step timing bars */
  .step-bar { display:flex; align-items:center; margin:6px 0; }
  .step-name { width:90px; font-size:12px; color:var(--text); text-align:right; margin-right:10px; flex-shrink:0; font-weight:500; }
  .step-bar-bg { flex:1; background:var(--border); border-radius:4px; height:24px; position:relative; overflow:hidden; }
  .step-bar-fill { border-radius:4px; height:100%; transition:width .5s; display:flex; align-items:center; padding-left:8px; font-size:11px; color:#fff; font-weight:500; min-width:fit-content; }
  .step-bar-fill.search { background:#1f6feb; }
  .step-bar-fill.ingest { background:#238636; }
  .step-bar-fill.evidence { background:#8957e5; }
  .step-bar-fill.draft { background:#da3633; }
  .step-bar-fill.review { background:#d2991d; }
  .step-bar-fill.export { background:#0d9488; }
  .step-stats { width:160px; font-size:11px; color:#8b949e; text-align:right; margin-left:10px; flex-shrink:0; }

  /* API success table */
  .api-table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--border); border-radius:8px; overflow:hidden; margin-bottom:16px; }
  .api-table th, .api-table td { padding:8px 12px; text-align:left; border-bottom:1px solid var(--border); font-size:13px; }
  .api-table th { color:#8b949e; font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
  .api-table tr:last-child td { border-bottom:none; }
  .rate-good { color:var(--green); font-weight:600; }
  .rate-warn { color:var(--yellow); font-weight:600; }
  .rate-bad { color:var(--red); font-weight:600; }

  /* Task table */
  table.tasks { width:100%; border-collapse:collapse; margin-top:12px; }
  .tasks th, .tasks td { padding:8px 12px; text-align:left; border-bottom:1px solid var(--border); font-size:13px; }
  .tasks th { color:#8b949e; font-weight:500; }
  .tasks tr.task-row { cursor:pointer; }
  .tasks tr.task-row:hover { background:rgba(88,166,255,0.06); }
  .task-detail { background:rgba(22,27,34,0.6); border-bottom:1px solid var(--border); }
  .task-detail-inner { padding:12px 16px; font-size:12px; }
  .detail-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:8px; margin-bottom:10px; }
  .detail-item { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:8px 10px; }
  .detail-item .dlabel { font-size:10px; color:#8b949e; text-transform:uppercase; }
  .detail-item .dvalue { font-size:16px; font-weight:600; margin:2px 0; }
  .detail-section { margin-top:8px; }
  .detail-section h4 { font-size:12px; color:#8b949e; margin-bottom:6px; }
  .mini-step-bar { display:flex; align-items:center; margin:3px 0; }
  .mini-step-name { width:70px; font-size:11px; color:var(--text); text-align:right; margin-right:6px; }
  .mini-step-bg { flex:1; background:var(--border); border-radius:3px; height:16px; overflow:hidden; }
  .mini-step-fill { border-radius:3px; height:100%; font-size:10px; color:#fff; padding-left:4px; display:flex; align-items:center; }
  .mini-step-val { width:60px; font-size:11px; color:#8b949e; text-align:right; margin-left:6px; }
  .quality-badges { display:flex; gap:6px; flex-wrap:wrap; margin-top:4px; }
  .qbadge { padding:2px 8px; border-radius:4px; font-size:11px; font-weight:500; }
  .qbadge.pass { background:rgba(63,185,80,0.15); color:var(--green); }
  .qbadge.fail { background:rgba(248,81,73,0.15); color:var(--red); }
  .logs-wrap { max-height:120px; overflow-y:auto; background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:6px 8px; margin-top:6px; font-family:monospace; font-size:11px; color:#8b949e; line-height:1.6; white-space:pre-wrap; word-break:break-all; }
  .refresh { font-size:12px; color:var(--blue); cursor:pointer; margin-left:12px; }
  .section { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:16px; margin-bottom:16px; }
  .two-col { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
  @media (max-width:900px) { .two-col { grid-template-columns:1fr; } }
  .empty-hint { color:#8b949e; padding:20px; text-align:center; font-size:13px; }
</style>
</head>
<body>
<h1>PaperForge 运营面板</h1>
<p class="sub">每 5 秒自动刷新 <span class="refresh" onclick="fetchData()">&#x21bb; 立即刷新</span></p>

<div class="grid" id="cards"></div>

<div class="two-col">
  <div class="section">
    <h2>任务耗时分布</h2>
    <div id="histogram"></div>
  </div>
  <div class="section">
    <h2>工作流步骤耗时（平均秒数）</h2>
    <div id="step-timing"></div>
  </div>
</div>

<div class="section">
  <h2>外部 API 调用</h2>
  <div id="api-tables"></div>
</div>

<div class="section">
  <h2>任务列表 <span id="task-summary" style="font-size:13px;color:#8b949e;font-weight:400;margin-left:8px;"></span></h2>
  <div id="tasks-container"></div>
</div>

<script>
const STATUS_MAP = {completed:'green',failed:'red',running:'blue'};
const STATUS_CN = {completed:'已完成',failed:'失败',running:'运行中'};
const STEP_CN_TASK = {queued:'排队中',done:'已完成',failed:'失败',downloading:'下载中',parsing:'解析中',
  'downloading and parsing selected papers':'下载解析论文','building evidence cards':'构建证据卡',
  'generating draft':'生成草稿','reviewing draft':'审查草稿','exporting package':'导出文件',
  'searching papers':'检索论文','processing paper':'处理论文',
  'search_and_select':'检索论文','ingest_papers':'下载解析','build_evidence':'构建证据',
  'generate_draft':'生成草稿','initial_review':'初始审查','revise':'修订','review':'审查',
  'export':'导出','assemble_result':'组装结果'};

const HIST_LABELS = ['<10秒','10-30秒','30-60秒','1-2分','2-5分','5-10分','10-20分','>20分'];
const STEP_COLORS = {search:'search',ingest:'ingest',evidence:'evidence',draft:'draft',review:'review',export:'export'};
const STEP_CN = {search:'检索',ingest:'下载解析',evidence:'证据提取',draft:'草稿生成',review:'审查修订',export:'导出'};

function rateClass(rate) { return rate >= 95 ? 'rate-good' : rate >= 80 ? 'rate-warn' : 'rate-bad'; }

// Track which task details are expanded (survives re-render)
const _expandedTasks = new Set();

function toggleDetail(tid) {
  if (_expandedTasks.has(tid)) {
    _expandedTasks.delete(tid);
  } else {
    _expandedTasks.add(tid);
  }
  _applyDetailState(tid);
}

function _applyDetailState(tid) {
  const row = document.getElementById('detail-'+tid);
  const arrow = document.getElementById('arrow-'+tid);
  if (!row) return;
  if (_expandedTasks.has(tid)) {
    row.style.display = '';
    if (arrow) arrow.innerHTML = '&#9660;';
  } else {
    row.style.display = 'none';
    if (arrow) arrow.innerHTML = '&#9654;';
  }
}

/** Parse step timings from task logs (for old tasks without result.step_timings). */
function parseTimingsFromLogs(logs) {
  const timings = {};
  if (!logs) return timings;
  for (const line of logs) {
    // Match "[timing] search: 12.3s"
    const m = line.match(/\[timing\]\s+(\w+):\s+([\d.]+)s/);
    if (m) timings[m[1]] = parseFloat(m[2]);
    // Match "[timing] step_timings={'search': 12.3, ...}"
    const jm = line.match(/\[timing\]\s+step_timings=(\{.+\})/);
    if (jm) {
      try {
        const parsed = JSON.parse(jm[1].replace(/'/g, '"'));
        Object.assign(timings, parsed);
      } catch(e) {}
    }
  }
  return timings;
}

/** Parse node timings from logs (for old tasks without result.node_timings). */
function parseNodeTimingsFromLogs(logs) {
  const timings = {};
  if (!logs) return timings;
  for (const line of logs) {
    // Match "[timing_node] search_and_select: 12.3s"
    const m = line.match(/\[timing_node\]\s+([\w_]+):\s+([\d.]+)s/);
    if (m) timings[m[1]] = parseFloat(m[2]);
  }
  return timings;
}

async function fetchData() {
  try {
    const resp = await fetch('/api/metrics-detail');
    const data = await resp.json();
    const c = data.counters || {};
    const h = data.histograms || {};
    const tc = data.tagged_counters || {};
    const st = data.step_timings || {};

    // --- Metric cards ---
    const total = c.paperforge_requests_total || 0;
    const ok = c.paperforge_requests_2xx || 0;
    const err4 = c.paperforge_requests_4xx || 0;
    const err5 = c.paperforge_requests_5xx || 0;
    const dur = c.paperforge_request_duration_ms_total || 0;
    const avgMs = total ? (dur / total).toFixed(0) : 0;
    const errRate = total ? ((err4 + err5) / total * 100).toFixed(1) : 0;
    const tasksDone = c.paperforge_tasks_completed || 0;
    const tasksFail = c.paperforge_tasks_failed || 0;
    const tasksTotal = tasksDone + tasksFail;
    const successRate = tasksTotal ? (tasksDone / tasksTotal * 100).toFixed(0) : 0;
    const cards = c.paperforge_evidence_cards_generated || 0;
    const gateTotal = c.paperforge_publication_gate_total || 0;
    const gatePass = c.paperforge_publication_gate_passed || 0;
    const gateRate = gateTotal ? (gatePass / gateTotal * 100).toFixed(0) : 0;

    // Compute P50/P90 from histogram
    const histData = h.paperforge_task_duration_seconds;
    let p50='--', p90='--';
    if (histData) {
      const counts = histData.counts;
      const totalH = counts.reduce((a,b)=>a+b,0);
      if (totalH > 0) {
        let cum=0;
        for (let i=0;i<counts.length;i++) {
          cum += counts[i];
          if (p50==='--' && cum >= totalH*0.5) p50 = HIST_LABELS[i];
          if (p90==='--' && cum >= totalH*0.9) p90 = HIST_LABELS[i];
        }
      }
    }

    document.getElementById('cards').innerHTML = [
      {label:'总请求数', value:total, detail:`平均 ${avgMs}ms`, cls:'blue'},
      {label:'错误率', value:errRate+'%', detail:`${err4+err5} 个错误`, cls:errRate>5?'red':'green'},
      {label:'任务完成', value:tasksDone, detail:`${successRate}% 成功`, cls:'green'},
      {label:'任务失败', value:tasksFail, detail:'', cls:tasksFail?'red':'green'},
      {label:'证据卡', value:cards, detail:'累计生成', cls:'blue'},
      {label:'门禁通过率', value:gateRate+'%', detail:`${gatePass}/${gateTotal}`, cls:gateRate>=80?'green':gateRate>=50?'yellow':'red'},
      {label:'任务 P50', value:p50, detail:'耗时', cls:'purple'},
      {label:'任务 P90', value:p90, detail:'耗时', cls:'purple'},
    ].map(c => `<div class="card">
      <div class="label">${c.label}</div>
      <div class="value ${c.cls}">${c.value}</div>
      <div class="detail">${c.detail}</div>
    </div>`).join('');

    // --- Histogram ---
    if (histData) {
      const counts = histData.counts;
      const maxC = Math.max(...counts, 1);
      document.getElementById('histogram').innerHTML = counts.map((c,i) => {
        const pct = (c / maxC * 100).toFixed(0);
        return `<div class="hist-row">
          <span class="hist-label">${HIST_LABELS[i]}</span>
          <div class="hist-bar-bg"><div class="hist-bar-fill" style="width:${pct}%"></div></div>
          <span class="hist-count">${c}</span>
        </div>`;
      }).join('');
    }

    // --- Step timing ---
    const stepNames = ['search','ingest','evidence','draft','review','export'];
    const stepData = stepNames.map(n => ({name:n, ...(st[n]||{count:0,avg:0,p50:0,p90:0,min:0,max:0})}));
    const maxAvg = Math.max(...stepData.map(s=>s.avg), 1);
    document.getElementById('step-timing').innerHTML = stepData.map(s => {
      const pct = (s.avg / maxAvg * 100).toFixed(0);
      const cls = STEP_COLORS[s.name] || '';
      return `<div class="step-bar">
        <span class="step-name">${STEP_CN[s.name]||s.name}</span>
        <div class="step-bar-bg"><div class="step-bar-fill ${cls}" style="width:${pct}%">${s.avg > 0 ? s.avg.toFixed(1)+'秒' : ''}</div></div>
        <span class="step-stats">${s.count} 次 | p50:${s.p50}秒 | p90:${s.p90}秒</span>
      </div>`;
    }).join('');

    // --- API tables ---
    let apiHTML = '';
    for (const group of API_GROUPS) {
      const bucket = tc[group.key] || {};
      let rows = [];
      if (group.tags === 'auto') {
        // Auto-discover provider names from tags
        const providers = new Set();
        for (const tag of Object.keys(bucket)) {
          const dot = tag.lastIndexOf('.');
          if (dot > 0) providers.add(tag.substring(0, dot));
        }
        for (const prov of [...providers].sort()) {
          const ok = bucket[prov+'.ok'] || 0;
          const err = (bucket[prov+'.err'] || 0) + (bucket[prov+'.exhausted'] || 0);
          const total = ok + err;
          const rate = total ? (ok/total*100).toFixed(0) : '--';
          rows.push({name:prov, ok, err, total, rate});
        }
      } else {
        for (const t of group.tags) {
          rows.push({name:t.name, count:bucket[t.tag]||0});
        }
        // Compute rate for simple ok/err groups
        if (rows.length === 2 && (rows[0].name === 'Success' || rows[0].name === '成功')) {
          const ok = rows[0].count, err = rows[1].count, total = ok+err;
          const rate = total ? (ok/total*100).toFixed(0) : '--';
          apiHTML += `<h3 style="font-size:14px;margin:12px 0 8px;">${group.label} <span class="${rateClass(parseFloat(rate))}">${rate}% 成功</span> (共 ${total} 次)</h3>`;
          continue;
        }
      }
      if (rows.length === 0) continue;
      const isAuto = group.tags === 'auto';
      apiHTML += `<h3 style="font-size:14px;margin:12px 0 8px;">${group.label}</h3>`;
      apiHTML += `<table class="api-table"><thead><tr>`;
      if (isAuto) {
        apiHTML += `<th>Provider</th><th>成功</th><th>失败</th><th>总计</th><th>成功率</th>`;
      } else {
        apiHTML += `<th>指标</th><th>计数</th>`;
      }
      apiHTML += `</tr></thead><tbody>`;
      for (const r of rows) {
        if (isAuto) {
          apiHTML += `<tr><td>${r.name}</td><td>${r.ok}</td><td>${r.err}</td><td>${r.total}</td><td class="${rateClass(parseFloat(r.rate))}">${r.rate}%</td></tr>`;

        } else {
          apiHTML += `<tr><td>${r.name}</td><td>${r.count}</td></tr>`;
        }
      }
      apiHTML += `</tbody></table>`;
    }
    document.getElementById('api-tables').innerHTML = apiHTML || '<p style="color:#8b949e">暂无 API 调用数据</p>';

    // --- Tasks ---
    const tResp = await fetch('/api/tasks?limit=10');
    if (tResp.ok) {
      const tasks = await tResp.json();
      const tDone = tasks.filter(t=>t.status==='completed').length;
      const tFail = tasks.filter(t=>t.status==='failed').length;
      const tRun = tasks.filter(t=>t.status==='running').length;
      document.getElementById('task-summary').innerHTML =
        `共 ${tasks.length} 个任务 ( <span class="green">${tDone} 完成</span> / <span class="red">${tFail} 失败</span> / <span class="blue">${tRun} 运行中</span> )`;

      if (tasks.length === 0) {
        document.getElementById('tasks-container').innerHTML =
          '<div class="empty-hint">暂无任务记录。在项目详情页点击「一键自动工作流」后，任务执行情况将在此显示。</div>';
      } else {
        let html = '<table class="tasks"><thead><tr>';
        html += '<th></th><th>任务 ID</th><th>状态</th><th>当前步骤</th><th>进度</th><th>耗时</th>';
        html += '</tr></thead><tbody>';

        for (const t of tasks) {
          const created = new Date(t.created_at);
          const updated = new Date(t.updated_at);
          const durSec = ((updated - created) / 1000).toFixed(0);
          const r = t.result || {};
          const hasResult = Object.keys(r).length > 0;
          const tid = t.task_id;

          html += `<tr class="task-row" onclick="toggleDetail('${tid}')">
            <td style="width:20px;font-size:11px;color:#8b949e" id="arrow-${tid}">&#9654;</td>
            <td style="font-family:monospace;font-size:12px">${tid.slice(0,8)}...</td>
            <td class="${STATUS_MAP[t.status]||''}">${STATUS_CN[t.status]||t.status}</td>
            <td>${STEP_CN_TASK[t.current_step]||t.current_step}</td>
            <td>${t.progress}%</td>
            <td>${durSec}秒</td>
          </tr>`;

          html += `<tr class="task-detail" id="detail-${tid}" style="display:none"><td colspan="6"><div class="task-detail-inner">`;

          // Result summary cards
          if (hasResult) {
            html += '<div class="detail-grid">';
            const items = [
              {l:'检索论文', v: r.selected_count ?? '--'},
              {l:'已下载', v: r.downloaded_count ?? '--'},
              {l:'已解析', v: r.parsed_count ?? '--'},
              {l:'证据卡', v: r.evidence_count ?? '--'},
              {l:'审查问题', v: r.review_issue_count ?? '--'},
              {l:'修订轮次', v: r.revision_rounds_executed ?? '--'},
            ];
            for (const it of items) {
              html += `<div class="detail-item"><div class="dlabel">${it.l}</div><div class="dvalue">${it.v}</div></div>`;
            }
            html += '</div>';

            // Publication gate
            if (r.publication_prepared !== undefined) {
              html += '<div class="quality-badges">';
              html += `<span class="qbadge ${r.publication_prepared?'pass':'fail'}">${r.publication_prepared?'已达到出版标准':'未达出版标准'}</span>`;
              if (r.quality_gate) {
                const qg = r.quality_gate;
                const qMetrics = [
                  {k:'evidence_coverage',l:'证据覆盖'},
                  {k:'citation_validity',l:'引用有效'},
                  {k:'logic_score',l:'逻辑评分'},
                  {k:'style_score',l:'风格评分'},
                ];
                for (const m of qMetrics) {
                  if (qg[m.k] !== undefined) {
                    const val = (qg[m.k] * 100).toFixed(0);
                    const pass = val >= 80;
                    html += `<span class="qbadge ${pass?'pass':'fail'}">${m.l}: ${val}%</span>`;
                  }
                }
              }
              html += '</div>';
            }
          }

          // Step timings: prefer result.step_timings, fallback to parsing logs
          const st = (r.step_timings && Object.keys(r.step_timings).length > 0)
            ? r.step_timings : parseTimingsFromLogs(t.logs);
          if (Object.keys(st).length > 0) {
            const maxT = Math.max(...Object.values(st), 1);
            html += '<div class="detail-section"><h4>步骤耗时</h4>';
            for (const sn of ['search','ingest','evidence','draft','review','export']) {
              const val = st[sn] || 0;
              const pct = (val / maxT * 100).toFixed(0);
              const cls = STEP_COLORS[sn] || '';
              html += `<div class="mini-step-bar">
                <span class="mini-step-name">${STEP_CN[sn]||sn}</span>
                <div class="mini-step-bg"><div class="mini-step-fill ${cls}" style="width:${pct}%">${val>0?(val).toFixed(1)+'秒':''}</div></div>
                <span class="mini-step-val">${val>0?(val).toFixed(1)+'秒':'--'}</span>
              </div>`;
            }
            html += '</div>';
          }

          // Node timings: prefer result.node_timings, fallback to parsing logs
          const nt = (r.node_timings && Object.keys(r.node_timings).length > 0)
            ? r.node_timings : parseNodeTimingsFromLogs(t.logs);
          if (Object.keys(nt).length > 0) {
            html += '<div class="detail-section"><h4>节点耗时 (LangGraph)</h4><div style="display:flex;gap:8px;flex-wrap:wrap">';
            for (const [k,v] of Object.entries(nt)) {
              html += `<span style="background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px">${k}: ${v}秒</span>`;
            }
            html += '</div></div>';
          }

          // Export files
          if (r.export_files && Object.keys(r.export_files).length > 0) {
            html += '<div class="detail-section"><h4>导出文件</h4><div style="display:flex;gap:6px;flex-wrap:wrap">';
            for (const [k,v] of Object.entries(r.export_files)) {
              const fname = v.split(/[/\\]/).pop();
              html += `<span style="background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px">${k}: ${fname}</span>`;
            }
            html += '</div></div>';
          }

          // Error info for failed tasks
          if (r.code || r.message) {
            html += `<div class="detail-section"><h4>错误信息</h4>`;
            html += `<div style="color:var(--red);font-size:12px">${r.code||''}: ${r.message||''}</div>`;
            html += '</div>';
          } else if (!hasResult && (!st || Object.keys(st).length === 0)) {
            html += '<div style="color:#8b949e;font-size:12px">暂无执行结果</div>';
          }

          // Logs
          if (t.logs && t.logs.length > 0) {
            html += '<div class="detail-section"><h4>执行日志（最近 30 条）</h4>';
            html += `<div class="logs-wrap">${t.logs.slice(-30).join('\n')}</div>`;
            html += '</div>';
          }

          html += '</div></td></tr>';
        }
        html += '</tbody></table>';
        document.getElementById('tasks-container').innerHTML = html;

        // Restore expanded state after re-render
        for (const tid of _expandedTasks) {
          _applyDetailState(tid);
        }
      }
    }
  } catch(e) {
    console.error('面板数据加载失败:', e);
  }
}
fetchData();
setInterval(fetchData, 5000);
</script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML
