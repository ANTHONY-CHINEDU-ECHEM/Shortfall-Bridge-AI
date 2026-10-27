"""Transparent, rule based ranking of substitution candidates.

The score is intentionally simple and explainable. Every additive term is returned in
``Candidate.score_breakdown`` so a clinician can see exactly why one option ranks above another.
Blocked candidates are never ranked: they move to ``not_recommended`` with their blocking reasons
visible.

Terms
    tier base               1.00, 0.80, 0.62, 0.35 by substitution tier
    caution                 -0.10 per gate at Caution (supply handled separately)
    needs data              -0.04 per gate at Needs data
    supply, same route      -0.22 when the alternative is itself short by the route it would be used
    supply, market          -0.10 when the presentation is short but the proposed makers are not named
    route mismatch          -0.15 when the alternative is not available by the requested route family
    dose ceiling            -0.15 when the converted daily dose exceeds the labelled maximum
    dose available          +0.06 when a published conversion produced a starting dose
    evidence                up to +0.08 for the share of non clear findings that carry label citations
    specialist review       -0.05 for alternative class proposals
"""

from __future__ import annotations

from ..domain.enums import CandidateTier, GateName, GateStatus
from ..domain.models import Candidate, ScoreTerm

TIER_BASE = {
    CandidateTier.THERAPEUTIC_EQUIVALENT: 1.00,
    CandidateTier.ALTERNATIVE_FORMULATION: 0.80,
    CandidateTier.THERAPEUTIC_INTERCHANGE: 0.62,
    CandidateTier.ALTERNATIVE_CLASS: 0.35,
}
CAUTION_PENALTY = 0.10
UNKNOWN_PENALTY = 0.04
AVAILABILITY_PENALTY = 0.22
MARKET_SUPPLY_PENALTY = 0.10
ROUTE_MISMATCH_PENALTY = 0.15
CEILING_PENALTY = 0.15
DOSE_BONUS = 0.06
EVIDENCE_BONUS = 0.08
SPECIALIST_PENALTY = 0.05


def score_terms(c: Candidate) -> list[ScoreTerm]:
    terms = [ScoreTerm(term="tier", value=TIER_BASE[c.tier], detail=c.tier.label)]
    for g in c.gates:
        if g.status is GateStatus.CAUTION:
            if g.gate is GateName.AVAILABILITY:
                market = g.facts.get("supply_scope") == "market"
                terms.append(ScoreTerm(
                    term="supply_market" if market else "supply_same_route",
                    value=-(MARKET_SUPPLY_PENALTY if market else AVAILABILITY_PENALTY),
                    detail="presentation short, proposed makers not named" if market
                    else "alternative is itself in shortage by this route",
                ))
            else:
                terms.append(ScoreTerm(term="caution", value=-CAUTION_PENALTY, detail=g.gate.value))
        elif g.status is GateStatus.UNKNOWN:
            terms.append(ScoreTerm(term="needs_data", value=-UNKNOWN_PENALTY, detail=g.gate.value))
    if c.route_match is False and c.tier in (CandidateTier.THERAPEUTIC_INTERCHANGE, CandidateTier.ALTERNATIVE_CLASS):
        terms.append(ScoreTerm(term="route_mismatch", value=-ROUTE_MISMATCH_PENALTY,
                               detail=f"available by {', '.join(c.routes_available) or 'another route'} only"))
    dc = c.dose_conversion
    if dc and dc.feasible:
        if dc.ceiling_exceeded:
            terms.append(ScoreTerm(term="dose_ceiling", value=-CEILING_PENALTY,
                                   detail=f"suggested daily total exceeds labelled maximum {dc.label_max_daily}"))
        else:
            terms.append(ScoreTerm(term="dose_available", value=DOSE_BONUS, detail=dc.method))
    gated = [g for g in c.gates if g.status is not GateStatus.PASS]
    if gated:
        share = sum(1 for g in gated if g.citations) / len(gated)
        if share:
            terms.append(ScoreTerm(term="evidence", value=round(EVIDENCE_BONUS * share, 4),
                                   detail=f"{share:.0%} of non clear findings cite label text"))
    if c.requires_specialist_review:
        terms.append(ScoreTerm(term="specialist_review", value=-SPECIALIST_PENALTY, detail="alternative class"))
    return terms


def score_candidate(c: Candidate) -> float:
    return round(max(sum(t.value for t in score_terms(c)), 0.0), 4)


def rank(candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    allowed, blocked = [], []
    for c in candidates:
        c.score_breakdown = score_terms(c)
        c.score = round(max(sum(t.value for t in c.score_breakdown), 0.0), 4)
        (blocked if c.blocked else allowed).append(c)
    allowed.sort(key=lambda c: (-c.score, c.tier.rank, c.display_name))
    blocked.sort(key=lambda c: (c.tier.rank, c.display_name))
    return allowed, blocked
