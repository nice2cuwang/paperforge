"""Process-internal metrics counters (zero external dependencies).

Exposes GET /metrics in Prometheus text format
and GET /api/metrics-detail in JSON format for the dashboard.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_lock = Lock()

# -- counter store ----------------------------------------------------
_counters: dict[str, int] = {
    "paperforge_requests_total": 0,
    "paperforge_requests_2xx": 0,
    "paperforge_requests_4xx": 0,
    "paperforge_requests_5xx": 0,
    "paperforge_request_duration_ms_total": 0,
    "paperforge_tasks_created": 0,
    "paperforge_tasks_completed": 0,
    "paperforge_tasks_failed": 0,
    "paperforge_evidence_cards_generated": 0,
    "paperforge_publication_gate_passed": 0,
    "paperforge_publication_gate_total": 0,
}

# -- histogram store (task duration distribution) ---------------------
_histograms: dict[str, dict[str, Any]] = {
    "paperforge_task_duration_seconds": {
        "buckets": [10, 30, 60, 120, 300, 600, 1200],
        "counts": [0, 0, 0, 0, 0, 0, 0, 0],  # last is +Inf
    },
}

# -- tagged counter store (external API calls) ------------------------
_tagged_counters: dict[str, dict[str, int]] = {
    "paperforge_search_api_calls": {},
    "paperforge_llm_api_calls": {},
    "paperforge_grobid_api_calls": {},
    "paperforge_pdf_download_calls": {},
    "paperforge_parse_calls": {},
}

# -- step timing store (per-workflow-step durations) ------------------
_step_timings: dict[str, list[float]] = {
    "search": [],
    "ingest": [],
    "evidence": [],
    "draft": [],
    "review": [],
    "export": [],
}

# Max samples to keep per step (rolling window)
_MAX_STEP_SAMPLES = 200


def metrics_inc(key: str, delta: int = 1) -> None:
    with _lock:
        if key in _counters:
            _counters[key] += delta


def metrics_observe(key: str, value: float) -> None:
    """Record a value into a histogram."""
    with _lock:
        hist = _histograms.get(key)
        if hist is None:
            return
        for i, boundary in enumerate(hist["buckets"]):
            if value <= boundary:
                hist["counts"][i] += 1
                break
        else:
            hist["counts"][-1] += 1  # +Inf bucket


def metrics_inc_tagged(key: str, tag: str, delta: int = 1) -> None:
    """Increment a tagged counter (e.g. 'openalex.ok')."""
    with _lock:
        bucket = _tagged_counters.get(key)
        if bucket is None:
            _tagged_counters[key] = {}
            bucket = _tagged_counters[key]
        bucket[tag] = bucket.get(tag, 0) + delta


def metrics_record_step(step_name: str, duration_seconds: float) -> None:
    """Record a workflow step duration (rolling window)."""
    with _lock:
        samples = _step_timings.get(step_name)
        if samples is None:
            _step_timings[step_name] = []
            samples = _step_timings[step_name]
        samples.append(duration_seconds)
        if len(samples) > _MAX_STEP_SAMPLES:
            samples.pop(0)


# -- exposed for dashboard / other consumers --------------------------

def get_metrics() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def get_metrics_detail() -> dict[str, Any]:
    """Full metrics snapshot for the dashboard JSON endpoint."""
    with _lock:
        counters = dict(_counters)
        histograms = {}
        for k, v in _histograms.items():
            histograms[k] = {"buckets": list(v["buckets"]), "counts": list(v["counts"])}
        tagged = {}
        for k, v in _tagged_counters.items():
            tagged[k] = dict(v)
        step_timings = {}
        for k, v in _step_timings.items():
            if v:
                sorted_v = sorted(v)
                n = len(sorted_v)
                step_timings[k] = {
                    "count": n,
                    "avg": round(sum(sorted_v) / n, 2),
                    "p50": round(sorted_v[int(n * 0.5)], 2),
                    "p90": round(sorted_v[int(n * 0.9)], 2),
                    "p99": round(sorted_v[int(n * 0.99)], 2),
                    "min": round(sorted_v[0], 2),
                    "max": round(sorted_v[-1], 2),
                }
            else:
                step_timings[k] = {"count": 0, "avg": 0, "p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0}
    return {
        "counters": counters,
        "histograms": histograms,
        "tagged_counters": tagged,
        "step_timings": step_timings,
    }


# -- Prometheus text format endpoint ----------------------------------

def metrics_text() -> str:
    with _lock:
        items = dict(_counters)
        histograms = {k: dict(v) for k, v in _histograms.items()}
        tagged = {k: dict(v) for k, v in _tagged_counters.items()}

    lines = [
        "# HELP paperforge_requests_total Total HTTP requests.",
        "# TYPE paperforge_requests_total counter",
        f"paperforge_requests_total {items['paperforge_requests_total']}",
        "",
        "# HELP paperforge_requests_2xx Successful requests.",
        "# TYPE paperforge_requests_2xx counter",
        f"paperforge_requests_2xx {items['paperforge_requests_2xx']}",
        "",
        "# HELP paperforge_requests_4xx Client errors.",
        "# TYPE paperforge_requests_4xx counter",
        f"paperforge_requests_4xx {items['paperforge_requests_4xx']}",
        "",
        "# HELP paperforge_requests_5xx Server errors.",
        "# TYPE paperforge_requests_5xx counter",
        f"paperforge_requests_5xx {items['paperforge_requests_5xx']}",
        "",
        "# HELP paperforge_request_duration_ms_total Total request duration in ms.",
        "# TYPE paperforge_request_duration_ms_total counter",
        f"paperforge_request_duration_ms_total {items['paperforge_request_duration_ms_total']}",
        "",
        "# HELP paperforge_tasks_created Total workflow tasks created.",
        "# TYPE paperforge_tasks_created counter",
        f"paperforge_tasks_created {items['paperforge_tasks_created']}",
        "",
        "# HELP paperforge_tasks_completed Successfully completed tasks.",
        "# TYPE paperforge_tasks_completed counter",
        f"paperforge_tasks_completed {items['paperforge_tasks_completed']}",
        "",
        "# HELP paperforge_tasks_failed Failed tasks.",
        "# TYPE paperforge_tasks_failed counter",
        f"paperforge_tasks_failed {items['paperforge_tasks_failed']}",
        "",
        "# HELP paperforge_evidence_cards_generated Total evidence cards generated.",
        "# TYPE paperforge_evidence_cards_generated counter",
        f"paperforge_evidence_cards_generated {items['paperforge_evidence_cards_generated']}",
        "",
        "# HELP paperforge_publication_gate_passed Times publication gate passed.",
        "# TYPE paperforge_publication_gate_passed counter",
        f"paperforge_publication_gate_passed {items['paperforge_publication_gate_passed']}",
        "",
        "# HELP paperforge_publication_gate_total Total publication gate evaluations.",
        "# TYPE paperforge_publication_gate_total counter",
        f"paperforge_publication_gate_total {items['paperforge_publication_gate_total']}",
        "",
    ]

    # Histograms
    for name, hist in histograms.items():
        lines.append(f"# HELP {name} Distribution of {name}.")
        lines.append(f"# TYPE {name} histogram")
        cumulative = 0
        for i, boundary in enumerate(hist["buckets"]):
            cumulative += hist["counts"][i]
            lines.append(f'{name}_bucket{{le="{boundary}"}} {cumulative}')
        cumulative += hist["counts"][-1]
        lines.append(f'{name}_bucket{{le="+Inf"}} {cumulative}')
        lines.append("")

    # Tagged counters
    for name, tags in tagged.items():
        lines.append(f"# HELP {name} {name} by tag.")
        lines.append(f"# TYPE {name} counter")
        for tag, count in sorted(tags.items()):
            lines.append(f'{name}{{tag="{tag}"}} {count}')
        lines.append("")

    return "\n".join(lines)


# -- Middleware -------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/metrics":
            return PlainTextResponse(metrics_text(), media_type="text/plain; charset=utf-8")

        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)

        with _lock:
            _counters["paperforge_requests_total"] += 1
            _counters["paperforge_request_duration_ms_total"] += duration_ms
            if 200 <= response.status_code < 300:
                _counters["paperforge_requests_2xx"] += 1
            elif 400 <= response.status_code < 500:
                _counters["paperforge_requests_4xx"] += 1
            elif response.status_code >= 500:
                _counters["paperforge_requests_5xx"] += 1
        return response
