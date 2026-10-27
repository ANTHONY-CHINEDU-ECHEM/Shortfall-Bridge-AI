"""Framework free API handlers shared by the FastAPI app and the in browser runtime.

Each method validates nothing that pydantic has not already validated, performs one API
operation against the container and returns a pydantic model or plain JSON. FastAPI routes are
thin wrappers around these, and ``browser.runtime`` dispatches the same paths to them inside
Pyodide, so the hosted demo exercises exactly the code the server runs.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from .. import __version__
from ..analytics.insights import shortage_insights
from ..analytics.reviews import review_summary
from ..clinical.renal import estimate_renal_function
from ..domain.enums import CandidateTier, GateName, ShortageStatus
from ..domain.models import (
    DoseConversion,
    PatientContext,
    Recommendation,
    RenalEstimate,
    ReviewRecord,
    ReviewRequest,
    SubstitutionRequest,
)
from ..engine.equivalence import convert, load_tables
from ..engine.gates import INTERACTION_SECTIONS, PREGNANCY_SECTIONS, RENAL_SECTIONS, SAFETY_SECTIONS
from ..engine.service import SubstitutionService
from ..interop import cds_hooks
from ..interop.fhir import FhirMappingError, map_fhir
from ..observability import REVIEWS
from ..reporting.note import render_note
from .container import Container
from .schemas import (
    AuditVerification,
    BatchItem,
    BatchRequest,
    BatchResponse,
    ConvertRequest,
    DrugSuggestion,
    EvidenceResponse,
    FhirSubstitutionResponse,
    GateDefinition,
    MetaResponse,
    ShortagePage,
)

FEATURES = [
    "four_tier_substitution", "eight_safety_gates", "dose_equivalence", "dose_ceiling_check",
    "renal_estimation", "class_duplication_check", "hybrid_retrieval", "grounded_narrative",
    "hash_chained_audit", "pharmacist_review", "shortage_insights", "batch", "fhir_r4_input",
    "cds_hooks_2", "clinical_note_export", "score_breakdown",
]

GATE_DEFINITIONS = [
    GateDefinition(gate=GateName.AVAILABILITY, question="Is the alternative itself in a current FDA shortage by the route it would be used?",
                   sections=["FDA Drug Shortages database"]),
    GateDefinition(gate=GateName.ALLERGY, question="Does the patient's allergy list match the drug, its class, a cross reactive class or a labelled hypersensitivity warning?",
                   sections=["contraindications", "warnings_and_cautions", "RxClass and EPC classes"]),
    GateDefinition(gate=GateName.RENAL, question="Does the label restrict or adjust use at this renal function (entered or estimated)?",
                   sections=[s.value for s in RENAL_SECTIONS]),
    GateDefinition(gate=GateName.HEPATIC, question="Does the label restrict use at this degree of hepatic impairment?",
                   sections=[s.value for s in RENAL_SECTIONS]),
    GateDefinition(gate=GateName.PREGNANCY_LACTATION, question="Is it contraindicated or harmful in pregnancy or lactation?",
                   sections=[s.value for s in PREGNANCY_SECTIONS]),
    GateDefinition(gate=GateName.INTERACTIONS, question="Do either label's interaction sections name the patient's medicines or their classes, or is it duplicate therapy?",
                   sections=[s.value for s in INTERACTION_SECTIONS]),
    GateDefinition(gate=GateName.BOXED_WARNING, question="Does the drug carry a boxed warning?",
                   sections=[s.value for s in SAFETY_SECTIONS[:1]]),
    GateDefinition(gate=GateName.AGE, question="Is use limited in children or flagged in older adults?",
                   sections=["pediatric_use", "geriatric_use"]),
]


class ApiError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status, self.detail = status, detail


class ApiHandlers:
    def __init__(self, container: Container, runtime: str = "server", auth_required: bool = False) -> None:
        self.c = container
        self.runtime = runtime
        self.auth_required = auth_required

    @property
    def service(self) -> SubstitutionService:
        return self.c.service

    # Reference ---------------------------------------------------------------------------------

    def meta(self) -> MetaResponse:
        store = self.c.store
        return MetaResponse(
            version=__version__, corpus=store.stats(), data_versions=store.data_versions(),
            embedding_model=self.c.embedder.name, llm_provider=self.c.settings.llm_provider,
            tiers=[{"id": t.value, "label": t.label} for t in CandidateTier],
            gates=GATE_DEFINITIONS,
            store_mode="live_openfda" if getattr(store, "live", False) else self.c.settings.store_backend,
            runtime=self.runtime, auth_required=self.auth_required, features=FEATURES,
        )

    def search_drugs(self, q: str, limit: int = 8) -> list[DrugSuggestion]:
        q = q.strip()
        if not 2 <= len(q) <= 80:
            raise ApiError(422, [{"loc": ["query", "q"], "msg": "String should have 2 to 80 characters",
                                  "type": "string_too_short" if len(q) < 2 else "string_too_long"}])
        store = self.c.store
        out = []
        for ing, display, score in store.search_ingredients(q, max(1, min(limit, 25))):
            current = any(r.status is ShortageStatus.CURRENT for r in store.shortages_for_ingredient(ing))
            out.append(DrugSuggestion(ingredient=ing, display=display, score=score, in_current_shortage=current))
        return out

    def shortages(self, status: str | None = None, q: str | None = None, limit: int = 25, offset: int = 0) -> ShortagePage:
        st = ShortageStatus.parse(status) if status else None
        if status and st is ShortageStatus.UNKNOWN and status.lower() != "unknown":
            raise ApiError(422, f"Unknown status '{status}'")
        limit = max(1, min(limit, 200))
        items, total = self.c.store.shortages(status=st, query=(q or None), limit=limit, offset=max(0, offset))
        return ShortagePage(items=items, total=total, limit=limit, offset=offset)

    def insights(self) -> dict[str, Any]:
        records, _ = self.c.store.shortages(limit=100_000)
        return shortage_insights(records)

    def evidence(self, chunk_id: str) -> EvidenceResponse:
        chunks = self.c.store.chunks([chunk_id])
        if not chunks:
            raise ApiError(404, "Evidence passage not found")
        ch = chunks[0]
        return EvidenceResponse(citation=SubstitutionService._citation(ch), label=self.c.store.label(ch.set_id))

    def equivalence_tables(self) -> list[dict[str, Any]]:
        return [
            {"id": t.id, "type": t.type, "title": t.title, "unit": t.unit, "reference": t.reference,
             "reference_url": t.reference_url, "members": t.members, "excluded": t.excluded, "warnings": t.warnings,
             "cross_tolerance_reduction": list(t.cross_tolerance_reduction) if t.cross_tolerance_reduction else None}
            for t in load_tables()
        ]

    def convert(self, body: ConvertRequest) -> DoseConversion:
        dc = convert(body.from_drug, body.to_drug, body.dose, body.from_route, body.to_route)
        if dc is None:
            raise ApiError(404, f"No published equivalence table relates {body.from_drug} and {body.to_drug}.")
        return dc

    def renal(self, patient: PatientContext) -> RenalEstimate:
        return estimate_renal_function(patient)

    # Governance --------------------------------------------------------------------------------

    def audit(self, request_id: str) -> dict[str, Any]:
        record = self.c.store.get_audit(request_id)
        if not record:
            raise ApiError(404, "Audit record not found")
        return record

    def audit_verify(self) -> AuditVerification:
        return AuditVerification(**self.c.store.verify_audit_chain())

    def note(self, request_id: str, fmt: str = "markdown") -> str:
        if fmt not in {"markdown", "text"}:
            raise ApiError(422, "format must be markdown or text")
        return render_note(self.audit(request_id), fmt)

    def review(self, body: ReviewRequest) -> ReviewRecord:
        record = self.c.store.get_audit(body.request_id)
        if not record:
            raise ApiError(404, "No stored recommendation with that request id")
        resp = record["response"]
        ranked = [c["ingredient"] for c in resp.get("candidates", [])]
        known = set(ranked) | {c["ingredient"] for c in resp.get("not_recommended", [])}
        if body.candidate_ingredient not in known:
            raise ApiError(422, f"'{body.candidate_ingredient}' is not an option in recommendation {body.request_id}")
        rank = ranked.index(body.candidate_ingredient) + 1 if body.candidate_ingredient in ranked else None
        rec = ReviewRecord(**body.model_dump(), review_id="rv_" + uuid.uuid4().hex[:16],
                           created_at=datetime.now(UTC), rank=rank)
        self.c.store.record_review(rec)
        REVIEWS.labels(decision=rec.decision.value).inc()
        return rec

    def reviews(self, request_id: str | None = None, limit: int = 200) -> list[ReviewRecord]:
        return self.c.store.reviews(request_id, max(1, min(limit, 1000)))

    def review_summary(self) -> dict[str, Any]:
        return review_summary(self.c.store.reviews(None, 100_000))

    # Core --------------------------------------------------------------------------------------

    def recommend(self, req: SubstitutionRequest) -> Recommendation:
        return self.service.recommend(req)

    def batch(self, body: BatchRequest) -> BatchResponse:
        labels = body.labels or []
        results = [BatchItem(label=labels[i] if i < len(labels) else None, recommendation=self.service.recommend(r))
                   for i, r in enumerate(body.requests)]
        recs = [x.recommendation for x in results]
        top: Counter[str] = Counter(r.candidates[0].display_name for r in recs if r.candidates)
        summary = {
            "requests": len(recs),
            "by_status": dict(Counter(r.status.value for r in recs)),
            "in_shortage": sum(1 for r in recs if r.shortage and r.shortage.in_shortage),
            "escalate": [{"label": x.label, "drug": x.recommendation.query.display_name if x.recommendation.query
                          else None, "request_id": x.recommendation.request_id, "reasons": x.recommendation.abstain_reasons}
                         for x in results if x.recommendation.status.value == "abstain"],
            "top_options": [{"option": k, "requests": v} for k, v in top.most_common(10)],
            "blocked_somewhere": sorted({c.display_name for r in recs for c in r.not_recommended}),
            "latency_ms_median": round(statistics.median([r.latency_ms for r in recs]), 1) if recs else 0,
        }
        return BatchResponse(results=results, summary=summary)

    def fhir_substitution(self, payload: dict[str, Any], order_id: str | None = None) -> FhirSubstitutionResponse:
        try:
            mapping = map_fhir(payload, order_id=order_id)
        except FhirMappingError as exc:
            raise ApiError(422, str(exc)) from exc
        assert mapping.request is not None
        rec = self.service.recommend(mapping.request)
        return FhirSubstitutionResponse(mapping=mapping.as_dict(), recommendation=rec)

    def cds_discovery(self) -> dict[str, Any]:
        return cds_hooks.discovery()

    def cds_hook(self, service_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return cds_hooks.handle_hook(service_id, body, self.service, self.c.settings.public_app_url,
                                         self.c.settings.cds_always_card)
        except KeyError as exc:
            raise ApiError(404, f"Unknown CDS service '{service_id}'") from exc
