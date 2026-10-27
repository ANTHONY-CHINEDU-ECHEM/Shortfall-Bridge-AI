"""Dose equivalence conversion over published reference tables.

Conversions are deterministic, auditable and conservative:
* only drugs present in the same table convert, and only along routes the table covers;
* excluded drugs (methadone, fentanyl, buprenorphine) always refuse with the reason;
* opioid conversions apply the incomplete cross tolerance reduction and report a range;
* results are rounded to practical measurable doses and the unrounded value is kept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from ..domain.models import DoseConversion, DoseInput
from ..domain.normalize import active_moiety, normalize_route, route_family

_ROUTE_KEYS = ("oral", "intravenous", "injection", "intramuscular", "subcutaneous")


@dataclass
class EquivalenceTable:
    id: str
    type: str
    title: str
    unit: str
    reference: str
    reference_url: str
    members: dict[str, dict[str, Any]]
    excluded: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    cross_tolerance_reduction: tuple[float, float] | None = None

    def covers(self, ingredient: str) -> bool:
        return ingredient in self.members or ingredient in self.excluded


@lru_cache(maxsize=1)
def load_tables() -> tuple[EquivalenceTable, ...]:
    raw = yaml.safe_load(
        resources.files("shortfall_bridge.engine").joinpath("data/equivalence_tables.yaml").read_text()
    )
    tables = []
    for t in raw["tables"]:
        ctr = t.get("cross_tolerance_reduction")
        tables.append(
            EquivalenceTable(
                id=t["id"], type=t["type"], title=t["title"], unit=t["unit"],
                reference=" ".join(t["reference"].split()), reference_url=t["reference_url"],
                members={k.lower(): v for k, v in t["members"].items()},
                excluded={k.lower(): v for k, v in (t.get("excluded") or {}).items()},
                warnings=list(t.get("warnings") or []),
                cross_tolerance_reduction=tuple(ctr) if ctr else None,
            )
        )
    return tuple(tables)


def table_for(a: str, b: str) -> EquivalenceTable | None:
    a, b = active_moiety(a), active_moiety(b)
    for t in load_tables():
        if t.covers(a) and t.covers(b):
            return t
    return None


def practical_round(x: float) -> float:
    if x <= 0:
        return 0.0
    if x < 1:
        step = 0.125
    elif x < 2:
        step = 0.25
    elif x < 10:
        step = 0.5
    elif x < 100:
        step = 1.0
    else:
        step = 5.0
    return round(round(x / step) * step, 3)


def _fmt(x: float, unit: str) -> str:
    return f"{x:g} {unit}"


def _route_key(route: str | None) -> str:
    r = normalize_route(route) or "oral"
    if r in _ROUTE_KEYS:
        return r
    fam = route_family(r)
    return "injection" if fam == "parenteral" else "oral"


def _route_value(member: dict[str, Any], route: str) -> float | None:
    if route in member:
        return float(member[route])
    if route in {"intravenous", "intramuscular", "subcutaneous"} and "injection" in member:
        return float(member["injection"])
    if route == "injection" and "intravenous" in member:
        return float(member["intravenous"])
    return None


def convert(
    from_ingredient: str,
    to_ingredient: str,
    dose: DoseInput | None,
    from_route: str | None = None,
    to_route: str | None = None,
) -> DoseConversion | None:
    """Return a DoseConversion, or None when no table relates the two drugs."""
    a, b = active_moiety(from_ingredient), active_moiety(to_ingredient)
    table = table_for(a, b)
    if table is None:
        return None
    base = {"from_drug": a, "to_drug": b, "reference": f"{table.title}. {table.reference}"}
    for drug in (a, b):
        if drug in table.excluded:
            return DoseConversion(
                feasible=False, method=f"refused:{table.id}", warnings=[table.excluded[drug]], **base
            )
    if dose is None:
        return DoseConversion(
            feasible=False, method=f"{table.id}:no_input_dose",
            warnings=["Enter the current dose to calculate an equivalent starting dose."], **base,
        )
    if dose.unit.lower() not in {table.unit, "mg"}:
        return DoseConversion(
            feasible=False, method=f"{table.id}:unit_unsupported",
            warnings=[f"Table is expressed in {table.unit}; received {dose.unit}."], **base,
        )
    rf, rt = _route_key(from_route), _route_key(to_route)
    from_dose_str = f"{dose.amount:g} {dose.unit}" + (f" {dose.frequency}" if dose.frequency else "")

    if table.type == "band":
        return _convert_band(table, a, b, dose, from_dose_str, base)

    vf = _route_value(table.members[a], rf)
    vt = _route_value(table.members[b], rt)
    if vf is None or vt is None:
        missing = a if vf is None else b
        route = rf if vf is None else rt
        return DoseConversion(
            feasible=False, method=f"{table.id}:route_uncovered", from_dose=from_dose_str,
            warnings=[f"The reference table does not cover {missing} by the {route} route."], **base,
        )

    warnings = list(table.warnings)
    if table.type == "mme":
        mme = dose.amount * vf
        exact = mme / vt
        lo_red, hi_red = table.cross_tolerance_reduction or (0.0, 0.0)
        low, high = practical_round(exact * (1 - hi_red)), practical_round(exact * (1 - lo_red))
        return DoseConversion(
            feasible=True, from_dose=from_dose_str,
            suggested_dose=f"{_fmt(low, table.unit)} to {_fmt(high, table.unit)}"
            + (f" {dose.frequency}" if dose.frequency else ""),
            suggested_range=f"calculated equivalent {exact:.3g} {table.unit} ({mme:.3g} MME), "
            f"reduced {int(lo_red * 100)} to {int(hi_red * 100)} percent for cross tolerance",
            method=f"{table.id}:mme_factor", warnings=warnings, **base,
        )

    exact = dose.amount / vf * vt
    rounded = practical_round(exact)
    if a == b and rf != rt:
        warnings.insert(0, f"Route change from {rf} to {rt}; confirm the patient can use the new route.")
    return DoseConversion(
        feasible=True, from_dose=from_dose_str,
        suggested_dose=_fmt(rounded, table.unit) + (f" {dose.frequency}" if dose.frequency else ""),
        suggested_range=f"calculated {exact:.4g} {table.unit}, rounded to a measurable dose",
        method=f"{table.id}:ratio", warnings=warnings, **base,
    )


def _convert_band(
    table: EquivalenceTable, a: str, b: str, dose: DoseInput, from_dose_str: str, base: dict[str, str]
) -> DoseConversion:
    band_from = None
    for band, (lo, hi) in table.members[a].items():
        if lo <= dose.amount <= hi:
            band_from = band
            break
    if band_from is None:
        return DoseConversion(
            feasible=False, method=f"{table.id}:dose_outside_bands", from_dose=from_dose_str,
            warnings=[f"{dose.amount:g} {table.unit} of {a} is not in a published intensity band."], **base,
        )
    target = table.members[b].get(band_from)
    if target is None:
        order = ["high", "moderate", "low"]
        alternatives = [x for x in order if x in table.members[b]]
        return DoseConversion(
            feasible=False, method=f"{table.id}:band_unavailable", from_dose=from_dose_str,
            warnings=[f"{b} has no {band_from} intensity dose; available bands: {', '.join(alternatives)}."],
            **base,
        )
    lo, hi = target
    dose_text = _fmt(lo, table.unit) if lo == hi else f"{lo:g} to {hi:g} {table.unit}"
    return DoseConversion(
        feasible=True, from_dose=from_dose_str, suggested_dose=f"{dose_text} daily",
        suggested_range=f"{band_from} intensity band (matches {dose.amount:g} {table.unit} {a})",
        method=f"{table.id}:band", warnings=list(table.warnings), **base,
    )


def interchange_partners(ingredient: str) -> list[str]:
    """Drugs that share an equivalence table with ``ingredient`` (excluding refused ones)."""
    m = active_moiety(ingredient)
    out: list[str] = []
    for t in load_tables():
        if m in t.members:
            out.extend(x for x in t.members if x != m and x not in out)
    return out


def is_conversion_refused(ingredient: str) -> bool:
    m = active_moiety(ingredient)
    return any(m in t.excluded for t in load_tables())


def round_trip_error(a: str, b: str, amount: float, route: str = "oral") -> float:
    """Used by tests: converting a to b and back should approximately preserve the dose."""
    there = convert(a, b, DoseInput(amount=amount), route, route)
    if not there or not there.feasible or not there.suggested_dose:
        return math.nan
    val = float(there.suggested_dose.split()[0])
    back = convert(b, a, DoseInput(amount=val), route, route)
    if not back or not back.feasible or not back.suggested_dose:
        return math.nan
    return abs(float(back.suggested_dose.split()[0]) - amount) / amount
