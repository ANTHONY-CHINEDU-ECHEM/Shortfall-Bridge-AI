"""Substitution service: the orchestration of one recommendation request.

Pipeline (each stage is timed and exported as a Prometheus histogram):

  1 resolve      free text drug name to an indexed ingredient
  2 shortage     current FDA shortage status for that ingredient and presentation
  3 candidates   four tier candidate generation
  4 gates        eight safety gates per candidate over whole label sections
  5 dose         dose equivalence where a published table relates the two drugs
  6 evidence     hybrid retrieval of supporting passages per candidate
  7 rank         transparent scoring; blocked options separated
  8 narrate      LLM narration under the grounding verifier, or template
  9 audit        immutable record of request, response and data versions
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..domain.enums import CandidateTier, GateStatus, RecommendationStatus, ShortageStatus
from ..domain.models import (
    Candidate,
    Chunk,
    Citation,
    DoseConversion,
    DrugConcept,
    Narrative,
    NarrativeSentence,
    Recommendation,
    ShortageRecord,
    ShortageSummary,
    SubstitutionRequest,
)
from ..domain.normalize import route_family
from ..embeddings import Embedder
from ..generation.composer import NarrativeComposer
from ..generation.evidence_pack import build_pack
from ..observability import GATE_OUTCOMES, NARRATIVE, NARRATIVE_ACCEPTANCE, RECOMMENDATIONS, STAGE_LATENCY
from ..retrieval.hybrid import HybridRetriever
from ..retrieval.intent import build_query, detect_intents, section_priors
from ..store.base import KnowledgeStore
from .candidates import CandidateGenerator, Presentation, infer_presentation
from .equivalence import convert
from .gates import GateContext, GateEvaluator
from .resolver import DrugResolver
from .scoring import rank


@contextmanager
def _stage(name: str, timings: dict[str, float]):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        timings[name] = round(dt * 1000, 2)
        STAGE_LATENCY.labels(stage=name).observe(dt)


def summarise_shortage(records: list[ShortageRecord], route: str | None) -> ShortageSummary:
    current = [r for r in records if r.status is ShortageStatus.CURRENT]
    fam = route_family(route)
    if fam:
        scoped = [r for r in current if not r.routes or fam in {route_family(x) for x in r.routes}]
        current = scoped or current
    status = ShortageStatus.CURRENT if current else (
        records[0].status if records else ShortageStatus.UNKNOWN
    )
    dates = [r.update_date for r in records if r.update_date]
    return ShortageSummary(
        in_shortage=bool(current),
        status=status,
        matching_records=len(current),
        presentations=sorted({r.presentation for r in current if r.presentation})[:12],
        reasons=sorted({r.shortage_reason for r in current if r.shortage_reason})[:5],
        availability_notes=sorted({r.availability for r in current if r.availability})[:5],
        last_updated=max(dates) if dates else None,
    )


class SubstitutionService:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder,
        retriever: HybridRetriever,
        resolver: DrugResolver,
        composer: NarrativeComposer,
        settings: Settings,
    ) -> None:
        self.store, self.embedder, self.retriever = store, embedder, retriever
        self.resolver, self.composer, self.settings = resolver, composer, settings
        self.generator = CandidateGenerator(
            store, embedder, settings.max_class_members, settings.effective_alt_class_min_similarity
        )

    def recommend(self, req: SubstitutionRequest, audit: bool = True) -> Recommendation:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        request_id = "r_" + uuid.uuid4().hex[:20]

        with _stage("resolve", timings):
            concept = self.resolver.resolve(req.drug, req.rxcui)
        if concept is None:
            return self._abstain(
                request_id, started, None, None,
                [f"'{req.drug}' could not be matched to an indexed ingredient. Check spelling or use the generic name."],
                req, audit,
            )

        ing = concept.ingredients[0]
        with _stage("shortage", timings):
            records = self.store.shortages_for_ingredient(ing)
            label_routes = [r for d in self.store.labels_for_ingredient(ing, limit=30) for r in d.routes]
            presentation, inferred_note = infer_presentation(
                Presentation(req.route, req.dosage_form, req.strength), records, label_routes
            )
            shortage = summarise_shortage(records, presentation.route)

        with _stage("candidates", timings):
            cset = self.generator.generate(
                concept, presentation, records, req.include_alternative_classes, req.max_candidates
            )

        evaluator = GateEvaluator(GateContext(self.store, req.patient, presentation.route))
        with _stage("gates", timings):
            for c in cset.candidates:
                c.gates = evaluator.evaluate(c.ingredient)
                for g in c.gates:
                    GATE_OUTCOMES.labels(gate=g.gate.value, status=g.status.value).inc()

        with _stage("dose", timings):
            for c in cset.candidates:
                c.dose_conversion = self._dose(c, ing, req, presentation)

        intents = detect_intents(req.clinical_question, req.patient)
        priors = section_priors(intents)
        with _stage("evidence", timings):
            for c in cset.candidates[: req.max_candidates * 2]:
                if c.tier is CandidateTier.ALTERNATIVE_CLASS and c.citations:
                    continue
                query = build_query(c.ingredient, intents, req.patient, req.clinical_question)
                hits = self.retriever.retrieve(query, k=4, ingredients=[c.ingredient], priors=priors)
                gate_cites = [cid for g in c.gates for cid in g.citations]
                c.citations = list(dict.fromkeys(gate_cites + c.citations + [h.chunk.chunk_id for h in hits]))[:10]

        with _stage("rank", timings):
            allowed, blocked = rank(cset.candidates)
            allowed = allowed[: req.max_candidates]

        cited_ids = {cid for c in allowed + blocked for cid in c.citations}
        cited_ids |= {cid for c in allowed + blocked for g in c.gates for cid in g.citations}
        chunks = {ch.chunk_id: ch for ch in self.store.chunks(sorted(cited_ids))}

        with _stage("narrate", timings):
            pack = build_pack(concept, shortage, allowed, blocked, chunks,
                              req.patient.current_medications + req.patient.allergies)
            narrative = self.composer.compose(pack, concept.display_name, req.clinical_question)
            if inferred_note:
                narrative.sentences.insert(0, NarrativeSentence(text=inferred_note, engine_facts=[inferred_note]))
            NARRATIVE.labels(generator=narrative.generator.split(":")[0],
                             fallback=str("fallback_reason" in narrative.verification).lower()).inc()
            if "acceptance_rate" in narrative.verification:
                NARRATIVE_ACCEPTANCE.observe(float(narrative.verification["acceptance_rate"]))

        abstain: list[str] = []
        if not allowed:
            status = RecommendationStatus.ABSTAIN
            abstain.append(
                "No candidate passed the safety gates. Escalate to a specialist pharmacist; do not substitute "
                "on the basis of this output."
                if blocked else "No substitution candidates were found in the indexed knowledge base."
            )
        elif all(c.worst_gate in (GateStatus.CAUTION, GateStatus.UNKNOWN) for c in allowed):
            status = RecommendationStatus.PARTIAL
        else:
            status = RecommendationStatus.OK
        abstain.extend(cset.notes if status is RecommendationStatus.ABSTAIN else [])

        rec = Recommendation(
            request_id=request_id,
            created_at=datetime.now(UTC),
            status=status,
            query=concept,
            shortage=shortage,
            candidates=allowed,
            not_recommended=blocked,
            narrative=narrative,
            citations={cid: self._citation(ch) for cid, ch in chunks.items()},
            abstain_reasons=abstain,
            data_versions={**self.store.data_versions(), "timings_ms": str(timings)},
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        RECOMMENDATIONS.labels(status=status.value).inc()
        if audit:
            self._audit(req, rec)
        return rec

    # Helpers ----------------------------------------------------------------------------------

    def _dose(self, c: Candidate, ing: str, req: SubstitutionRequest, p: Presentation):
        target_route = p.route
        if c.tier is CandidateTier.ALTERNATIVE_FORMULATION:
            # display name ends with "(<route family>)"
            fam = c.display_name.rsplit("(", 1)[-1].rstrip(")")
            target_route = "oral" if fam == "oral" else ("intravenous" if fam == "parenteral" else fam)
        if c.tier is CandidateTier.THERAPEUTIC_EQUIVALENT:
            if req.current_dose:
                return DoseConversion(
                    feasible=True, from_drug=ing, to_drug=c.ingredient,
                    from_dose=f"{req.current_dose.amount:g} {req.current_dose.unit}",
                    suggested_dose=f"{req.current_dose.amount:g} {req.current_dose.unit}"
                    + (f" {req.current_dose.frequency}" if req.current_dose.frequency else ""),
                    method="identity:therapeutic_equivalent",
                    reference="FDA Orange Book therapeutic equivalence (A rated products are substitutable at the same dose).",
                )
            return None
        return convert(ing, c.ingredient, req.current_dose, p.route, target_route)

    @staticmethod
    def _citation(ch: Chunk) -> Citation:
        return Citation(
            chunk_id=ch.chunk_id, set_id=ch.set_id, section=ch.section, title=ch.title,
            snippet=ch.text[:1500], effective_time=ch.effective_time,
            dailymed_url=f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={ch.set_id}",
        )

    def _abstain(
        self, request_id: str, started: float, concept: DrugConcept | None, shortage: ShortageSummary | None,
        reasons: list[str], req: SubstitutionRequest, audit: bool,
    ) -> Recommendation:
        rec = Recommendation(
            request_id=request_id, created_at=datetime.now(UTC), status=RecommendationStatus.ABSTAIN,
            query=concept, shortage=shortage, candidates=[], not_recommended=[],
            narrative=Narrative(sentences=[NarrativeSentence(text=r, engine_facts=[r]) for r in reasons],
                                generator="template"),
            citations={}, abstain_reasons=reasons, data_versions=self.store.data_versions(),
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        RECOMMENDATIONS.labels(status="abstain").inc()
        if audit:
            self._audit(req, rec)
        return rec

    def _audit(self, req: SubstitutionRequest, rec: Recommendation) -> None:
        payload: dict[str, Any] = rec.model_dump(mode="json")
        self.store.record_audit(rec.request_id, req.model_dump(mode="json"), payload)

