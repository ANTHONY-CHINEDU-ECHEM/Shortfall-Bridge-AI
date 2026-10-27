"""Prometheus metrics, exposed at /metrics.

``prometheus_client`` is optional: the browser runtime and minimal installs fall back to no op
metrics with the same interface, so the engine code is identical everywhere.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    METRICS_ENABLED = True
except ImportError:  # pragma: no cover - exercised in the browser runtime
    METRICS_ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoOp:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def labels(self, *args: Any, **kwargs: Any) -> _NoOp:
            return self

        def inc(self, *args: Any, **kwargs: Any) -> None:
            return None

        def observe(self, *args: Any, **kwargs: Any) -> None:
            return None

        def set(self, *args: Any, **kwargs: Any) -> None:
            return None

    CollectorRegistry = Counter = Gauge = Histogram = _NoOp  # type: ignore[assignment,misc]

    def generate_latest(registry: Any = None) -> bytes:  # type: ignore[misc]
        return b"# metrics disabled: prometheus_client is not installed\n"


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
REVIEWS = Counter("sfb_reviews_total", "Pharmacist reviews by decision", ["decision"], registry=REGISTRY)
AUTH_FAILURES = Counter("sfb_auth_failures_total", "Rejected API key attempts", registry=REGISTRY)
CDS_CARDS = Counter("sfb_cds_cards_total", "CDS Hooks cards returned", ["hook", "indicator"], registry=REGISTRY)
