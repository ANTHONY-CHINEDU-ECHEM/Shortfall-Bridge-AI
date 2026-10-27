"""Transparent, rule based ranking of substitution candidates.

The score is intentionally simple and explainable; every term appears in the API response so a
clinician can see why one option ranks above another. Blocked candidates are never ranked: they
move to ``not_recommended`` with their blocking reasons visible.
"""

from __future__ import annotations

from ..domain.enums import CandidateTier, GateName, GateStatus
from ..domain.models import Candidate

TIER_BASE = {
    CandidateTier.THERAPEUTIC_EQUIVALENT: 1.00,
    CandidateTier.ALTERNATIVE_FORMULATION: 0.80,
    CandidateTier.THERAPEUTIC_INTERCHANGE: 0.62,
    CandidateTier.ALTERNATIVE_CLASS: 0.35,
}
CAUTION_PENALTY = 0.10
UNKNOWN_PENALTY = 0.04
AVAILABILITY_PENALTY = 0.22
DOSE_BONUS = 0.06
EVIDENCE_BONUS = 0.08


def score_candidate(c: Candidate) -> float:
    s = TIER_BASE[c.tier]
    for g in c.gates:
        if g.status is GateStatus.CAUTION:
            s -= AVAILABILITY_PENALTY if g.gate is GateName.AVAILABILITY else CAUTION_PENALTY
        elif g.status is GateStatus.UNKNOWN:
            s -= UNKNOWN_PENALTY
    if c.dose_conversion and c.dose_conversion.feasible:
        s += DOSE_BONUS
    gated = [g for g in c.gates if g.status is not GateStatus.PASS]
    if gated:
        s += EVIDENCE_BONUS * sum(1 for g in gated if g.citations) / len(gated)
    if c.requires_specialist_review:
        s -= 0.05
    return round(max(s, 0.0), 4)


def rank(candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
    allowed, blocked = [], []
    for c in candidates:
        c.score = score_candidate(c)
        (blocked if c.blocked else allowed).append(c)
    allowed.sort(key=lambda c: (-c.score, c.tier.rank, c.display_name))
    blocked.sort(key=lambda c: (c.tier.rank, c.display_name))
    return allowed, blocked
