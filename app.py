"""FastAPI application.

Routes are thin wrappers over ``api.handlers.ApiHandlers`` (shared with the in browser runtime).
Cross cutting concerns live here: API key authentication, proxy aware per client rate limiting,
request size limits, security headers, request correlation and Prometheus metrics.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..config import Settings, get_settings
from ..domain.models import (
    PatientContext,
    Recommendation,
    RenalEstimate,
    ReviewRecord,
    ReviewRequest,
    SubstitutionRequest,
)
from ..logging_setup import configure_logging, log_event, request_id_var
from ..observability import (
    AUTH_FAILURES,
    CONTENT_TYPE_LATEST,
    CORPUS,
    HTTP_LATENCY,
    HTTP_REQUESTS,
    REGISTRY,
    generate_latest,
)
from .container import Container
from .handlers import GATE_DEFINITIONS, ApiError, ApiHandlers
from .schemas import (
    AuditVerification,
    BatchRequest,
    BatchResponse,
    ConvertRequest,
    DrugSuggestion,
    EvidenceResponse,
    FhirSubstitutionResponse,
    MetaResponse,
    ShortagePage,
)

__all__ = ["GATE_DEFINITIONS", "SlidingWindowLimiter", "app_factory", "create_app"]

PUBLIC_PATHS = {"/health", "/ready", "/metrics", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Resource-Policy": "same-site",
}


class SlidingWindowLimiter:
    """Per client sliding window limiter with periodic pruning of idle clients.

    Production deployments should also limit at the gateway; this protects the engine itself.
    """

    def __init__(self, per_minute: int, prune_every: int = 500) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self._calls = 0
        self._prune_every = prune_every

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        self._calls += 1
        if self._calls % self._prune_every == 0:
            self._prune(now)
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True

    def _prune(self, now: float) -> None:
        for k in [k for k, q in self.hits.items() if not q or now - q[-1] > 60]:
            del self.hits[k]

    def remaining(self, key: str) -> int:
        return max(0, self.per_minute - len(self.hits.get(key, ())))


def client_key(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
        real = request.headers.get("X-Real-IP")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


def presented_key(request: Request) -> str | None:
    key = request.headers.get("X-API-Key")
    if key:
        return key.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def key_ok(candidate: str | None, keys: frozenset[str]) -> bool:
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate.encode(), k.encode()) for k in keys)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    keys = settings.api_key_set

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or Container.build(settings)
        app.state.container = c
        app.state.handlers = ApiHandlers(c, runtime="server", auth_required=bool(keys))
        for k, v in c.store.stats().items():
            CORPUS.labels(kind=k).set(v)
        if settings.environment == "production" and not keys:
            log_event(logging.getLogger(__name__), "api_auth_disabled_in_production",
                      level=logging.WARNING, hint="set SFB_API_KEYS")
        yield

    app = FastAPI(
        title="Shortfall Bridge AI",
        version=__version__,
        description=(
            "Drug shortage therapeutic substitution decision support grounded in FDA labelling. "
            "Authenticate with `X-API-Key` or `Authorization: Bearer` when the deployment sets `SFB_API_KEYS`."
        ),
        lifespan=lifespan,
    )

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"], expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
    )
    limiter = SlidingWindowLimiter(settings.api_rate_limit_per_minute)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        path = request.url.path
        guarded = path.startswith("/api/") or path.startswith("/cds-services")
        try:
            if guarded and request.method != "OPTIONS":
                length = request.headers.get("content-length")
                if length and length.isdigit() and int(length) > settings.max_body_bytes:
                    return _plain(JSONResponse({"detail": "Request body too large"}, status_code=413), rid)
                if keys and not key_ok(presented_key(request), keys):
                    AUTH_FAILURES.inc()
                    return _plain(JSONResponse({"detail": "Missing or invalid API key"}, status_code=401,
                                               headers={"WWW-Authenticate": "Bearer"}), rid)
                client = client_key(request, settings.trust_proxy_headers)
                if not limiter.allow(client):
                    return _plain(JSONResponse({"detail": "Rate limit exceeded"}, status_code=429,
                                               headers={"Retry-After": "60"}), rid)
            t0 = time.perf_counter()
            response: Response = await call_next(request)
            route = request.scope.get("route").path if request.scope.get("route") else "unmatched"
            HTTP_LATENCY.labels(route=route).observe(time.perf_counter() - t0)
            HTTP_REQUESTS.labels(method=request.method, route=route, status=str(response.status_code)).inc()
            if guarded:
                response.headers["X-RateLimit-Remaining"] = str(limiter.remaining(client_key(request, settings.trust_proxy_headers)))
            return _plain(response, rid, docs=path in {"/docs", "/redoc"})
        finally:
            request_id_var.reset(token)

    def _plain(response: Response, rid: str, docs: bool = False) -> Response:
        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if not docs:
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response

    def h(request: Request) -> ApiHandlers:
        return request.app.state.handlers

    # Probes ------------------------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(handlers: ApiHandlers = Depends(h)) -> JSONResponse:
        store = handlers.c.store
        readiness = getattr(store, "readiness", None)
        if callable(readiness):
            ok, detail = readiness()
            return JSONResponse({"ready": ok, **detail, "corpus": store.stats()}, status_code=200 if ok else 503)
        stats = store.stats()
        ok = stats.get("labels", 0) > 0
        return JSONResponse({"ready": ok, "corpus": stats}, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    # Reference ---------------------------------------------------------------------------------

    @app.get("/api/v1/meta", response_model=MetaResponse, tags=["reference"])
    def meta(handlers: ApiHandlers = Depends(h)) -> MetaResponse:
        return handlers.meta()

    @app.get("/api/v1/drugs/search", response_model=list[DrugSuggestion], tags=["reference"])
    def search_drugs(q: str = Query(min_length=2, max_length=80), limit: int = Query(8, ge=1, le=25),
                     handlers: ApiHandlers = Depends(h)) -> list[DrugSuggestion]:
        return handlers.search_drugs(q, limit)

    @app.get("/api/v1/shortages", response_model=ShortagePage, tags=["reference"])
    def list_shortages(status: str | None = None, q: str | None = Query(None, max_length=80),
                       limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
                       handlers: ApiHandlers = Depends(h)) -> ShortagePage:
        return handlers.shortages(status, q, limit, offset)

    @app.get("/api/v1/shortages/insights", tags=["reference"])
    def insights(handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return handlers.insights()

    @app.get("/api/v1/evidence/{chunk_id}", response_model=EvidenceResponse, tags=["reference"])
    def evidence(chunk_id: str, handlers: ApiHandlers = Depends(h)) -> EvidenceResponse:
        return handlers.evidence(chunk_id)

    @app.get("/api/v1/equivalence/tables", tags=["reference"])
    def equivalence_tables(handlers: ApiHandlers = Depends(h)) -> list[dict[str, Any]]:
        return handlers.equivalence_tables()

    @app.post("/api/v1/equivalence/convert", tags=["reference"])
    def equivalence_convert(body: ConvertRequest, handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return handlers.convert(body).model_dump(mode="json")

    @app.post("/api/v1/renal/estimate", response_model=RenalEstimate, tags=["reference"])
    def renal_estimate(patient: PatientContext, handlers: ApiHandlers = Depends(h)) -> RenalEstimate:
        return handlers.renal(patient)

    # Governance --------------------------------------------------------------------------------

    @app.get("/api/v1/audit/verify", response_model=AuditVerification, tags=["governance"])
    def audit_verify(handlers: ApiHandlers = Depends(h)) -> AuditVerification:
        return handlers.audit_verify()

    @app.get("/api/v1/audit/{request_id}", tags=["governance"])
    def audit(request_id: str, handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return handlers.audit(request_id)

    @app.get("/api/v1/audit/{request_id}/note", tags=["governance"], response_class=PlainTextResponse)
    def audit_note(request_id: str, format: str = Query("markdown", pattern="^(markdown|text)$"),
                   handlers: ApiHandlers = Depends(h)) -> PlainTextResponse:
        media = "text/markdown; charset=utf-8" if format == "markdown" else "text/plain; charset=utf-8"
        ext = "md" if format == "markdown" else "txt"
        return PlainTextResponse(handlers.note(request_id, format), media_type=media,
                                 headers={"Content-Disposition": f'attachment; filename="shortfall-{request_id}.{ext}"'})

    @app.post("/api/v1/reviews", response_model=ReviewRecord, status_code=201, tags=["governance"])
    def create_review(body: ReviewRequest, handlers: ApiHandlers = Depends(h)) -> ReviewRecord:
        return handlers.review(body)

    @app.get("/api/v1/reviews", response_model=list[ReviewRecord], tags=["governance"])
    def list_reviews(request_id: str | None = None, limit: int = Query(200, ge=1, le=1000),
                     handlers: ApiHandlers = Depends(h)) -> list[ReviewRecord]:
        return handlers.reviews(request_id, limit)

    @app.get("/api/v1/reviews/summary", tags=["governance"])
    def reviews_summary(handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return handlers.review_summary()

    # Core --------------------------------------------------------------------------------------

    @app.post("/api/v1/substitutions", response_model=Recommendation, tags=["substitution"])
    async def substitutions(req: SubstitutionRequest, handlers: ApiHandlers = Depends(h)) -> Recommendation:
        return await run_in_threadpool(handlers.recommend, req)

    @app.post("/api/v1/substitutions/batch", response_model=BatchResponse, tags=["substitution"])
    async def substitutions_batch(body: BatchRequest, handlers: ApiHandlers = Depends(h)) -> BatchResponse:
        return await run_in_threadpool(handlers.batch, body)

    @app.post("/api/v1/fhir/substitutions", response_model=FhirSubstitutionResponse, tags=["interoperability"])
    async def fhir_substitutions(payload: dict[str, Any] = Body(...), order_id: str | None = None,
                                 handlers: ApiHandlers = Depends(h)) -> FhirSubstitutionResponse:
        return await run_in_threadpool(handlers.fhir_substitution, payload, order_id)

    @app.get("/cds-services", tags=["interoperability"])
    def cds_discovery(handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return handlers.cds_discovery()

    @app.post("/cds-services/{service_id}", tags=["interoperability"])
    async def cds_service(service_id: str, body: dict[str, Any] = Body(...),
                          handlers: ApiHandlers = Depends(h)) -> dict[str, Any]:
        return await run_in_threadpool(handlers.cds_hook, service_id, body)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn shortfall_bridge.api.app:app_factory --factory``."""
    return create_app()
