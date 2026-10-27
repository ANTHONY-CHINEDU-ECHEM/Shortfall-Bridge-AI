"""Pharmacist review analytics: how often clinicians agree with the engine, and why they do not.

Every review is a labelled example (option, decision, reason). Aggregated, they measure ranking
quality in the field and point at the rules that most need attention, closing the governance loop
described in the clinical safety case.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ..domain.enums import ReviewDecision
from ..domain.models import ReviewRecord


def review_summary(reviews: list[ReviewRecord]) -> dict[str, Any]:
    n = len(reviews)
    decisions = Counter(r.decision.value for r in reviews)
    by_tier: dict[str, Counter[str]] = defaultdict(Counter)
    by_rank: dict[str, Counter[str]] = defaultdict(Counter)
    for r in reviews:
        by_tier[r.candidate_tier.value if r.candidate_tier else "unknown"][r.decision.value] += 1
        by_rank[str(r.rank) if r.rank else "unranked"][r.decision.value] += 1
    reasons = Counter(" ".join((r.reason or "").lower().split())[:120] for r in reviews
                      if r.decision is not ReviewDecision.AGREE and r.reason)
    overridden = Counter(r.candidate_ingredient for r in reviews if r.decision is not ReviewDecision.AGREE)
    chosen = Counter(r.chosen_alternative.lower() for r in reviews if r.chosen_alternative)

    def rate(c: Counter[str]) -> float | None:
        total = sum(c.values())
        return round(c.get("agree", 0) / total, 3) if total else None

    top1 = by_rank.get("1", Counter())
    return {
        "reviews": n,
        "requests_reviewed": len({r.request_id for r in reviews}),
        "decisions": dict(decisions),
        "agreement_rate": rate(decisions),
        "top_option_agreement_rate": rate(top1),
        "by_tier": {t: {"reviews": sum(c.values()), "agreement_rate": rate(c), **dict(c)} for t, c in by_tier.items()},
        "by_rank": {k: {"reviews": sum(c.values()), "agreement_rate": rate(c)} for k, c in sorted(by_rank.items())},
        "top_override_reasons": [{"reason": k, "count": v} for k, v in reasons.most_common(10)],
        "most_overridden_options": [{"ingredient": k, "count": v} for k, v in overridden.most_common(10)],
        "alternatives_chosen_instead": [{"ingredient": k, "count": v} for k, v in chosen.most_common(10)],
    }
