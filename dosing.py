"""Dosing frequency parsing and labelled maximum dose extraction.

Two safety checks depend on these:

* the daily total implied by a suggested conversion (dose x administrations per day), and
* the labelled maximum daily dose for the target drug, read from its Dosage and Administration
  section, so that a conversion which would exceed the label ceiling is flagged before anyone
  acts on it.

Both are deliberately conservative. Ambiguous frequencies ("as needed", "with meals") return
``None`` instead of a guess, and weight or body surface based maxima (mg/kg, mg/m2) are ignored
because they cannot be compared without patient specific calculation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.models import Chunk

# Frequency -----------------------------------------------------------------------------------

_WORD_NUM = {"once": 1, "one": 1, "twice": 2, "two": 2, "three": 3, "thrice": 3, "four": 4, "five": 5, "six": 6}

_FIXED: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\b(?:as needed|as required|prn|when required|if needed)\b"), -1.0),  # ambiguous
    (re.compile(r"\bevery other day\b|\balternate days?\b|\bqod\b"), 0.5),
    (re.compile(r"\b(?:once )?weekly\b|\bonce a week\b|\bevery week\b"), 1 / 7),
    (re.compile(r"\bqid\b|\bqds\b"), 4.0),
    (re.compile(r"\btid\b|\btds\b"), 3.0),
    (re.compile(r"\bbid\b|\bbd\b"), 2.0),
    (re.compile(r"\b(?:qd|od|daily|nightly|mane|nocte|qhs|at night|at bedtime|in the morning)\b"), 1.0),
]
_TIMES = re.compile(r"\b(once|twice|thrice|one|two|three|four|five|six|\d+)\s*(?:times?|x)?\s*(?:a|per|each|every)?\s*(?:day|daily)\b")
_EVERY_HOURS = re.compile(r"\b(?:every|q)\s*(\d{1,2})\s*(?:-\s*\d{1,2}\s*)?(?:h|hr|hrs|hours?)\b")
_QNH = re.compile(r"\bq(\d{1,2})h\b")


def doses_per_day(frequency: str | None) -> float | None:
    """Administrations per day implied by free text frequency, or None when ambiguous."""
    if not frequency:
        return None
    f = " ".join(frequency.lower().replace(".", "").split())
    if _FIXED[0][0].search(f):
        return None  # "as needed" has no fixed daily total
    m = _EVERY_HOURS.search(f) or _QNH.search(f)
    if m:
        hours = int(m.group(1))
        return round(24 / hours, 3) if 1 <= hours <= 72 else None
    m = _TIMES.search(f)
    if m:
        token = m.group(1)
        n = _WORD_NUM.get(token) or (int(token) if token.isdigit() else None)
        return float(n) if n and 1 <= n <= 24 else None
    for pattern, value in _FIXED[1:]:
        if pattern.search(f):
            return value
    return None


# Labelled maximum dose -------------------------------------------------------------------------

_UNIT_TO_MG = {"mg": 1.0, "g": 1000.0, "gram": 1000.0, "grams": 1000.0, "mcg": 0.001, "microgram": 0.001,
               "micrograms": 0.001, "µg": 0.001}
_AMOUNT = r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(mg|g|grams?|mcg|micrograms?|µg)\b"
_DAILY = r"(?:/\s*day|per day|a day|daily|each day|in (?:a |any )?24(?:-| )hours?(?: period)?|/24 ?h(?:ours)?)"
_PER_WEIGHT = re.compile(r"\b(?:mg|mcg|g)\s*/\s*(?:kg|m2|m\^2|m²)|per (?:kg|kilogram|square meter)", re.I)

_MAX_PATTERNS = [
    re.compile(rf"maximum (?:recommended )?(?:total )?(?:daily |24.hour )?dos(?:e|age)[^.;]{{0,60}}?{_AMOUNT}", re.I),
    re.compile(rf"(?:do not|should not|not to) exceed[^.;]{{0,30}}?{_AMOUNT}[^.;]{{0,20}}?{_DAILY}", re.I),
    re.compile(rf"up to (?:a maximum of )?{_AMOUNT}[^.;]{{0,12}}?{_DAILY}", re.I),
    re.compile(rf"{_AMOUNT}\s*{_DAILY}[^.;]{{0,20}}?(?:maximum|max\b)", re.I),
]


@dataclass(frozen=True)
class LabelMaximum:
    mg_per_day: float
    sentence: str
    chunk_id: str


def _to_mg(value: str, unit: str) -> float:
    return float(value.replace(",", "")) * _UNIT_TO_MG[unit.lower()]


def extract_max_daily_doses(chunks: list[Chunk]) -> list[LabelMaximum]:
    """Labelled maximum daily doses (mg per day) found in dosage text, strongest evidence first."""
    from ..engine.label_rules import sentences  # local import keeps this module dependency light

    found: list[LabelMaximum] = []
    for ch in chunks:
        for s in sentences(ch):
            low = s.lower()
            if "max" not in low and "exceed" not in low and "up to" not in low:
                continue
            for pattern in _MAX_PATTERNS:
                for m in pattern.finditer(s):
                    window = s[max(0, m.start() - 10): m.end() + 12]
                    if _PER_WEIGHT.search(window):
                        continue
                    daily_context = re.search(_DAILY, s[m.start(): m.end() + 40], re.I) or "daily" in m.group(0).lower()
                    if pattern is _MAX_PATTERNS[0] and not daily_context and "single" in low:
                        continue
                    mg = _to_mg(m.group(1), m.group(2))
                    if 0 < mg <= 100_000:
                        found.append(LabelMaximum(mg, s, ch.chunk_id))
    unique: dict[float, LabelMaximum] = {}
    for f in found:
        unique.setdefault(f.mg_per_day, f)
    return sorted(unique.values(), key=lambda x: -x.mg_per_day)


_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|g|mcg)?\s*to\s*(\d+(?:\.\d+)?)\s*(mg|g|mcg)\b", re.I)


def parse_dose_mg(text: str | None) -> tuple[float, float] | None:
    """(low, high) in mg from '2 mg', '7.5 mg to 11 mg', '20 to 40 mg daily' or '0.5 g'."""
    if not text:
        return None
    m = _RANGE.search(text)
    if m:
        unit_hi = m.group(4).lower()
        unit_lo = (m.group(2) or unit_hi).lower()
        return _to_mg(m.group(1), unit_lo), _to_mg(m.group(3), unit_hi)
    values = [_to_mg(v, u) for v, u in re.findall(_AMOUNT, text, re.I)]
    return (min(values), max(values)) if values else None


def fmt_mg(mg: float) -> str:
    if mg >= 1000 and mg % 1000 == 0:
        return f"{mg / 1000:g} g"
    if mg < 1:
        return f"{mg * 1000:g} mcg"
    return f"{mg:g} mg"
