"""NLM RxNav clients: RxNorm concept resolution and RxClass drug class membership.

Used for two jobs:
* resolving free text drug names typed by clinicians to RxNorm ingredients, and
* enriching each ingredient with ATC level 4 classes and DailyMed EPC classes, which define
  the therapeutic interchange neighbourhood used by Tier 3 candidates.

All calls are cached on disk and rate limited below the NLM fair use ceiling.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings
from ..domain.models import DrugClass
from ..domain.normalize import active_moiety
from .http import HttpClient

logger = logging.getLogger(__name__)


class RxNavClient:
    def __init__(self, http: HttpClient, settings: Settings) -> None:
        self.http = http
        self.base = settings.rxnav_base_url.rstrip("/")

    # RxNorm -----------------------------------------------------------------------------------

    def approximate_match(self, term: str, max_entries: int = 5) -> list[dict[str, Any]]:
        data = self.http.get_json(
            f"{self.base}/approximateTerm.json", params={"term": term, "maxEntries": max_entries}
        )
        return list((data.get("approximateGroup") or {}).get("candidate") or [])

    def related(self, rxcui: str, ttys: str = "IN+PIN+MIN") -> list[dict[str, Any]]:
        data = self.http.get_json(f"{self.base}/rxcui/{rxcui}/related.json", params={"tty": ttys.replace("+", " ")})
        out: list[dict[str, Any]] = []
        for group in (data.get("relatedGroup") or {}).get("conceptGroup") or []:
            out.extend(group.get("conceptProperties") or [])
        return out

    def ingredient_for_term(self, term: str) -> tuple[str | None, str | None, float]:
        """Return (ingredient rxcui, ingredient name, match score) for free text."""
        candidates = self.approximate_match(term)
        for cand in candidates:
            rxcui = cand.get("rxcui")
            if not rxcui:
                continue
            score = float(cand.get("score") or 0.0)
            ings = [c for c in self.related(rxcui, "IN") if c.get("tty") == "IN"]
            if ings:
                return ings[0]["rxcui"], ings[0]["name"].lower(), score
        return None, None, 0.0

    def rxcui_for_ingredient(self, name: str) -> str | None:
        data = self.http.get_json(f"{self.base}/rxcui.json", params={"name": name, "search": 2})
        ids = (data.get("idGroup") or {}).get("rxnormId") or []
        return ids[0] if ids else None

    # RxClass ----------------------------------------------------------------------------------

    def classes_for_rxcui(self, rxcui: str) -> list[DrugClass]:
        out: list[DrugClass] = []
        for rela_source, rela in (("ATC", None), ("DAILYMED", "has_epc")):
            params: dict[str, Any] = {"rxcui": rxcui, "relaSource": rela_source}
            if rela:
                params["relas"] = rela
            data = self.http.get_json(f"{self.base}/rxclass/class/byRxcui.json", params=params)
            for info in (data.get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []:
                item = info.get("rxclassMinConceptItem") or {}
                cls = DrugClass(
                    class_id=item.get("classId", ""),
                    class_name=item.get("className", ""),
                    class_type=item.get("classType", ""),
                    source=rela_source,
                )
                if cls.class_id and all(c.class_id != cls.class_id for c in out):
                    out.append(cls)
        return out

    def class_members(self, class_id: str, rela_source: str, rela: str | None = None) -> list[str]:
        params: dict[str, Any] = {"classId": class_id, "relaSource": rela_source, "ttys": "IN"}
        if rela:
            params["rela"] = rela
        data = self.http.get_json(f"{self.base}/rxclass/classMembers.json", params=params)
        members: list[str] = []
        for m in (data.get("drugMemberGroup") or {}).get("drugMember") or []:
            name = (m.get("minConcept") or {}).get("name")
            if name:
                n = active_moiety(name)
                if n not in members:
                    members.append(n)
        return members
