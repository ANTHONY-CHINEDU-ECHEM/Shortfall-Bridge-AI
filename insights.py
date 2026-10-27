"""Shortage landscape analytics over the FDA Drug Shortages table.

Pharmacy leadership asks different questions from the bedside: which therapeutic areas are most
affected, why, for how long, by which manufacturers, and what changed recently. This module
answers them deterministically from the same shortage records the engine uses, so the numbers on
the dashboard and the supply gate never disagree.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

from ..domain.enums import ShortageStatus
from ..domain.models import ShortageRecord
from ..engine.gates import shortage_route_families


def _top(counter: Counter[str], n: int) -> list[dict[str, Any]]:
    return [{"name": k, "count": v} for k, v in counter.most_common(n)]


def _month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_back(today: date, n: int) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def shortage_insights(records: list[ShortageRecord], today: date | None = None, months: int = 24) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    by_status = Counter(r.status.value for r in records)
    current = [r for r in records if r.status is ShortageStatus.CURRENT]

    categories: Counter[str] = Counter()
    for r in current:
        for c in r.therapeutic_category or ["Not stated"]:
            categories[c.strip() or "Not stated"] += 1
    reasons = Counter((r.shortage_reason or "Not stated").strip() for r in current)
    routes: Counter[str] = Counter()
    for r in current:
        for f in shortage_route_families(r) or {"unknown"}:
            routes[f] += 1
    companies = Counter((r.company_name or "Not stated").strip() for r in current)
    ingredients: Counter[str] = Counter()
    for r in current:
        for i in set(r.ingredients) or {r.generic_name.lower()}:
            ingredients[i] += 1

    durations = [(today - r.initial_posting_date).days for r in current
                 if r.initial_posting_date and r.initial_posting_date <= today]
    longest = sorted((r for r in current if r.initial_posting_date), key=lambda r: r.initial_posting_date or today)[:10]
    recent = sorted((r for r in records if r.update_date), key=lambda r: r.update_date or date.min, reverse=True)[:10]

    window = _months_back(today, months)
    postings = Counter(_month(r.initial_posting_date) for r in records if r.initial_posting_date)
    updates = Counter(_month(r.update_date) for r in records if r.update_date)

    def brief(r: ShortageRecord) -> dict[str, Any]:
        return {"record_id": r.record_id, "generic_name": r.generic_name, "presentation": r.presentation,
                "company_name": r.company_name, "status": r.status.value,
                "initial_posting_date": r.initial_posting_date.isoformat() if r.initial_posting_date else None,
                "update_date": r.update_date.isoformat() if r.update_date else None,
                "days_listed": (today - r.initial_posting_date).days if r.initial_posting_date else None}

    p90 = sorted(durations)[max(0, int(len(durations) * 0.9) - 1)] if durations else None
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": today.isoformat(),
        "total_records": len(records),
        "by_status": dict(by_status),
        "current": {
            "entries": len(current),
            "ingredients": len(ingredients),
            "companies": len({r.company_name for r in current if r.company_name}),
            "by_category": _top(categories, 12),
            "by_reason": _top(reasons, 8),
            "by_route_family": _top(routes, 8),
            "top_companies": _top(companies, 10),
            "most_affected_ingredients": _top(ingredients, 10),
            "days_listed": {
                "median": int(statistics.median(durations)) if durations else None,
                "p90": p90,
                "max": max(durations) if durations else None,
            },
            "longest_running": [brief(r) for r in longest],
        },
        "recently_updated": [brief(r) for r in recent],
        "timeline": [{"month": m, "new_postings": postings.get(m, 0), "updates": updates.get(m, 0)} for m in window],
    }
