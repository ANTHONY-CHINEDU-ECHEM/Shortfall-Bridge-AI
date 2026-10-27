"""Substitution service: the orchestration of one recommendation request.

Pipeline (each stage is timed and exported as a Prometheus histogram):

  0 prefetch     live stores only: fetch the data this request needs in parallel batches
  1 resolve      free text drug name to an indexed ingredient
    renal        estimate renal function (Cockcroft-Gault, CKD-EPI 2021) when not entered
  2 shortage     current FDA shortage status for that ingredient and presentation
  3 candidates   four tier candidate generation
  4 gates        eight safety gates per candidate over whole label sections
  5 dose         dose equivalence where a published table relates the two drugs, daily total and
                 labelled maximum daily dose ceiling
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

from .. import __version__
from ..clinical.dosing import doses_per_day, extract_max_daily_doses, fmt_mg, parse_dose_mg
from ..clinical.renal import estimate_renal_function, with_estimated_renal_function
from ..config import Settings
from ..domain.enums import CandidateTier, GateStatus, RecommendationStatus, SectionType, ShortageStatus
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
        warnings: list[str] = []
        live_errors = getattr(self.store, "errors", None)
        errors_before = len(live_errors) if isinstance(live_errors, list) else 0

        prefetch = getattr(self.store, "prefetch_request", None)
        if callable(prefetch):
            with _stage("prefetch", timings):
                try:
                    prefetch(req)
                except Exception as exc:  # a live source failure degrades to lazy fetching, never a crash
                    warnings.append(f"Live data prefetch was incomplete ({str(exc)[:120]}); results may be partial.")

        with _stage("resolve", timings):
            concept = self.resolver.resolve(req.drug, req.rxcui)
        if concept is None:
            return self._abstain(
                request_id, started, None, None,
                [f"'{req.drug}' could not be matched to an indexed ingredient. Check spelling or use the generic name."],
                req, audit, timings, warnings,
            )

        with _stage("renal", timings):
            renal = estimate_renal_function(req.patient)
            patient = with_estimated_renal_function(req.patient, renal)
            if renal.source in {"cockcroft_gault", "ckd_epi_2021", "bedside_schwartz"}:
                warnings.append(
                    f"Renal function estimated at {renal.value_ml_min:g} mL/min by {renal.method}"
                    + (f" using {renal.weight_used}" if renal.weight_used else "") + "."
                )
            warnings.extend(renal.warnings)

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

        evaluator = GateEvaluator(GateContext(self.store, patient, presentation.route, replacing=ing))
        with _stage("gates", timings):
            for c in cset.candidates:
                c.gates = evaluator.evaluate(c)
                for g in c.gates:
                    GATE_OUTCOMES.labels(gate=g.gate.value, status=g.status.value).inc()

        with _stage("dose", timings):
            for c in cset.candidates:
                c.dose_conversion = self._dose(c, ing, req, presentation)

        intents = detect_intents(req.clinical_question, patient)
        priors = section_priors(intents)
        with _stage("evidence", timings):
            for c in cset.candidates[: req.max_candidates * 2]:
                if c.tier is CandidateTier.ALTERNATIVE_CLASS and c.citations:
                    continue
                query = build_query(c.ingredient, intents, patient, req.clinical_question)
                hits = self.retriever.retrieve(query, k=4, ingredients=[c.ingredient], priors=priors)
                gate_cites = [cid for g in c.gates for cid in g.citations]
                dose_cites = c.dose_conversion.ceiling_citations if c.dose_conversion else []
                c.citations = list(dict.fromkeys(
                    gate_cites + dose_cites + c.citations + [h.chunk.chunk_id for h in hits]))[:10]

        with _stage("rank", timings):
            allowed, blocked = rank(cset.candidates)
            allowed = allowed[: req.max_candidates]

        cited_ids = {cid for c in allowed + blocked for cid in c.citations}
        cited_ids |= {cid for c in allowed + blocked for g in c.gates for cid in g.citations}
        chunks = {ch.chunk_id: ch for ch in self.store.chunks(sorted(cited_ids))}

        with _stage("narrate", timings):
            pack = build_pack(concept, shortage, allowed, blocked, chunks,
                              req.patient.current_medications + req.patient.allergies, renal=renal)
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
            renal=renal,
            warnings=list(dict.fromkeys(warnings + ([inferred_note] if inferred_note else []))),
            timings_ms=timings,
            engine_version=__version__,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        if isinstance(live_errors, list) and len(live_errors) > errors_before:
            failed = sorted({e.split(":", 1)[0] for e in live_errors[errors_before:]})
            rec.warnings.append(
                f"{len(live_errors) - errors_before} live openFDA request(s) failed ({', '.join(failed[:4])}). "
                "Some options or safety findings may be missing; retry before relying on this result."
            )
            if rec.status is RecommendationStatus.OK:
                rec.status = RecommendationStatus.PARTIAL
        RECOMMENDATIONS.labels(status=status.value).inc()
        if audit:
            self._audit(req, rec)
        return rec

    # Helpers ----------------------------------------------------------------------------------

    @staticmethod
    def _target_route(c: Candidate, p: Presentation) -> str | None:
        """The route the alternative would actually be given by."""
        def as_route(fam: str) -> str:
            return {"oral": "oral", "parenteral": "intravenous"}.get(fam, fam)

        if c.tier is CandidateTier.ALTERNATIVE_FORMULATION and c.routes_available:
            return as_route(c.routes_available[0])
        if c.route_match is False and c.routes_available:
            fam = "oral" if "oral" in c.routes_available else c.routes_available[0]
            return as_route(fam)
        return p.route

    def _dose(self, c: Candidate, ing: str, req: SubstitutionRequest, p: Presentation) -> DoseConversion | None:
        target_route = self._target_route(c, p)
        if c.tier is CandidateTier.THERAPEUTIC_EQUIVALENT:
            if not req.current_dose:
                return None
            dc = DoseConversion(
                feasible=True, from_drug=ing, to_drug=c.ingredient,
                from_dose=f"{req.current_dose.amount:g} {req.current_dose.unit}",
                suggested_dose=f"{req.current_dose.amount:g} {req.current_dose.unit}"
                + (f" {req.current_dose.frequency}" if req.current_dose.frequency else ""),
                method="identity:therapeutic_equivalent",
                reference="FDA Orange Book therapeutic equivalence (A rated products are substitutable at the same dose).",
                from_route=p.route, to_route=p.route,
            )
        else:
            dc = convert(ing, c.ingredient, req.current_dose, p.route, target_route)
            if dc is None:
                return None
            dc.from_route, dc.to_route = p.route, target_route
            if (dc.feasible and ing != c.ingredient and p.route and target_route
                    and route_family(p.route) != route_family(target_route)):
                dc.warnings.insert(0, f"Route change from {p.route} to {target_route}: {c.ingredient} is not "
                                      f"indexed by the {route_family(p.route)} route. Confirm the patient can use it.")
        if dc.feasible and req.current_dose:
            self._apply_ceiling(dc, c, req)
        return dc

    def _apply_ceiling(self, dc: DoseConversion, c: Candidate, req: SubstitutionRequest) -> None:
        """Daily total from the frequency and comparison with the labelled maximum daily dose."""
        if req.current_dose is None or req.current_dose.unit.lower() not in {"mg", "g", "mcg"}:
            return
        per_dose = parse_dose_mg(dc.suggested_dose)
        if per_dose is None:
            return
        n = 1.0 if dc.method.endswith(":band") else doses_per_day(req.current_dose.frequency)
        if n is None:
            if req.current_dose.frequency:
                dc.warnings.append(f"Frequency '{req.current_dose.frequency}' has no fixed daily total, so the "
                                   "labelled maximum daily dose could not be checked.")
            return
        dc.doses_per_day = n
        lo, hi = per_dose[0] * n, per_dose[1] * n
        dc.suggested_daily_total = fmt_mg(hi) if abs(hi - lo) < 1e-9 else f"{fmt_mg(lo)} to {fmt_mg(hi)}"
        maxima = extract_max_daily_doses(
            self.store.section_chunks(c.ingredient, (SectionType.DOSAGE_AND_ADMINISTRATION,), limit=40)
        )
        if not maxima:
            return
        highest = maxima[0]
        dc.label_max_daily = f"{fmt_mg(highest.mg_per_day)} per day"
        if hi > highest.mg_per_day + 1e-9:
            dc.ceiling_exceeded = True
            dc.ceiling_citations = [highest.chunk_id]
            dc.warnings.insert(0, f"Suggested daily total ({dc.suggested_daily_total}) exceeds the labelled maximum "
                                  f"daily dose of {fmt_mg(highest.mg_per_day)}. Do not use without specialist review.")
        else:
            lower = [m for m in maxima if hi > m.mg_per_day + 1e-9]
            if lower:
                dc.ceiling_citations = [lower[0].chunk_id]
                dc.warnings.append(f"Daily total ({dc.suggested_daily_total}) is above a maximum of "
                                   f"{fmt_mg(lower[0].mg_per_day)} stated for some uses in the label; check the indication.")

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
        timings: dict[str, float] | None = None, warnings: list[str] | None = None,
    ) -> Recommendation:
        rec = Recommendation(
            request_id=request_id, created_at=datetime.now(UTC), status=RecommendationStatus.ABSTAIN,
            query=concept, shortage=shortage, candidates=[], not_recommended=[],
            narrative=Narrative(sentences=[NarrativeSentence(text=r, engine_facts=[r]) for r in reasons],
                                generator="template"),
            citations={}, abstain_reasons=reasons, data_versions=self.store.data_versions(),
            renal=estimate_renal_function(req.patient), warnings=warnings or [], timings_ms=timings or {},
            engine_version=__version__,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        RECOMMENDATIONS.labels(status="abstain").inc()
        if audit:
            self._audit(req, rec)
        return rec

    def _audit(self, req: SubstitutionRequest, rec: Recommendation) -> None:
        payload: dict[str, Any] = rec.model_dump(mode="json")
        self.store.record_audit(rec.request_id, req.model_dump(mode="json"), payload)

