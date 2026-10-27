"""Safety gates evaluated for every substitution candidate.

Each gate is a pure function of (candidate, patient, knowledge) returning PASS, CAUTION, BLOCK
or UNKNOWN with a rationale and the chunk ids that justify it. Gates read whole label sections
through ``KnowledgeStore.section_chunks`` instead of top k retrieval, so recall does not depend
on ranking. UNKNOWN is a first class outcome: missing patient data is surfaced, never assumed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.enums import CandidateTier, GateName, GateStatus, HepaticImpairment, SectionType, ShortageStatus
from ..domain.models import Candidate, Chunk, DrugClass, GateResult, PatientContext, ShortageRecord
from ..domain.normalize import active_moiety, route_family, text_route_families, word_boundary_contains
from ..store.base import KnowledgeStore
from .equivalence import table_for
from .label_rules import (
    Action,
    class_mention_terms,
    extract_hepatic_rules,
    extract_pregnancy_findings,
    extract_renal_rules,
    find_interactions,
    hepatic_rank,
    paediatric_unestablished,
    sentences,
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

ALLERGY_WARNING_SECTIONS = (
    SectionType.WARNINGS_AND_CAUTIONS, SectionType.WARNINGS, SectionType.PRECAUTIONS,
)

# Allergy list entries that name a class rather than a drug, mapped to the stem used in EPC names.
ALLERGEN_CLASS_TERMS = {
    "sulfa": "sulfonamide", "sulpha": "sulfonamide", "sulfa drugs": "sulfonamide", "sulfonamide": "sulfonamide",
    "sulfonamides": "sulfonamide", "sulphonamides": "sulfonamide", "penicillins": "penicillin",
    "cephalosporins": "cephalosporin", "nsaid": "anti-inflammatory", "nsaids": "anti-inflammatory",
    "opioids": "opioid", "opiates": "opioid", "macrolides": "macrolide", "quinolones": "quinolone",
    "fluoroquinolones": "quinolone", "tetracyclines": "tetracycline", "statins": "hmg-coa reductase",
}
_HYPERSENSITIVITY = re.compile(r"hypersensitiv|allerg|cross.?(?:sensitiv|reactiv)|anaphyla", re.I)

# Supply caution scopes, read by scoring.
SUPPLY_SAME_ROUTE = "same_route"
SUPPLY_MARKET = "market"
SUPPLY_OTHER_ROUTE = "other_route"

# Clinically recognised cross sensitivity between beta lactam classes (low but real).
CROSS_REACTIVE = {
    "penicillin": ("cephalosporin", "carbapenem"),
    "cephalosporin": ("penicillin", "carbapenem"),
    "carbapenem": ("penicillin", "cephalosporin"),
}


def _trim(s: str, n: int = 220) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def shortage_route_families(r: ShortageRecord) -> set[str]:
    """Route families a shortage entry affects: openFDA routes when present, else its wording."""
    fams = {f for f in (route_family(x) for x in r.routes) if f}
    return fams or text_route_families(r.dosage_form, r.presentation, r.generic_name)


def _company_key(name: str | None) -> str:
    return (name or "").lower().replace(",", " ").split(" ")[0] if name else ""


@dataclass
class GateContext:
    store: KnowledgeStore
    patient: PatientContext
    requested_route: str | None
    replacing: str | None = None  # the shortage drug itself: never a duplicate of its own replacement


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

    def evaluate(self, candidate: Candidate | str, routes: list[str] | None = None) -> list[GateResult]:
        """Run all eight gates. Pass the Candidate so supply is scoped to its route and makers."""
        if isinstance(candidate, Candidate):
            ing = active_moiety(candidate.ingredient)
            if candidate.route_match and self.ctx.requested_route:
                fams = {route_family(self.ctx.requested_route) or ""} - {""}
            else:
                fams = set(candidate.routes_available)
            makers = candidate.manufacturers if candidate.tier is CandidateTier.THERAPEUTIC_EQUIVALENT else []
        else:
            ing = active_moiety(candidate)
            fams = {f for f in (route_family(r) for r in routes or []) if f}
            makers = []
        return [
            self.availability(ing, fams, makers),
            self.allergy(ing),
            self.renal(ing),
            self.hepatic(ing),
            self.pregnancy(ing),
            self.interactions(ing),
            self.boxed_warning(ing),
            self.age(ing),
        ]

    # Individual gates -------------------------------------------------------------------------

    def availability(self, ing: str, route_families: set[str] | None = None,
                     manufacturers: list[str] | None = None) -> GateResult:
        """Supply gate, scoped to the candidate's own route families and, for T1, its makers.

        * PASS when no current entry exists, or every current entry affects other routes only.
        * CAUTION (market) when entries exist for this presentation but none name the candidate's
          A rated manufacturers: allocation is plausible but should be confirmed.
        * CAUTION (same route) otherwise.
        """
        records = [r for r in self.ctx.store.shortages_for_ingredient(ing) if r.status is ShortageStatus.CURRENT]
        if not records:
            return GateResult(gate=GateName.AVAILABILITY, status=GateStatus.PASS,
                              rationale="No current FDA shortage listed for this ingredient.",
                              facts={"current_entries": 0})
        fams = {f for f in (route_families or set()) if f}
        if not fams and self.ctx.requested_route:
            fams = {route_family(self.ctx.requested_route) or ""} - {""}
        if fams:
            relevant = [r for r in records if not shortage_route_families(r) or shortage_route_families(r) & fams]
        else:
            relevant = records
        if not relevant:
            other = sorted({f for r in records for f in shortage_route_families(r)})
            return GateResult(
                gate=GateName.AVAILABILITY, status=GateStatus.PASS,
                rationale=(f"{len(records)} current FDA shortage entr{'y' if len(records) == 1 else 'ies'} for this "
                           f"ingredient affect{'s' if len(records) == 1 else ''} the {', '.join(other)} route only; "
                           f"none list the {', '.join(sorted(fams))} route."),
                facts={"supply_scope": SUPPLY_OTHER_ROUTE, "current_entries": len(records), "relevant_entries": 0},
            )
        names = sorted({r.presentation or r.generic_name for r in relevant})[:4]
        affected = sorted({r.company_name for r in relevant if r.company_name})
        affected_keys = {_company_key(c) for c in affected}
        makers = [m for m in (manufacturers or []) if m]
        unaffected_makers = [m for m in makers if _company_key(m) not in affected_keys]
        n = len(relevant)
        if makers and unaffected_makers and len(unaffected_makers) == len(makers):
            named = ", ".join(a.rstrip(".") for a in affected) or "other suppliers"
            return GateResult(
                gate=GateName.AVAILABILITY, status=GateStatus.CAUTION,
                rationale=(f"{n} current FDA shortage entr{'y' if n == 1 else 'ies'} for this presentation "
                           f"list{'s' if n == 1 else ''} {named}. The manufacturers "
                           "proposed here are not named; confirm allocation with your wholesaler before switching."),
                facts={"supply_scope": SUPPLY_MARKET, "current_entries": len(records), "relevant_entries": n,
                       "affected_companies": affected},
            )
        scope = " in the same route family" if fams else ""
        return GateResult(
            gate=GateName.AVAILABILITY, status=GateStatus.CAUTION,
            rationale=(
                f"{n} current FDA shortage entr{'y' if n == 1 else 'ies'} for this ingredient{scope}, including "
                + "; ".join(names) + ". Confirm stock with suppliers before switching."
            ),
            facts={"supply_scope": SUPPLY_SAME_ROUTE, "current_entries": len(records), "relevant_entries": n,
                   "affected_companies": affected},
        )

    def allergy(self, ing: str) -> GateResult:
        allergies = self.ctx.patient.allergies
        if not allergies:
            return GateResult(gate=GateName.ALLERGY, status=GateStatus.PASS, rationale="No allergies recorded.")
        cand_classes = [c.class_name.lower() for c in self._classes(ing)]
        contra = self._sections(ing, (SectionType.CONTRAINDICATIONS,))
        warn_chunks = self._sections(ing, ALLERGY_WARNING_SECTIONS)
        worst, reasons, cites = GateStatus.PASS, [], []
        matched: list[str] = []
        for allergen in allergies:
            raw = " ".join(allergen.lower().split())
            a = active_moiety(allergen)
            if a == ing:
                return GateResult(gate=GateName.ALLERGY, status=GateStatus.BLOCK,
                                  rationale=f"Recorded allergy to {allergen}, which is this ingredient.",
                                  facts={"matched_allergens": [allergen], "match": "ingredient"})
            # An allergen written as a class ("penicillins", "sulfa") is compared with the drug's classes.
            stem = ALLERGEN_CLASS_TERMS.get(raw) or ALLERGEN_CLASS_TERMS.get(a) or re.sub(r"s$", "", a)
            if len(stem) >= 4 and any(stem in cn for cn in cand_classes):
                return GateResult(gate=GateName.ALLERGY, status=GateStatus.BLOCK,
                                  rationale=f"Recorded allergy to {allergen}; this drug belongs to that class.",
                                  facts={"matched_allergens": [allergen], "match": "class"})
            allergen_classes = [c.class_name.lower() for c in self._classes(a)]
            shared = sorted({c for c in allergen_classes if c in cand_classes})
            if shared:
                worst = max(worst, GateStatus.CAUTION, key=lambda s: s.severity)
                reasons.append(f"shares class '{shared[0]}' with allergen {allergen}; cross sensitivity is possible")
                matched.append(allergen)
            for group, partners in CROSS_REACTIVE.items():
                if any(group in c for c in [*allergen_classes, a, stem]) and \
                        any(p in c for c in cand_classes for p in partners):
                    worst = max(worst, GateStatus.CAUTION, key=lambda s: s.severity)
                    reasons.append(f"recognised beta lactam cross reactivity with {allergen}")
                    matched.append(allergen)
            terms = {t for t in (a, raw, stem) if len(t) >= 4}
            for ch in contra:
                if any(word_boundary_contains(ch.text, t, plural=True) for t in terms):
                    worst = GateStatus.BLOCK
                    reasons.append(f"label contraindicates use with hypersensitivity involving {allergen}")
                    cites.append(ch.chunk_id)
                    matched.append(allergen)
                    break
            else:
                # Warnings sections often carry cross sensitivity statements, e.g. "patients allergic to
                # sulfonamides may show hypersensitivity to bumetanide".
                for ch in warn_chunks:
                    hit = next((sent for sent in sentences(ch) if _HYPERSENSITIVITY.search(sent)
                                and any(word_boundary_contains(sent, t, plural=True) for t in terms)), None)
                    if hit:
                        worst = max(worst, GateStatus.CAUTION, key=lambda s: s.severity)
                        reasons.append(f"label warns of hypersensitivity with {allergen}: “{_trim(hit, 150)}”")
                        cites.append(ch.chunk_id)
                        matched.append(allergen)
                        break
        if worst is GateStatus.PASS:
            return GateResult(gate=GateName.ALLERGY, status=GateStatus.PASS,
                              rationale="No match between recorded allergies and this drug or its classes.")
        return GateResult(gate=GateName.ALLERGY, status=worst, rationale=_trim("; ".join(reasons).capitalize() + ".", 320),
                          citations=list(dict.fromkeys(cites))[:3],
                          facts={"matched_allergens": sorted(set(matched))})

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
        thresholds = sorted({r.threshold for r in rules})
        if not applicable:
            return GateResult(gate=GateName.RENAL, status=GateStatus.PASS,
                              rationale=f"Renal function {fn:g} mL/min is above every labelled threshold.",
                              citations=[rules[0].chunk_id],
                              facts={"renal_function": fn, "thresholds": thresholds})
        top = max(applicable, key=lambda r: (r.action, -r.threshold))
        status = GateStatus.BLOCK if top.action is Action.CONTRAINDICATED else GateStatus.CAUTION
        verb = {Action.CONTRAINDICATED: "Contraindicated", Action.AVOID: "Not recommended",
                Action.ADJUST: "Dose adjustment required", Action.MONITOR: "Monitoring advised"}[top.action]
        return GateResult(
            gate=GateName.RENAL, status=status,
            rationale=f"{verb} at renal function below {top.threshold:g} mL/min (patient {fn:g}). "
            f"Label: “{_trim(top.sentence, 180)}”",
            citations=sorted({r.chunk_id for r in applicable})[:3],
            facts={"renal_function": fn, "thresholds": thresholds, "applied_threshold": top.threshold,
                   "action": top.action.name.lower()},
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
        replacing = active_moiety(self.ctx.replacing) if self.ctx.replacing else None
        meds = [m for m in self.ctx.patient.current_medications if active_moiety(m) != replacing]
        skipped = [m for m in self.ctx.patient.current_medications if active_moiety(m) == replacing]
        if not meds:
            note = (f" {skipped[0]} is the drug being replaced, so it is not checked against its alternatives."
                    if skipped else "")
            return GateResult(gate=GateName.INTERACTIONS, status=GateStatus.PASS,
                              rationale=("No other current medications entered." if skipped else
                                         "No current medications entered.") + note)
        cand_chunks = self._sections(ing, INTERACTION_SECTIONS)
        cand_class_objs = self._classes(ing)
        cand_terms = [ing] + class_mention_terms([c.class_name for c in cand_class_objs])
        cand_epc = {c.class_name.lower() for c in cand_class_objs if c.class_type.upper() == "EPC"}
        hits = []
        duplicates: list[str] = []
        for med in meds:
            m = active_moiety(med)
            if m == ing:
                hits.append((Action.AVOID, f"{med} is the same ingredient (duplicate therapy)", None))
                duplicates.append(med)
                continue
            med_classes = self._classes(m)
            shared_epc = sorted(cand_epc & {c.class_name.lower() for c in med_classes if c.class_type.upper() == "EPC"})
            table = table_for(m, ing)
            if shared_epc or table is not None:
                why = shared_epc[0] if shared_epc else table.title.split(",")[0].lower()  # type: ignore[union-attr]
                hits.append((Action.AVOID, f"{med} is in the same class ({why}): duplicate therapy unless "
                             f"{med} is being stopped", None))
                duplicates.append(med)
            terms = [m] + class_mention_terms([c.class_name for c in med_classes])
            forward = find_interactions(cand_chunks, med, terms)
            reverse = find_interactions(self._sections(m, INTERACTION_SECTIONS), med, cand_terms)
            for h in (forward[:1] + reverse[:1]):
                hits.append((h.action, f"{med}: “{_trim(h.sentence, 170)}”", h.chunk_id))
        hits_note = f" {skipped[0]} is the drug being replaced and was not checked." if skipped else ""
        if not hits:
            return GateResult(gate=GateName.INTERACTIONS, status=GateStatus.PASS,
                              rationale=f"No labelled interaction found with {', '.join(meds)}.{hits_note}")
        hits.sort(key=lambda x: -x[0])
        status = GateStatus.BLOCK if hits[0][0] is Action.CONTRAINDICATED else GateStatus.CAUTION
        return GateResult(
            gate=GateName.INTERACTIONS, status=status,
            rationale=" ".join(h[1] for h in hits[:3]) + hits_note,
            citations=[h[2] for h in hits if h[2]][:4],
            facts={"duplicate_therapy": duplicates, "checked_medications": meds,
                   "strongest_action": hits[0][0].name.lower()},
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
