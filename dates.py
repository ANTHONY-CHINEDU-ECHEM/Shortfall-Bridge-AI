"""Tolerant date parsing for the mix of formats used across FDA datasets."""

from __future__ import annotations

from datetime import date, datetime

_FORMATS = ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S")


def parse_date(value: str | int | None) -> date | None:
    if value is None or value == "":
        return None
    raw = str(value).strip()
    candidates = [raw, raw[:10], raw[:8]]
    for text in candidates:
        for fmt in _FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None
