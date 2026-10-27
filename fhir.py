"""FHIR R4 input mapping: open the workbench straight from an electronic prescribing system.

``map_fhir`` accepts a FHIR ``Bundle`` (any type), a single ``MedicationRequest``, or a CDS Hooks
style ``{"draftOrders": Bundle, "prefetch": {...}}`` and returns a ``SubstitutionRequest`` plus a
mapping report that records where every value came from and what could not be used.

Resources understood
    MedicationRequest   the order to substitute (draft, active or on hold), with dosage and route
    Medication          referenced or contained medication codes
    Patient             birthDate (age), gender (sex for renal equations)
    Observation         LOINC: serum creatinine (2160-0, 38483-4, 14682-9), eGFR (33914-3, 48642-3,
                        48643-1, 62238-1, 98979-8, 88293-6, 88294-4), creatinine clearance (2164-2),
                        body weight (29463-7, 3141-9), body height (8302-2, 8306-3), pregnancy
                        status (82810-3)
    AllergyIntolerance  active, not refuted, medication allergies
    MedicationStatement / other MedicationRequests   current medicines
    Condition           pregnancy (SNOMED 77386006), dialysis dependence (SNOMED 105502003, ICD-10 Z99.2)

Nothing is invented: a value that cannot be mapped is reported, never defaulted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from ..domain.enums import CreatinineUnit, Sex
from ..domain.models import DoseInput, PatientContext, SubstitutionRequest

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"

SCR_MG_DL = {"2160-0", "38483-4"}
SCR_UMOL_L = {"14682-9", "59826-8"}
EGFR = {"33914-3", "48642-3", "48643-1", "62238-1", "98979-8", "88293-6", "88294-4", "50044-7"}
CRCL = {"2164-2", "35591-7"}
WEIGHT = {"29463-7", "3141-9"}
HEIGHT = {"8302-2", "8306-3", "3137-7"}
PREGNANCY_STATUS = {"82810-3"}
PREGNANT_ANSWERS = {"LA15173-0", "77386006"}
PREGNANCY_CONDITIONS = {"77386006"}
DIALYSIS_CONDITIONS = {"105502003", "Z99.2"}

_PERIOD_UNIT_HOURS = {"s": 1 / 3600, "min": 1 / 60, "h": 1, "d": 24, "wk": 168, "mo": 720, "a": 8760}


@dataclass
class FhirMapping:
    request: SubstitutionRequest | None
    mapped: list[dict[str, str]] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    order_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"order_id": self.order_id, "mapped": self.mapped, "unmapped": self.unmapped, "warnings": self.warnings}


class FhirMappingError(ValueError):
    pass


# Helpers -----------------------------------------------------------------------------------------


def _codes(cc: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """(system, code, display) for each coding of a CodeableConcept."""
    if not cc:
        return []
    return [(c.get("system", ""), str(c.get("code", "")), c.get("display", "")) for c in cc.get("coding") or []]


def _cc_text(cc: dict[str, Any] | None) -> str | None:
    if not cc:
        return None
    if cc.get("text"):
        return str(cc["text"])
    for _, _, display in _codes(cc):
        if display:
            return display
    return None


def _resources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("resourceType") == "Bundle":
                for e in node.get("entry") or []:
                    walk(e.get("resource"))
            elif node.get("resourceType"):
                out.append(node)
                for c in node.get("contained") or []:
                    walk(c)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    if "resourceType" in payload:
        walk(payload)
    else:  # CDS Hooks style: context.draftOrders plus prefetch
        walk(payload.get("draftOrders") or (payload.get("context") or {}).get("draftOrders"))
        for v in (payload.get("prefetch") or {}).values():
            walk(v)
    return out


def _when(r: dict[str, Any]) -> str:
    return str(r.get("effectiveDateTime") or r.get("issued") or (r.get("effectivePeriod") or {}).get("end")
               or (r.get("meta") or {}).get("lastUpdated") or "")


def _age(birth: str, today: date) -> float | None:
    try:
        b = date.fromisoformat(birth[:10]) if len(birth) >= 10 else date(int(birth[:4]), 7, 1)
    except ValueError:
        return None
    years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return float(years) if 0 <= years <= 125 else None


def frequency_text(timing: dict[str, Any] | None) -> str | None:
    """FHIR Timing to the plain frequency vocabulary the engine parses ('twice daily', 'every 8 hours')."""
    if not timing:
        return None
    code_text = _cc_text(timing.get("code"))
    rep = timing.get("repeat") or {}
    freq, period, unit = rep.get("frequency"), rep.get("period"), rep.get("periodUnit")
    if freq and period and unit in _PERIOD_UNIT_HOURS:
        hours = float(period) * _PERIOD_UNIT_HOURS[unit] / float(freq)
        if unit == "d" and float(period) == 1:
            return {1: "once daily", 2: "twice daily", 3: "three times daily", 4: "four times daily"}.get(
                int(freq), f"{int(freq)} times daily")
        if unit == "wk" and float(period) == 1 and int(freq) == 1:
            return "weekly"
        if hours >= 1 and float(hours).is_integer():
            return f"every {int(hours)} hours"
    if rep.get("when"):
        return "once daily"
    return code_text


def _route_text(cc: dict[str, Any] | None) -> str | None:
    text = (_cc_text(cc) or "").lower()
    for key in ("intravenous", "intramuscular", "subcutaneous", "oral", "inhal", "topical", "transdermal",
                "ophthalmic", "nasal", "rectal", "sublingual"):
        if key in text:
            return {"inhal": "inhalation"}.get(key, key)
    snomed = {c for s, c, _ in _codes(cc) if "snomed" in s}
    return {"47625008": "intravenous", "78421000": "intramuscular", "34206005": "subcutaneous",
            "26643006": "oral", "37839007": "sublingual"}.get(next(iter(snomed), ""))


def _medication(mr: dict[str, Any], by_ref: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    cc = mr.get("medicationCodeableConcept") or (mr.get("medication") or {}).get("concept")
    ref = (mr.get("medicationReference") or (mr.get("medication") or {}).get("reference") or {}).get("reference")
    if not cc and ref:
        med = by_ref.get(ref) or by_ref.get(ref.lstrip("#"))
        cc = (med or {}).get("code")
    if not cc:
        return None, None
    rxcui = next((c for s, c, _ in _codes(cc) if s == RXNORM and c), None)
    return _cc_text(cc), rxcui


# Mapping ------------------------------------------------------------------------------------------


def map_fhir(payload: dict[str, Any], today: date | None = None, order_id: str | None = None) -> FhirMapping:
    today = today or datetime.now(UTC).date()
    resources = _resources(payload)
    if not resources:
        raise FhirMappingError("No FHIR resources found. Send a Bundle, a MedicationRequest, or draftOrders.")
    by_ref: dict[str, dict[str, Any]] = {}
    for r in resources:
        if r.get("id"):
            by_ref[f"{r['resourceType']}/{r['id']}"] = r
            by_ref[r["id"]] = r

    orders = [r for r in resources if r["resourceType"] == "MedicationRequest"
              and r.get("status", "draft") in {"draft", "active", "on-hold"}]
    if order_id:
        orders = [r for r in orders if r.get("id") == order_id] or orders
    drafts = [r for r in orders if r.get("status") == "draft"]
    order = (drafts or orders or [None])[0]
    if order is None:
        raise FhirMappingError("No MedicationRequest with status draft, active or on-hold was found.")

    m = FhirMapping(request=None, order_id=order.get("id"))

    def note(field_name: str, value: Any, source: str) -> None:
        m.mapped.append({"field": field_name, "value": str(value), "source": source})

    drug, rxcui = _medication(order, by_ref)
    if not drug:
        raise FhirMappingError("The MedicationRequest has no medication text or coding display to resolve.")
    note("drug", drug, "MedicationRequest.medication")
    if rxcui:
        note("rxcui", rxcui, "MedicationRequest.medication RxNorm coding")

    route = dose = None
    dosage = (order.get("dosageInstruction") or [{}])[0]
    route = _route_text(dosage.get("route"))
    if route:
        note("route", route, "dosageInstruction.route")
    dq = ((dosage.get("doseAndRate") or [{}])[0]).get("doseQuantity") or {}
    if dq.get("value") is not None:
        unit = str(dq.get("code") or dq.get("unit") or "mg").lower()
        unit = {"milligram": "mg", "microgram": "mcg", "ug": "mcg", "gram": "g"}.get(unit, unit)
        if unit in {"mg", "mcg", "g"}:
            dose = DoseInput(amount=float(dq["value"]), unit=unit, frequency=frequency_text(dosage.get("timing")))
            note("current_dose", f"{dose.amount:g} {dose.unit} {dose.frequency or ''}".strip(),
                 "dosageInstruction.doseAndRate.doseQuantity and timing")
        else:
            m.unmapped.append(f"dose unit '{unit}' (only mg, mcg and g convert)")
    elif dosage.get("text"):
        m.unmapped.append(f"free text dosage '{dosage['text'][:80]}' (structured doseQuantity needed for conversion)")

    p: dict[str, Any] = {}
    patient = next((r for r in resources if r["resourceType"] == "Patient"), None)
    if patient:
        if patient.get("birthDate"):
            age = _age(patient["birthDate"], today)
            if age is not None:
                p["age_years"] = age
                note("age_years", age, "Patient.birthDate")
        g = (patient.get("gender") or "").lower()
        if g in {"female", "male"}:
            p["sex"] = Sex(g)
            note("sex", g, "Patient.gender (used only by renal equations)")

    observations = sorted((r for r in resources if r["resourceType"] == "Observation"
                           and r.get("status", "final") in {"final", "amended", "corrected", "preliminary"}),
                          key=_when, reverse=True)
    taken: set[str] = set()
    for obs in observations:
        loinc = {c for s, c, _ in _codes(obs.get("code")) if s == LOINC or not s}
        vq = obs.get("valueQuantity") or {}
        val = vq.get("value")
        unit = str(vq.get("code") or vq.get("unit") or "")
        when = _when(obs)[:10]
        src = f"Observation {next(iter(loinc), '?')} {when}".strip()
        if loinc & (SCR_MG_DL | SCR_UMOL_L) and val is not None and "scr" not in taken:
            umol = bool(loinc & SCR_UMOL_L) or "mol" in unit.lower()
            p["serum_creatinine"] = float(val)
            p["serum_creatinine_unit"] = CreatinineUnit.UMOL_L if umol else CreatinineUnit.MG_DL
            taken.add("scr")
            note("serum_creatinine", f"{val} {'umol/L' if umol else 'mg/dL'}", src)
        elif loinc & EGFR and val is not None and "egfr" not in taken:
            p["egfr_ml_min_1_73m2"] = float(val)
            taken.add("egfr")
            note("egfr_ml_min_1_73m2", val, src)
        elif loinc & CRCL and val is not None and "crcl" not in taken:
            p["creatinine_clearance_ml_min"] = float(val)
            taken.add("crcl")
            note("creatinine_clearance_ml_min", val, src)
        elif loinc & WEIGHT and val is not None and "wt" not in taken:
            kg = float(val) * (0.453592 if unit.lower() in {"lb", "[lb_av]"} else 1.0)
            p["weight_kg"] = round(kg, 1)
            taken.add("wt")
            note("weight_kg", p["weight_kg"], src)
        elif loinc & HEIGHT and val is not None and "ht" not in taken:
            cm = float(val) * (2.54 if unit.lower() in {"in", "[in_i]"} else (100.0 if unit == "m" else 1.0))
            p["height_cm"] = round(cm, 1)
            taken.add("ht")
            note("height_cm", p["height_cm"], src)
        elif loinc & PREGNANCY_STATUS and "preg" not in taken:
            answers = {c for _, c, _ in _codes(obs.get("valueCodeableConcept"))}
            p["pregnant"] = bool(answers & PREGNANT_ANSWERS)
            taken.add("preg")
            note("pregnant", p["pregnant"], src)

    for cond in (r for r in resources if r["resourceType"] == "Condition"):
        status = {c for _, c, _ in _codes(cond.get("clinicalStatus"))}
        if status and not status & {"active", "recurrence", "relapse"}:
            continue
        codes = {c for _, c, _ in _codes(cond.get("code"))}
        if codes & PREGNANCY_CONDITIONS and "preg" not in taken:
            p["pregnant"] = True
            taken.add("preg")
            note("pregnant", True, "Condition pregnancy")
        if codes & DIALYSIS_CONDITIONS:
            p["on_dialysis"] = True
            note("on_dialysis", True, "Condition dialysis dependence")

    allergies: list[str] = []
    for a in (r for r in resources if r["resourceType"] == "AllergyIntolerance"):
        verification = {c for _, c, _ in _codes(a.get("verificationStatus"))}
        clinical = {c for _, c, _ in _codes(a.get("clinicalStatus"))}
        if verification & {"refuted", "entered-in-error"} or (clinical and not clinical & {"active"}):
            continue
        if a.get("category") and "medication" not in a["category"]:
            continue
        text = _cc_text(a.get("code"))
        if text and text.lower() not in {x.lower() for x in allergies}:
            allergies.append(text)
    if allergies:
        p["allergies"] = allergies
        note("allergies", ", ".join(allergies), "AllergyIntolerance")

    meds: list[str] = []
    for r in resources:
        if r is order or r["resourceType"] not in {"MedicationRequest", "MedicationStatement"}:
            continue
        if r.get("status") not in {"active", "intended", "on-hold", None}:
            continue
        text, _ = _medication(r, by_ref)
        if text and text.lower() not in {x.lower() for x in meds} and text.lower() != drug.lower():
            meds.append(text)
    if meds:
        p["current_medications"] = meds
        note("current_medications", ", ".join(meds), "MedicationRequest/MedicationStatement (active)")

    try:
        patient_ctx = PatientContext(**p)
    except ValueError as exc:
        m.warnings.append(f"Patient values were rejected by validation: {exc}")
        patient_ctx = PatientContext()
    reason = _cc_text((order.get("reasonCode") or [None])[0]) if order.get("reasonCode") else None
    m.request = SubstitutionRequest(
        drug=re.sub(r"\s+", " ", drug).strip()[:200], rxcui=rxcui, route=route, current_dose=dose,
        patient=patient_ctx, clinical_question=f"Indication: {reason}" if reason else None,
    )
    if not patient:
        m.warnings.append("No Patient resource: age and sex were not mapped.")
    if "scr" not in taken and "egfr" not in taken and "crcl" not in taken and not p.get("on_dialysis"):
        m.warnings.append("No creatinine, eGFR or clearance observation: renal checks will show Needs data.")
    return m
