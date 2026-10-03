"""Prometheus metrics (process-local registry).

Labels are bounded: HTTP routes use the route *template* (never raw paths with
ids), providers and outcomes come from fixed sets. Nothing derived from
document content is ever a label.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "idp_http_requests_total",
    "HTTP requests by route template and status class.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "idp_http_request_duration_seconds",
    "HTTP request latency by route template.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    registry=REGISTRY,
)
JOBS = Counter("idp_jobs_total", "Processing job runs by outcome.", ("outcome",), registry=REGISTRY)
JOB_LATENCY = Histogram(
    "idp_job_run_duration_seconds",
    "Wall time of one job run (claim to outcome).",
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)
LLM_CALLS = Counter(
    "idp_llm_calls_total",
    "LLM gateway calls by provider, purpose and status.",
    ("provider", "purpose", "status"),
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "idp_llm_tokens_total",
    "LLM tokens by provider and direction.",
    ("provider", "direction"),
    registry=REGISTRY,
)
OUTBOX_PUBLISHED = Counter(
    "idp_outbox_events_published_total", "Outbox events fully published.", registry=REGISTRY
)


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method, route, f"{status // 100}xx").inc()
    HTTP_LATENCY.labels(method, route).observe(seconds)


def render() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
