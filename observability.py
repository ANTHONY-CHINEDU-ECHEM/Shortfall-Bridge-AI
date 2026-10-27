"""Prometheus metrics. Exposed at /metrics."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "sfb_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY
)
HTTP_LATENCY = Histogram(
    "sfb_http_request_seconds", "HTTP request latency", ["route"], registry=REGISTRY,
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)
RECOMMENDATIONS = Counter(
    "sfb_recommendations_total", "Substitution recommendations by outcome", ["status"], registry=REGISTRY
)
STAGE_LATENCY = Histogram(
    "sfb_engine_stage_seconds", "Engine stage latency", ["stage"], registry=REGISTRY,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
GATE_OUTCOMES = Counter(
    "sfb_gate_outcomes_total", "Safety gate outcomes", ["gate", "status"], registry=REGISTRY
)
NARRATIVE = Counter(
    "sfb_narrative_total", "Narrative generator used", ["generator", "fallback"], registry=REGISTRY
)
NARRATIVE_ACCEPTANCE = Histogram(
    "sfb_narrative_acceptance_ratio", "Share of LLM sentences passing verification", registry=REGISTRY,
    buckets=(0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0),
)
CORPUS = Gauge("sfb_corpus_items", "Indexed corpus size", ["kind"], registry=REGISTRY)
