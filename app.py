"""FastAPI application."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..config import Settings, get_settings
from ..domain.enums import CandidateTier, GateName, ShortageStatus
from ..domain.models import Recommendation, SubstitutionRequest
from ..engine.gates import INTERACTION_SECTIONS, PREGNANCY_SECTIONS, RENAL_SECTIONS, SAFETY_SECTIONS
from ..engine.service import SubstitutionService
from ..logging_setup import configure_logging, request_id_var
from ..observability import CORPUS, HTTP_LATENCY, HTTP_REQUESTS, REGISTRY
from ..store.base import KnowledgeStore
from .container import Container
from .schemas import DrugSuggestion, EvidenceResponse, GateDefinition, MetaResponse, ShortagePage

GATE_DEFINITIONS = [
    GateDefinition(gate=GateName.AVAILABILITY, question="Is the alternative itself in a current FDA shortage?",
                   sections=["FDA Drug Shortages database"]),
    GateDefinition(gate=GateName.ALLERGY, question="Does the patient's allergy list match the drug, its class or a cross reactive class?",
                   sections=["contraindications", "RxClass and EPC classes"]),
    GateDefinition(gate=GateName.RENAL, question="Does the label restrict or adjust use at this renal function?",
                   sections=[s.value for s in RENAL_SECTIONS]),
    GateDefinition(gate=GateName.HEPATIC, question="Does the label restrict use at this degree of hepatic impairment?",
                   sections=[s.value for s in RENAL_SECTIONS]),
    GateDefinition(gate=GateName.PREGNANCY_LACTATION, question="Is it contraindicated or harmful in pregnancy or lactation?",
                   sections=[s.value for s in PREGNANCY_SECTIONS]),
    GateDefinition(gate=GateName.INTERACTIONS, question="Do either label's interaction sections name the patient's medicines or their classes?",
                   sections=[s.value for s in INTERACTION_SECTIONS]),
    GateDefinition(gate=GateName.BOXED_WARNING, question="Does the drug carry a boxed warning?",
                   sections=[s.value for s in SAFETY_SECTIONS[:1]]),
    GateDefinition(gate=GateName.AGE, question="Is use limited in children or flagged in older adults?",
                   sections=["pediatric_use", "geriatric_use"]),
]


class SlidingWindowLimiter:
    """Per client sliding window limiter. Production deployments should also limit at the gateway."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or Container.build(settings)
        for k, v in app.state.container.store.stats().items():
            CORPUS.labels(kind=k).set(v)
        yield

    app = FastAPI(
        title="Shortfall Bridge AI",
        version=__version__,
        description="Drug shortage therapeutic substitution decision support grounded in FDA labelling.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
        allow_headers=["*"], expose_headers=["X-Request-ID"],
    )
    limiter = SlidingWindowLimiter(settings.api_rate_limit_per_minute)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        client = request.client.host if request.client else "unknown"
        route = request.scope.get("route").path if request.scope.get("route") else request.url.path
        if request.url.path.startswith("/api/") and not limiter.allow(client):
            request_id_var.reset(token)
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        t0 = time.perf_counter()
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        route = request.scope.get("route").path if request.scope.get("route") else route
        HTTP_LATENCY.labels(route=route).observe(time.perf_counter() - t0)
        HTTP_REQUESTS.labels(method=request.method, route=route, status=str(response.status_code)).inc()
        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_container(request: Request) -> Container:
        return request.app.state.container

    def get_store(c: Container = Depends(get_container)) -> KnowledgeStore:
        return c.store

    # Probes ------------------------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready(store: KnowledgeStore = Depends(get_store)) -> JSONResponse:
        stats = store.stats()
        ok = stats.get("labels", 0) > 0
        return JSONResponse({"ready": ok, "corpus": stats}, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    # Reference ---------------------------------------------------------------------------------

    @app.get("/api/v1/meta", response_model=MetaResponse, tags=["reference"])
    def meta(c: Container = Depends(get_container)) -> MetaResponse:
        return MetaResponse(
            version=__version__, corpus=c.store.stats(), data_versions=c.store.data_versions(),
            embedding_model=c.embedder.name, llm_provider=c.settings.llm_provider,
            tiers=[{"id": t.value, "label": t.label} for t in CandidateTier],
            gates=GATE_DEFINITIONS,
        )

    @app.get("/api/v1/drugs/search", response_model=list[DrugSuggestion], tags=["reference"])
    def search_drugs(
        q: str = Query(min_length=2, max_length=80), limit: int = Query(8, ge=1, le=25),
        store: KnowledgeStore = Depends(get_store),
    ) -> list[DrugSuggestion]:
        out = []
        for ing, display, score in store.search_ingredients(q, limit):
            current = any(r.status is ShortageStatus.CURRENT for r in store.shortages_for_ingredient(ing))
            out.append(DrugSuggestion(ingredient=ing, display=display, score=score, in_current_shortage=current))
        return out

    @app.get("/api/v1/shortages", response_model=ShortagePage, tags=["reference"])
    def list_shortages(
        status: ShortageStatus | None = None, q: str | None = Query(None, max_length=80),
        limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0),
        store: KnowledgeStore = Depends(get_store),
    ) -> ShortagePage:
        items, total = store.shortages(status=status, query=q, limit=limit, offset=offset)
        return ShortagePage(items=items, total=total, limit=limit, offset=offset)

    @app.get("/api/v1/evidence/{chunk_id}", response_model=EvidenceResponse, tags=["reference"])
    def evidence(chunk_id: str, store: KnowledgeStore = Depends(get_store)) -> EvidenceResponse:
        chunks = store.chunks([chunk_id])
        if not chunks:
            raise HTTPException(404, "Evidence passage not found")
        ch = chunks[0]
        return EvidenceResponse(citation=SubstitutionService._citation(ch), label=store.label(ch.set_id))

    @app.get("/api/v1/audit/{request_id}", tags=["governance"])
    def audit(request_id: str, store: KnowledgeStore = Depends(get_store)) -> dict:
        record = store.get_audit(request_id)
        if not record:
            raise HTTPException(404, "Audit record not found")
        return record

    # Core --------------------------------------------------------------------------------------

    @app.post("/api/v1/substitutions", response_model=Recommendation, tags=["substitution"])
    async def substitutions(req: SubstitutionRequest, c: Container = Depends(get_container)) -> Recommendation:
        return await run_in_threadpool(c.service.recommend, req)

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn shortfall_bridge.api.app:app_factory --factory``."""
    return create_app()
