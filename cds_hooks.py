"""CDS Hooks 2.0 service: shortage alerts inside the prescriber's ordering workflow.

Two services are published at ``GET /cds-services``:

* ``shortfall-bridge-order-select`` on the ``order-select`` hook, fired while a medicine is being
  chosen, and
* ``shortfall-bridge-order-sign`` on ``order-sign``, fired before the order is signed.

When a draft MedicationRequest names a drug that is in current FDA shortage, the service runs the
full engine with the patient context from the hook prefetch and returns one card: the shortage,
the top ranked safe options with doses and cautions, and a suggestion that replaces the draft
order with the top option when a dose conversion exists. Drugs that are not short return no card,
because alert fatigue is itself a patient safety hazard. Every card links to its audit record.

Specification: https://cds-hooks.hl7.org/2.0/
"""

from __future__ import annotations

import uuid
from typing import Any

from ..domain.enums import GateStatus, RecommendationStatus
from ..domain.models import Candidate, Recommendation
from ..engine.service import SubstitutionService
from ..observability import CDS_CARDS
from .fhir import FhirMappingError, map_fhir

SERVICE_IDS = {"shortfall-bridge-order-select": "order-select", "shortfall-bridge-order-sign": "order-sign"}

PREFETCH = {
    "patient": "Patient/{{context.patientId}}",
    "medications": "MedicationRequest?patient={{context.patientId}}&status=active",
    "allergies": "AllergyIntolerance?patient={{context.patientId}}&clinical-status=active",
    "renal": "Observation?patient={{context.patientId}}&code=http://loinc.org|2160-0,http://loinc.org|14682-9,"
             "http://loinc.org|33914-3,http://loinc.org|98979-8,http://loinc.org|2164-2&_sort=-date&_count=10",
    "body": "Observation?patient={{context.patientId}}&code=http://loinc.org|29463-7,http://loinc.org|8302-2"
            "&_sort=-date&_count=4",
}


def discovery() -> dict[str, Any]:
    return {"services": [
        {
            "hook": hook,
            "id": sid,
            "title": "Shortfall Bridge drug shortage check",
            "description": ("Warns when an ordered medicine is in current FDA shortage and proposes ranked, safety "
                            "checked alternatives with label citations and starting doses."),
            "prefetch": PREFETCH,
            "usageRequirements": "Decision support only. Verify against the current label and the patient record.",
        }
        for sid, hook in SERVICE_IDS.items()
    ]}


def _flags(c: Candidate) -> str:
    names = {"availability": "supply", "pregnancy_lactation": "pregnancy", "boxed_warning": "boxed warning"}
    items = [f"{names.get(g.gate.value, g.gate.value)} {('needs data' if g.status is GateStatus.UNKNOWN else g.status.value)}"
             for g in c.gates if g.status is not GateStatus.PASS]
    return ", ".join(items) if items else "all checks clear"


def _detail(rec: Recommendation) -> str:
    lines = []
    s = rec.shortage
    if s and s.in_shortage:
        lines.append(f"**{s.matching_records} current FDA shortage entr{'y' if s.matching_records == 1 else 'ies'}**"
                     + (f", reason: {'; '.join(s.reasons[:2])}" if s.reasons else "") + ".")
    if rec.candidates:
        lines.append("")
        lines.append("| # | Option | Starting dose | Checks |")
        lines.append("|---|---|---|---|")
        for i, c in enumerate(rec.candidates[:3], 1):
            dc = c.dose_conversion
            dose = dc.suggested_dose if dc and dc.feasible and dc.suggested_dose else "no automatic dose"
            lines.append(f"| {i} | {c.display_name} ({c.tier.label.lower()}) | {dose} | {_flags(c)} |")
    if rec.not_recommended:
        lines.append("")
        lines.append("Not recommended for this patient: " + ", ".join(c.display_name for c in rec.not_recommended[:4]) + ".")
    lines.append("")
    lines.append(f"_{rec.disclaimer}_ Reference {rec.request_id}.")
    return "\n".join(lines)


def _replacement(order: dict[str, Any] | None, c: Candidate) -> dict[str, Any] | None:
    dc = c.dose_conversion
    if not order or not dc or not dc.feasible or not dc.suggested_dose:
        return None
    resource: dict[str, Any] = {
        "resourceType": "MedicationRequest",
        "id": f"sfb-{uuid.uuid4().hex[:12]}",
        "status": "draft",
        "intent": order.get("intent", "order"),
        "medicationCodeableConcept": {"text": c.ingredient},
        "dosageInstruction": [{"text": f"{dc.suggested_dose}" + (f" {dc.to_route}" if dc.to_route else "")}],
        "note": [{"text": f"Shortage substitution for {dc.from_drug} {dc.from_dose or ''}. {dc.reference or ''}".strip()}],
    }
    for key in ("subject", "encounter", "requester"):
        if order.get(key):
            resource[key] = order[key]
    actions: list[dict[str, Any]] = [{"type": "create", "description": f"Order {c.ingredient} {dc.suggested_dose}",
                                      "resource": resource}]
    if order.get("id"):
        actions.insert(0, {"type": "delete", "description": "Remove the order for the drug in shortage",
                           "resourceId": [f"MedicationRequest/{order['id']}"]})
    return {"label": f"Replace with {c.ingredient} {dc.suggested_dose}", "uuid": str(uuid.uuid4()),
            "isRecommended": False, "actions": actions}


def handle_hook(service_id: str, body: dict[str, Any], service: SubstitutionService, app_url: str | None = None,
                always_card: bool = False) -> dict[str, Any]:
    hook = SERVICE_IDS.get(service_id)
    if hook is None:
        raise KeyError(service_id)
    context = body.get("context") or {}
    payload = {"draftOrders": context.get("draftOrders"), "prefetch": body.get("prefetch") or {}}
    selections = context.get("selections") or []
    order_id = selections[0].split("/", 1)[-1] if selections else None
    try:
        mapping = map_fhir(payload, order_id=order_id)
    except FhirMappingError:
        return {"cards": []}
    assert mapping.request is not None
    order = next((r for r in (context.get("draftOrders") or {}).get("entry", [])
                  if (r.get("resource") or {}).get("id") == mapping.order_id), {}).get("resource")

    rec = service.recommend(mapping.request)
    if not (rec.shortage and rec.shortage.in_shortage) and not always_card:
        return {"cards": []}
    name = rec.query.display_name if rec.query else mapping.request.drug
    top = rec.candidates[0] if rec.candidates else None
    if rec.status is RecommendationStatus.ABSTAIN:
        indicator, summary = "critical", f"{name} is in FDA shortage and no safe alternative was found: escalate"
    elif rec.shortage and rec.shortage.in_shortage and top:
        dose = top.dose_conversion.suggested_dose if top.dose_conversion and top.dose_conversion.feasible else None
        indicator = "warning"
        summary = f"{name} is in FDA shortage. Top option: {top.display_name}" + (f", {dose}" if dose else "")
    else:
        indicator, summary = "info", f"{name}: no current FDA shortage"
    summary = summary[:140]
    card: dict[str, Any] = {
        "uuid": str(uuid.uuid4()),
        "summary": summary,
        "indicator": indicator,
        "detail": _detail(rec),
        "source": {"label": "Shortfall Bridge AI (FDA labels, Drug Shortages, Orange Book)",
                   "url": "https://www.accessdata.fda.gov/scripts/drugshortages/"},
        "selectionBehavior": "at-most-one",
        "suggestions": [s for s in (_replacement(order, c) for c in rec.candidates[:2]) if s],
        "overrideReasons": [
            {"code": "stock-available", "system": "https://shortfallbridge.example/override", "display": "Local stock available"},
            {"code": "clinical-judgement", "system": "https://shortfallbridge.example/override", "display": "Clinical judgement"},
        ],
    }
    if app_url:
        card["links"] = [{"label": "Open the full review with label evidence", "type": "absolute",
                          "url": f"{app_url.rstrip('/')}/#/workbench?ref={rec.request_id}"}]
    if not card["suggestions"]:
        card.pop("selectionBehavior")
    CDS_CARDS.labels(hook=hook, indicator=indicator).inc()
    return {"cards": [card]}
