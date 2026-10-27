"""Candidate generation across the four substitution tiers.

T1  Therapeutic equivalent: same ingredient, dosage form, route and strength from another
    manufacturer, A rated in the Orange Book, and not that manufacturer's presentation in shortage.
T2  Alternative formulation: same ingredient by another dosage form or route.
T3  Therapeutic interchange: different ingredient sharing an EPC or ATC level 4 class, or
    sharing a published dose equivalence table.
T4  Alternative class: different class whose labelled indications are semantically close to
    the shortage drug's indications. Always flagged for specialist review.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.enums import CandidateTier, SectionType, ShortageStatus
from ..domain.models import Candidate, DrugConcept, OrangeBookProduct, ShortageRecord
from ..domain.normalize import (
    active_moiety,
    normalize_dosage_form,
    route_family,
    same_strength,
)
from ..embeddings import Embedder
from ..store.base import KnowledgeStore
from .equivalence import interchange_partners


@dataclass
class Presentation:
    route: str | None
    dosage_form: str | None
    strength: str | None

    @property
    def family(self) -> str | None:
        return route_family(self.route)


@dataclass
class CandidateSet:
    candidates: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _company_key(name: str | None) -> str:
    return (name or "").lower().replace(",", " ").split(" ")[0] if name else ""


def infer_presentation(
    requested: Presentation, shortages: list[ShortageRecord], label_routes: list[str] | None = None
) -> tuple[Presentation, str | None]:
    """Fill a missing route or form from the dominant presentation in current shortages."""
    if requested.route and requested.dosage_form:
        return requested, None
    current = [s for s in shortages if s.status is ShortageStatus.CURRENT]
    routes = defaultdict(int)
    forms = defaultdict(int)
    for s in current:
        for r in s.routes:
            routes[r] += 1
        df = normalize_dosage_form(s.dosage_form)
        if df:
            forms[df.base] += 1
    if not routes and label_routes:
        for r in label_routes:
            routes[r] += 1
    route = requested.route or (max(routes, key=routes.get) if routes else None)
    form = requested.dosage_form or (max(forms, key=forms.get) if forms else None)
    note = None
    if (route and not requested.route) or (form and not requested.dosage_form):
        source = "current shortage entries" if current else "the drug's indexed labels"
        note = f"Presentation inferred from {source}: {form or 'any form'}, {route or 'any route'}."
    return Presentation(route, form, requested.strength), note


class CandidateGenerator:
    def __init__(
        self, store: KnowledgeStore, embedder: Embedder, max_class_members: int = 40, alt_min_similarity: float = 0.78
    ) -> None:
        self.store, self.embedder, self.max_class_members = store, embedder, max_class_members
        self.alt_min_similarity = alt_min_similarity

    def generate(
        self,
        concept: DrugConcept,
        presentation: Presentation,
        shortages: list[ShortageRecord],
        include_alternative_classes: bool = True,
        limit: int = 12,
    ) -> CandidateSet:
        out = CandidateSet()
        ing = concept.ingredients[0]
        t1 = self._tier1(ing, presentation, shortages)
        if t1:
            out.candidates.append(t1)
        out.candidates.extend(self._tier2(ing, presentation))
        t3 = self._tier3(concept, presentation)
        out.candidates.extend(t3)
        if include_alternative_classes:
            exclude = {ing} | {c.ingredient for c in t3}
            out.candidates.extend(self._tier4(ing, exclude))
        if not t1:
            out.notes.append("No A rated therapeutic equivalent from an unaffected manufacturer was found.")
        seen: set[tuple[str, CandidateTier, str]] = set()
        unique: list[Candidate] = []
        for c in out.candidates:
            key = (c.ingredient, c.tier, c.display_name)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        out.candidates = unique[: limit * 2]
        return out

    # Tier 1 ------------------------------------------------------------------------------------

    def _tier1(self, ing: str, p: Presentation, shortages: list[ShortageRecord]) -> Candidate | None:
        affected_companies = {
            _company_key(s.company_name) for s in shortages if s.status is ShortageStatus.CURRENT and s.company_name
        }
        want_form = normalize_dosage_form(p.dosage_form)
        products: list[OrangeBookProduct] = []
        for prod in self.store.orange_book_for_ingredient(ing):
            if not prod.is_marketed or not prod.is_a_rated:
                continue
            if p.family and route_family(prod.route) != p.family:
                continue
            pf = normalize_dosage_form(prod.dosage_form)
            if want_form and pf and (pf.base != want_form.base or pf.modified_release != want_form.modified_release):
                continue
            if p.strength and not same_strength(prod.strength, p.strength):
                continue
            products.append(prod)
        unaffected = [x for x in products if _company_key(x.applicant_full_name or x.applicant) not in affected_companies
                      and _company_key(x.applicant) not in affected_companies]
        if unaffected:
            descriptors = sorted({
                f"{x.trade_name.title()} {x.strength} ({x.applicant_full_name or x.applicant}), TE {x.te_code}"
                for x in unaffected
            })
            return Candidate(
                ingredient=ing, display_name=f"{ing} (another manufacturer)",
                tier=CandidateTier.THERAPEUTIC_EQUIVALENT,
                rationale=(
                    f"{len({x.applicant for x in unaffected})} manufacturer(s) market A rated {ing} in the same "
                    "form, route and strength and are not listed in the current shortage entries."
                ),
                products=descriptors[:12], te_codes=sorted({x.te_code for x in unaffected if x.te_code}),
            )
        if products:
            return None  # every A rated source is affected
        # Fallback when the Orange Book has no TE evaluation (for example older or 505(b)(2) products)
        labels = [d for d in self.store.labels_for_ingredient(ing, limit=60)
                  if len(d.ingredients) == 1 and (not p.family or p.family in {route_family(r) for r in d.routes})]
        makers = sorted({d.manufacturer for d in labels if d.manufacturer
                         and _company_key(d.manufacturer) not in affected_companies})
        if len(makers) >= 1:
            return Candidate(
                ingredient=ing, display_name=f"{ing} (another labeller)",
                tier=CandidateTier.THERAPEUTIC_EQUIVALENT,
                rationale=(
                    "Other labellers hold current labels for this ingredient by the same route, but no Orange Book "
                    "TE code was matched. Confirm pharmaceutical equivalence before substituting."
                ),
                products=makers[:12],
            )
        return None

    # Tier 2 ------------------------------------------------------------------------------------

    def _tier2(self, ing: str, p: Presentation) -> list[Candidate]:
        want_form = normalize_dosage_form(p.dosage_form)
        options: dict[tuple[str, str], list[str]] = defaultdict(list)
        for prod in self.store.orange_book_for_ingredient(ing):
            if not prod.is_marketed:
                continue
            fam = route_family(prod.route) or "unknown"
            pf = normalize_dosage_form(prod.dosage_form)
            key = (fam, str(pf) if pf else prod.dosage_form.lower())
            same = fam == p.family and (not want_form or (pf and pf.base == want_form.base
                                                           and pf.modified_release == want_form.modified_release))
            if not same:
                options[key].append(f"{prod.trade_name.title()} {prod.strength} ({prod.applicant})")
        if not options:
            for d in self.store.labels_for_ingredient(ing, limit=60):
                if len(d.ingredients) != 1:
                    continue
                for r in d.routes:
                    fam = route_family(r) or r
                    if p.family and fam != p.family:
                        options[(fam, r)].append(d.title)
        out: list[Candidate] = []
        for (fam, form), prods in sorted(options.items(), key=lambda kv: -len(kv[1]))[:3]:
            route_note = "route change" if fam != p.family else "formulation change"
            out.append(
                Candidate(
                    ingredient=ing, display_name=f"{ing} {form} ({fam})",
                    tier=CandidateTier.ALTERNATIVE_FORMULATION,
                    rationale=f"Same active ingredient, available as {form} by the {fam} route ({route_note}); "
                    f"{len(prods)} marketed product(s) indexed.",
                    products=sorted(set(prods))[:8],
                )
            )
        return out

    # Tier 3 ------------------------------------------------------------------------------------

    def _tier3(self, concept: DrugConcept, p: Presentation) -> list[Candidate]:
        ing = concept.ingredients[0]
        known = self.store.known_ingredients()
        shared: dict[str, list[str]] = defaultdict(list)
        for cls in concept.classes:
            is_atc4 = cls.source == "ATC" and len(cls.class_id) == 5
            if cls.class_type.upper() != "EPC" and not is_atc4:
                continue
            for member in self.store.ingredients_in_class(cls.class_id, limit=self.max_class_members):
                m = active_moiety(member)
                if m != ing and m in known and cls.class_name not in shared[m]:
                    shared[m].append(cls.class_name)
        for partner in interchange_partners(ing):
            if partner in known and "published dose equivalence table" not in shared[partner]:
                shared[partner].append("published dose equivalence table")
        out: list[Candidate] = []
        for m, classes in sorted(shared.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            labels = [d for d in self.store.labels_for_ingredient(m, limit=30) if len(d.ingredients) == 1]
            if not labels:
                continue
            routes = sorted({route_family(r) or r for d in labels for r in d.routes})
            same_route = not p.family or p.family in routes
            out.append(
                Candidate(
                    ingredient=m, display_name=m, tier=CandidateTier.THERAPEUTIC_INTERCHANGE,
                    rationale=f"Shares {', '.join(classes[:2])} with {ing}."
                    + ("" if same_route else f" Not available by the {p.family} route (available: {', '.join(routes)})."),
                    shared_classes=classes,
                    products=sorted({d.title for d in labels})[:6],
                )
            )
        return out[: self.max_class_members]

    # Tier 4 ------------------------------------------------------------------------------------

    def _tier4(self, ing: str, exclude: set[str], limit: int = 4) -> list[Candidate]:
        indications = self.store.section_chunks(ing, (SectionType.INDICATIONS,), limit=3)
        if not indications:
            return []
        query = " ".join(c.text for c in indications)[:1500]
        vec = self.embedder.embed_query(query)
        hits = self.store.vector_search(vec, 60, sections=[SectionType.INDICATIONS])
        lex = self.store.lexical_search(query[:400], 60, sections=[SectionType.INDICATIONS])
        # Require agreement between dense and lexical retrieval: an alternative class is only proposed when
        # both views of the indication text agree, which suppresses spurious semantic neighbours.
        # A calibrated similarity floor then removes weak neighbours that share only label boilerplate.
        lex_rank = {cid: r for r, (cid, _) in enumerate(lex, 1)}
        scores: dict[str, float] = {}
        for rank, (cid, sim) in enumerate(hits, 1):
            if cid in lex_rank and sim >= self.alt_min_similarity:
                scores[cid] = 1 / (60 + rank) + 1 / (60 + lex_rank[cid])
        by_ing: dict[str, float] = {}
        evidence: dict[str, str] = {}
        for ch in self.store.chunks(list(scores)):
            if len(ch.ingredients) != 1:
                continue
            m = active_moiety(ch.ingredients[0])
            if m in exclude or m == ing:
                continue
            if scores[ch.chunk_id] > by_ing.get(m, 0):
                by_ing[m] = scores[ch.chunk_id]
                evidence[m] = ch.chunk_id
        out = []
        for m, _ in sorted(by_ing.items(), key=lambda kv: -kv[1])[:limit]:
            out.append(
                Candidate(
                    ingredient=m, display_name=m, tier=CandidateTier.ALTERNATIVE_CLASS,
                    rationale=f"Labelled indications overlap with those of {ing}, but the pharmacological class differs.",
                    citations=[evidence[m]], requires_specialist_review=True,
                )
            )
        return out
