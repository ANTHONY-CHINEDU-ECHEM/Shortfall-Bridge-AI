"""FHIR R4 mapping and the CDS Hooks 2.0 service."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from shortfall_bridge.api.app import create_app
from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import CreatinineUnit, Sex
from shortfall_bridge.interop.cds_hooks import discovery, handle_hook
from shortfall_bridge.interop.fhir import FhirMappingError, frequency_text, map_fhir

TODAY = date(2026, 10, 27)


def obs(code: str, value: float, unit: str, when: str = "2026-10-20", system: str = "http://loinc.org") -> dict:
    return {"resourceType": "Observation", "status": "final", "code": {"coding": [{"system": system, "code": code}]},
            "valueQuantity": {"value": value, "unit": unit}, "effectiveDateTime": when}


def order(drug: str = "Furosemide 10 mg/mL injection", status: str = "draft", oid: str = "mr1") -> dict:
    return {
        "resourceType": "MedicationRequest", "id": oid, "status": status, "intent": "order",
        "subject": {"reference": "Patient/p1"},
        "medicationCodeableConcept": {"text": drug, "coding": [
            {"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "4603", "display": "furosemide"}]},
        "dosageInstruction": [{
            "route": {"coding": [{"system": "http://snomed.info/sct", "code": "47625008", "display": "Intravenous"}]},
            "timing": {"repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}},
            "doseAndRate": [{"doseQuantity": {"value": 40, "unit": "mg", "code": "mg"}}],
        }],
    }


def bundle(*extra: dict) -> dict:
    entries = [
        order(),
        {"resourceType": "Patient", "id": "p1", "gender": "male", "birthDate": "1954-03-02"},
        obs("14682-9", 150, "umol/L", "2026-10-25"),
        obs("2160-0", 1.1, "mg/dL", "2025-01-01"),  # older value must lose to the newer one
        obs("29463-7", 176, "[lb_av]"),
        obs("8302-2", 69, "[in_i]"),
        {"resourceType": "AllergyIntolerance", "code": {"text": "Penicillin"}, "category": ["medication"],
         "clinicalStatus": {"coding": [{"code": "active"}]}},
        {"resourceType": "AllergyIntolerance", "code": {"text": "Latex"}, "category": ["environment"]},
        {"resourceType": "AllergyIntolerance", "code": {"text": "Codeine"},
         "verificationStatus": {"coding": [{"code": "refuted"}]}},
        {"resourceType": "MedicationStatement", "status": "active", "medicationCodeableConcept": {"text": "Lisinopril 10 mg"}},
        {"resourceType": "MedicationRequest", "id": "old", "status": "stopped", "medicationCodeableConcept": {"text": "Warfarin"}},
        *extra,
    ]
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in entries]}


def test_full_bundle_mapping() -> None:
    m = map_fhir(bundle(), today=TODAY)
    r = m.request
    assert r is not None and m.order_id == "mr1"
    assert r.drug == "Furosemide 10 mg/mL injection" and r.rxcui == "4603" and r.route == "intravenous"
    assert r.current_dose and r.current_dose.amount == 40 and r.current_dose.frequency == "twice daily"
    p = r.patient
    assert p.age_years == 72 and p.sex is Sex.MALE
    assert p.serum_creatinine == 150 and p.serum_creatinine_unit is CreatinineUnit.UMOL_L
    assert p.weight_kg == pytest.approx(79.8, abs=0.1) and p.height_cm == pytest.approx(175.3, abs=0.1)
    assert p.allergies == ["Penicillin"] and p.current_medications == ["Lisinopril 10 mg"]
    fields = {x["field"] for x in m.mapped}
    assert {"drug", "route", "current_dose", "age_years", "sex", "serum_creatinine", "allergies"} <= fields
    assert not m.warnings


def test_dialysis_and_pregnancy_conditions() -> None:
    m = map_fhir(bundle(
        {"resourceType": "Condition", "clinicalStatus": {"coding": [{"code": "active"}]},
         "code": {"coding": [{"system": "http://snomed.info/sct", "code": "105502003"}]}},
        {"resourceType": "Observation", "status": "final", "code": {"coding": [{"system": "http://loinc.org", "code": "82810-3"}]},
         "valueCodeableConcept": {"coding": [{"code": "LA15173-0"}]}, "effectiveDateTime": "2026-10-01"},
    ), today=TODAY)
    assert m.request.patient.on_dialysis and m.request.patient.pregnant


@pytest.mark.parametrize(
    ("timing", "expected"),
    [({"repeat": {"frequency": 1, "period": 1, "periodUnit": "d"}}, "once daily"),
     ({"repeat": {"frequency": 4, "period": 1, "periodUnit": "d"}}, "four times daily"),
     ({"repeat": {"frequency": 1, "period": 8, "periodUnit": "h"}}, "every 8 hours"),
     ({"repeat": {"frequency": 1, "period": 1, "periodUnit": "wk"}}, "weekly"),
     ({"code": {"text": "BID"}}, "BID"), (None, None)],
)
def test_timing_translation(timing: dict | None, expected: str | None) -> None:
    assert frequency_text(timing) == expected


def test_single_resource_and_contained_medication() -> None:
    mr = {"resourceType": "MedicationRequest", "status": "active",
          "contained": [{"resourceType": "Medication", "id": "med", "code": {"text": "Bumetanide 1 mg tablet"}}],
          "medicationReference": {"reference": "#med"}, "dosageInstruction": [{"text": "1 mg daily"}]}
    m = map_fhir(mr, today=TODAY)
    assert m.request.drug == "Bumetanide 1 mg tablet" and m.request.current_dose is None
    assert any("free text dosage" in u for u in m.unmapped)
    assert any("No Patient resource" in w for w in m.warnings) and any("creatinine" in w for w in m.warnings)


def test_mapping_errors() -> None:
    with pytest.raises(FhirMappingError):
        map_fhir({"resourceType": "Bundle", "entry": []})
    with pytest.raises(FhirMappingError):
        map_fhir({"resourceType": "Patient", "id": "p"})
    with pytest.raises(FhirMappingError):
        map_fhir({"resourceType": "MedicationRequest", "status": "draft"})


def test_fhir_endpoint(settings: Settings, container: Container) -> None:
    with TestClient(create_app(settings, container)) as client:
        r = client.post("/api/v1/fhir/substitutions", json=bundle())
        assert r.status_code == 200
        body = r.json()
        assert body["mapping"]["order_id"] == "mr1"
        rec = body["recommendation"]
        assert rec["query"]["display_name"] == "furosemide" and rec["renal"]["source"] == "cockcroft_gault"
        assert any(c["ingredient"] == "bumetanide" for c in rec["candidates"])
        assert client.post("/api/v1/fhir/substitutions", json={"resourceType": "Patient"}).status_code == 422


def test_cds_discovery() -> None:
    services = discovery()["services"]
    assert {s["hook"] for s in services} == {"order-select", "order-sign"}
    assert all("patient" in s["prefetch"] and "renal" in s["prefetch"] for s in services)


def _hook_body(drug: str = "Furosemide 10 mg/mL injection") -> dict:
    b = bundle()
    draft = next(e for e in b["entry"] if e["resource"]["resourceType"] == "MedicationRequest")
    draft["resource"]["medicationCodeableConcept"]["text"] = drug
    others = [e["resource"] for e in b["entry"] if e is not draft]
    return {"hook": "order-select", "hookInstance": "x", "context": {
        "userId": "Practitioner/1", "patientId": "p1", "selections": ["MedicationRequest/mr1"],
        "draftOrders": {"resourceType": "Bundle", "entry": [draft]}},
        "prefetch": {"patient": others[0], "rest": {"resourceType": "Bundle", "entry": [{"resource": r} for r in others[1:]]}}}


def test_cds_card_for_shortage_drug(container: Container) -> None:
    out = handle_hook("shortfall-bridge-order-select", _hook_body(), container.service, app_url="https://demo.example")
    card = out["cards"][0]
    assert card["indicator"] == "warning" and card["summary"].startswith("furosemide is in FDA shortage")
    assert len(card["summary"]) <= 140 and "| # | Option |" in card["detail"]
    sugg = card["suggestions"][0]
    kinds = [a["type"] for a in sugg["actions"]]
    assert kinds == ["delete", "create"] and sugg["actions"][0]["resourceId"] == ["MedicationRequest/mr1"]
    new = sugg["actions"][1]["resource"]
    assert new["status"] == "draft" and new["subject"] == {"reference": "Patient/p1"}
    assert card["links"][0]["url"].startswith("https://demo.example/#/workbench?ref=r_")


def test_cds_no_card_when_not_in_shortage(container: Container) -> None:
    out = handle_hook("shortfall-bridge-order-sign", _hook_body("Bumetanide injection"), container.service)
    assert out == {"cards": []}
    info = handle_hook("shortfall-bridge-order-sign", _hook_body("Bumetanide injection"), container.service,
                       always_card=True)
    assert info["cards"][0]["indicator"] == "info"


def test_cds_routes(settings: Settings, container: Container) -> None:
    with TestClient(create_app(settings, container)) as client:
        assert len(client.get("/cds-services").json()["services"]) == 2
        r = client.post("/cds-services/shortfall-bridge-order-select", json=_hook_body())
        assert r.status_code == 200 and r.json()["cards"]
        assert client.post("/cds-services/unknown", json=_hook_body()).status_code == 404
        assert client.post("/cds-services/shortfall-bridge-order-select", json={"context": {}}).json() == {"cards": []}
