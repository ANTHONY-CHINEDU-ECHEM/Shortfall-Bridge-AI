"""Safety gates evaluated for every substitution candidate.

Each gate is a pure function of (candidate, patient, knowledge) returning PASS, CAUTION, BLOCK
or UNKNOWN with a rationale and the chunk ids that justify it. Gates read whole label sections
through ``KnowledgeStore.section_chunks`` instead of top k retrieval, so recall does not depend
on ranking. UNKNOWN is a first class outcome: missing patient data is surfaced, never assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.enums import GateName, GateStatus, HepaticImpairment, SectionType, ShortageStatus
from ..domain.models import Chunk, DrugClass, GateResult, PatientContext
from ..domain.normalize import active_moiety, route_family, word_boundary_contains
from ..store.base import KnowledgeStore
from .label_rules import (
    Action,
    class_mention_terms,
    extract_hepatic_rules,
    extract_pregnancy_findings,
    extract_renal_rules,
    find_interactions,
    hepatic_rank,
    paediatric_unestablished,
)

SAFETY_SECTIONS = (
    SectionType.BOXED_WARNING, SectionType.CONTRAINDICATIONS,
    SectionType.WARNINGS_AND_CAUTIONS, SectionType.WARNINGS, SectionType.PRECAUTIONS,
)
RENAL_SECTIONS = (
    SectionType.CONTRAINDICATIONS, SectionType.DOSAGE_AND_ADMINISTRATION,
    SectionType.SPECIFIC_POPULATIONS, SectionType.WARNINGS_AND_CAUTIONS, SectionType.WARNINGS,
    SectionType.PRECAUTIONS,
)
INTERACTION_SECTIONS = (
    SectionType.DRUG_INTERACTIONS, SectionType.CONTRAINDICATIONS,
    SectionType.BOXED_WARNING, SectionType.WARNINGS_AND_CAUTIONS,
)
PREGNANCY_SECTIONS = (
    SectionType.BOXED_WARNING, SectionType.CONTRAINDICATIONS, SectionType.PREGNANCY,
    SectionType.LACTATION, SectionType.SPECIFIC_POPULATIONS, SectionType.WARNINGS_AND_CAUTIONS,
)

# Clinically recognised cross sensitivity between beta lactam classes (low but real).
CROSS_REACTIVE = {
    "penicillin": ("cephalosporin", "carbapenem"),
    "cephalosporin": ("penicillin", "carbapenem"),
    "carbapenem": ("penicillin", "cephalosporin"),
}


def _trim(s: str, n: int = 220) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


@dataclass
class GateContext:
    store: KnowledgeStore
    patient: PatientContext
    requested_route: str | None


class GateEvaluator:
    def __init__(self, ctx: GateContext) -> None:
        self.ctx = ctx
        self._cache: dict[tuple[str, tuple[SectionType, ...]], list[Chunk]] = {}
        self._class_cache: dict[str, list[DrugClass]] = {}

    def _sections(self, ingredient: str, sections: tuple[SectionType, ...]) -> list[Chunk]:
        key = (ingredient, sections)
        if key not in self._cache:
            self._cache[key] = self.ctx.store.section_chunks(ingredient, sections, limit=80)
        return self._cache[key]

    def _classes(self, ingredient: str) -> list[DrugClass]:
        if ingredient not in self._class_cache:
            self._class_cache[ingredient] = self.ctx.store.classes_for(ingredient)
        return self._class_cache[ingredient]

    def evaluate(self, ingredient: str, routes: list[str] | None = None) -> list[GateResult]:
        ing = active_moiety(ingredient)
        return [
            self.availability(ing, routes or []),
            self.allergy(ing),
            self.renal(ing),
            self.hepatic(ing),
            self.pregnancy(ing),
            self.interactions(ing),
            self.boxed_warning(ing),
            self.age(ing),
        ]

    # Individual gates -------------------------------------------------------------------------

    def availability(self, ing: str, routes: list[str]) -> GateResult:
        records = [r for r in self.ctx.store.shortages_for_ingredient(ing) if r.status is ShortageStatus.CURRENT]
        if not records:
            return GateResult(gate=GateName.AVAILABILITY, status=GateStatus.PASS,
                              rationale="No current FDA shortage listed for this ingredient.")
        fam = route_family(self.ctx.requested_route) if self.ctx.requested_route else None
        same_route = [r for r in records if not fam or not r.routes or fam in {route_family(x) for x in r.routes}]
        names = sorted({r.presentation or r.generic_name for r in same_route or records})[:4]
        return GateResult(
            gate=GateName.AVAILABILITY, status=GateStatus.CAUTION,
            rationale=(
                f"{len(records)} current FDA shortage entr{'y' if len(records) == 1 else 'ies'} for this ingredient"
                f"{' in the same route family' if same_route and fam else ''}, including "
                + "; ".join(names) + ". Confirm stock with suppliers before switching."
            ),
        )

    def allergy(self, ing: str) -> GateResult:
        allergies = self.ctx.patient.allergies
        if not allergies:
            return GateResult(gate=GateName.ALLERGY, status=GateStatus.PASS, rationale="No allergies recorded.")
        cand_classes = [c.class_name.lower() for c in self._classes(ing)]
        contra = self._sections(ing, (SectionType.CONTRAINDICATIONS,))
        worst, reasons, cites = GateStatus.PASS, [], []
        for allergen in allergies:
            a = active_moiety(allergen)
            if a == ing:
                return GateResult(gate=GateName.ALLERGY, status=GateStatus.BLOCK,
                                  rationale=f"Recorded allergy to {allergen}, which is this ingredient.")
            # allergen expressed as a class ("penicillins", "sulfa")
            stem = re.sub(r"s$", "", a.replace("sulfa", "sulfonamide antibacterial"))
            if len(stem) >= 4 and any(stem in cn for cn in cand_classes):
                return GateResult(gate=GateName.ALLERGY, status=GateStatus.BLOCK,
                                  rationale=f"Recorded allergy to {allergen}; this drug belongs to that class.")
            allergen_classes = [c.class_name.lower() for c in self._classes(a)]
            shared = sorted({c for c in allergen_classes if c in cand_classes})
            if shared:
                worst = max(worst, GateStatus.CAUTION, key=lambda s: s.severity)
                reasons.append(f"shares class '{shared[0]}' with allergen {allergen}; cross sensitivity is possible")
            for group, partners in CROSS_REACTIVE.items():
                if any(group in c for c in allergen_classes + [a]) and any(p in c for c in cand_classes for p in partners):
                    worst = max(worst, GateStatus.CAUTION, key=lambda s: s.severity)
                    reasons.append(f"recognised beta lactam cross reactivity with {allergen}")
            for ch in contra:
                if word_boundary_contains(ch.text, a) or word_boundary_contains(ch.text, allergen):
                    worst = GateStatus.BLOCK
                    reasons.append(f"label contraindicates use with hypersensitivity involving {allergen}")
                    cites.append(ch.chunk_id)
        if worst is GateStatus.PASS:
            return GateResult(gate=GateName.ALLERGY, status=GateStatus.PASS,
                              rationale="No match between recorded allergies and this drug or its classes.")
        return GateResult(gate=GateName.ALLERGY, status=worst, rationale=_trim("; ".join(reasons).capitalize() + "."),
                          citations=cites[:3])

    def renal(self, ing: str) -> GateResult:
        rules = extract_renal_rules(self._sections(ing, RENAL_SECTIONS))
        fn = self.ctx.patient.renal_function
        if not rules:
            return GateResult(gate=GateName.RENAL, status=GateStatus.PASS,
                              rationale="No renal restriction found in the indexed label sections.")
        if fn is None:
            strictest = max(rules, key=lambda r: (r.action, r.threshold))
            return GateResult(
                gate=GateName.RENAL, status=GateStatus.UNKNOWN,
                rationale=f"Label contains renal dosing rules (for example below {strictest.threshold:g} mL/min). "
                "Enter creatinine clearance to evaluate.",
                citations=[strictest.chunk_id],
            )
        applicable = [r for r in rules if fn < r.threshold]
        if not applicable:
            return GateResult(gate=GateName.RENAL, status=GateStatus.PASS,
                              rationale=f"Renal function {fn:g} mL/min is above every labelled threshold.",
                              citations=[rules[0].chunk_id])
        top = max(applicable, key=lambda r: (r.action, -r.threshold))
        status = GateStatus.BLOCK if top.action is Action.CONTRAINDICATED else GateStatus.CAUTION
        verb = {Action.CONTRAINDICATED: "Contraindicated", Action.AVOID: "Not recommended",
                Action.ADJUST: "Dose adjustment required", Action.MONITOR: "Monitoring advised"}[top.action]
        return GateResult(
            gate=GateName.RENAL, status=status,
            rationale=f"{verb} at renal function below {top.threshold:g} mL/min (patient {fn:g}). "
            f"Label: “{_trim(top.sentence, 180)}”",
            citations=sorted({r.chunk_id for r in applicable})[:3],
        )

    def hepatic(self, ing: str) -> GateResult:
        level = self.ctx.patient.hepatic_impairment
        rules = extract_hepatic_rules(self._sections(ing, RENAL_SECTIONS))
        if level is HepaticImpairment.NONE:
            return GateResult(gate=GateName.HEPATIC, status=GateStatus.PASS,
                              rationale="No hepatic impairment recorded.",
                              citations=[rules[0].chunk_id] if rules else [])
        applicable = [r for r in rules if hepatic_rank(level) >= hepatic_rank(r.min_severity)]
        if not applicable:
            return GateResult(gate=GateName.HEPATIC, status=GateStatus.PASS,
                              rationale=f"No labelled restriction applies at {level.value} hepatic impairment.")
        top = max(applicable, key=lambda r: r.action)
        status = GateStatus.BLOCK if top.action is Action.CONTRAINDICATED else GateStatus.CAUTION
        return GateResult(
            gate=GateName.HEPATIC, status=status,
            rationale=f"{level.value.capitalize()} hepatic impairment: “{_trim(top.sentence, 200)}”",
            citations=sorted({r.chunk_id for r in applicable})[:3],
        )

    def pregnancy(self, ing: str) -> GateResult:
        p = self.ctx.patient
        if not (p.pregnant or p.breastfeeding):
            return GateResult(gate=GateName.PREGNANCY_LACTATION, status=GateStatus.PASS,
                              rationale="Not pregnant or breastfeeding per the entered context.")
        findings = extract_pregnancy_findings(self._sections(ing, PREGNANCY_SECTIONS))
        if p.pregnant:
            contra = [f for f in findings if f.kind == "contraindicated"]
            if contra:
                return GateResult(gate=GateName.PREGNANCY_LACTATION, status=GateStatus.BLOCK,
                                  rationale=f"Contraindicated in pregnancy: “{_trim(contra[0].sentence)}”",
                                  citations=[f.chunk_id for f in contra[:3]])
            harm = [f for f in findings if f.kind == "fetal_harm"]
            if harm:
                return GateResult(gate=GateName.PREGNANCY_LACTATION, status=GateStatus.CAUTION,
                                  rationale=f"Fetal risk described: “{_trim(harm[0].sentence)}”",
                                  citations=[f.chunk_id for f in harm[:3]])
        if p.breastfeeding:
            lact = [f for f in findings if f.kind == "lactation_avoid"]
            if lact:
                return GateResult(gate=GateName.PREGNANCY_LACTATION, status=GateStatus.CAUTION,
                                  rationale=f"Lactation: “{_trim(lact[0].sentence)}”",
                                  citations=[f.chunk_id for f in lact[:2]])
        return GateResult(gate=GateName.PREGNANCY_LACTATION, status=GateStatus.PASS,
                          rationale="No pregnancy contraindication or lactation stop instruction found in the label.")

    def interactions(self, ing: str) -> GateResult:
        meds = self.ctx.patient.current_medications
        if not meds:
            return GateResult(gate=GateName.INTERACTIONS, status=GateStatus.PASS,
                              rationale="No current medications entered.")
        cand_chunks = self._sections(ing, INTERACTION_SECTIONS)
        cand_terms = [ing] + class_mention_terms([c.class_name for c in self._classes(ing)])
        hits = []
        for med in meds:
            m = active_moiety(med)
            if m == ing:
                hits.append((Action.AVOID, f"{med} is the same ingredient (duplicate therapy)", None))
                continue
            terms = [m] + class_mention_terms([c.class_name for c in self._classes(m)])
            forward = find_interactions(cand_chunks, med, terms)
            reverse = find_interactions(self._sections(m, INTERACTION_SECTIONS), med, cand_terms)
            for h in (forward[:1] + reverse[:1]):
                hits.append((h.action, f"{med}: “{_trim(h.sentence, 170)}”", h.chunk_id))
        if not hits:
            return GateResult(gate=GateName.INTERACTIONS, status=GateStatus.PASS,
                              rationale=f"No labelled interaction found with {', '.join(meds)}.")
        hits.sort(key=lambda x: -x[0])
        status = GateStatus.BLOCK if hits[0][0] is Action.CONTRAINDICATED else GateStatus.CAUTION
        return GateResult(
            gate=GateName.INTERACTIONS, status=status,
            rationale=" ".join(h[1] for h in hits[:3]),
            citations=[h[2] for h in hits if h[2]][:4],
        )

    def boxed_warning(self, ing: str) -> GateResult:
        boxed = self._sections(ing, (SectionType.BOXED_WARNING,))
        if not boxed:
            return GateResult(gate=GateName.BOXED_WARNING, status=GateStatus.PASS, rationale="No boxed warning.")
        return GateResult(gate=GateName.BOXED_WARNING, status=GateStatus.CAUTION,
                          rationale=f"Boxed warning: {_trim(boxed[0].text, 220)}",
                          citations=[c.chunk_id for c in boxed[:2]])

    def age(self, ing: str) -> GateResult:
        age = self.ctx.patient.age_years
        if age is None:
            return GateResult(gate=GateName.AGE, status=GateStatus.PASS, rationale="Age not entered.")
        if age < 18:
            hit = paediatric_unestablished(self._sections(ing, (SectionType.PEDIATRIC_USE,)))
            if hit:
                return GateResult(gate=GateName.AGE, status=GateStatus.CAUTION,
                                  rationale=f"Paediatric use: “{_trim(hit[0])}”", citations=[hit[1]])
            return GateResult(gate=GateName.AGE, status=GateStatus.PASS,
                              rationale="No paediatric limitation found in the label.")
        if age >= 65:
            geri = self._sections(ing, (SectionType.GERIATRIC_USE,))
            strong = [c for c in geri if re.search(r"not recommended|avoid|contraindicat", c.text, re.I)]
            if strong:
                return GateResult(gate=GateName.AGE, status=GateStatus.CAUTION,
                                  rationale=f"Geriatric use: {_trim(strong[0].text)}", citations=[strong[0].chunk_id])
        return GateResult(gate=GateName.AGE, status=GateStatus.PASS, rationale="No age specific restriction found.")
