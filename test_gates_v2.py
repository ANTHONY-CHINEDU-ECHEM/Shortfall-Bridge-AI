"""v2 gate and ranking behaviour: supply scope, allergy fixes, duplicate therapy, explainable scores."""

import pytest

from shortfall_bridge.api.container import Container
from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import CandidateTier, GateName, GateStatus
from shortfall_bridge.domain.models import DoseInput, PatientContext, ShortageRecord, SubstitutionRequest
from shortfall_bridge.engine.gates import SUPPLY_MARKET, SUPPLY_OTHER_ROUTE, shortage_route_families
from shortfall_bridge.engine.scoring import ROUTE_MISMATCH_PENALTY, score_candidate

from .conftest import build_container, build_store, make_label


def _gate(c, name: GateName):
    return next(g for g in c.gates if g.gate is name)


@pytest.fixture(scope="module")
def furosemide_iv(container: Container):
    return container.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", current_dose=DoseInput(amount=40, frequency="twice daily")),
        audit=False)


def test_oral_formulation_not_penalised_for_injection_shortage(furosemide_iv) -> None:
    oral = [c for c in furosemide_iv.candidates if c.tier is CandidateTier.ALTERNATIVE_FORMULATION]
    assert oral
    for c in oral:
        g = _gate(c, GateName.AVAILABILITY)
        assert g.status is GateStatus.PASS and g.facts["supply_scope"] == SUPPLY_OTHER_ROUTE
        assert "parenteral route only" in g.rationale and "oral" in g.rationale
        assert all(t.term not in {"supply_same_route", "supply_market"} for t in c.score_breakdown)


def test_t1_supply_is_market_scoped(furosemide_iv) -> None:
    t1 = next(c for c in furosemide_iv.candidates if c.tier is CandidateTier.THERAPEUTIC_EQUIVALENT)
    g = _gate(t1, GateName.AVAILABILITY)
    assert g.status is GateStatus.CAUTION and g.facts["supply_scope"] == SUPPLY_MARKET
    assert "Hospira" in g.rationale and ".." not in g.rationale
    assert any(t.term == "supply_market" and t.value == -0.10 for t in t1.score_breakdown)
    assert t1.manufacturers and all("HOSPIRA" not in m.upper() for m in t1.manufacturers)


def test_t1_still_ranks_first(furosemide_iv) -> None:
    assert furosemide_iv.candidates[0].tier is CandidateTier.THERAPEUTIC_EQUIVALENT


def test_route_mismatch_penalty_and_ordering(furosemide_iv) -> None:
    by = {c.ingredient: c for c in furosemide_iv.candidates if c.tier is CandidateTier.THERAPEUTIC_INTERCHANGE}
    bum, tor = by["bumetanide"], by["torsemide"]
    assert bum.route_match is True and tor.route_match is False and tor.routes_available == ["oral"]
    assert any(t.term == "route_mismatch" and t.value == -ROUTE_MISMATCH_PENALTY for t in tor.score_breakdown)
    order = [c.ingredient for c in furosemide_iv.candidates]
    assert order.index("bumetanide") < order.index("torsemide")
    # the conversion targets the route torsemide is actually available by
    assert tor.dose_conversion and tor.dose_conversion.to_route == "oral"
    assert tor.dose_conversion.warnings[0].startswith("Route change from intravenous to oral")


def test_score_breakdown_sums_to_score(furosemide_iv) -> None:
    for c in furosemide_iv.candidates + furosemide_iv.not_recommended:
        assert c.score_breakdown and c.score_breakdown[0].term == "tier"
        assert c.score == pytest.approx(max(0.0, sum(t.value for t in c.score_breakdown)), abs=1e-4)
        assert c.score == score_candidate(c)


def test_shortage_route_family_inference_without_openfda_block() -> None:
    r = ShortageRecord(record_id="s", generic_name="Amoxicillin Oral Powder for Suspension",
                       dosage_form="Powder, for suspension")
    assert shortage_route_families(r) == {"oral"}
    r2 = ShortageRecord(record_id="s2", generic_name="Furosemide Injection", dosage_form="Injection, solution")
    assert shortage_route_families(r2) == {"parenteral"}


def test_replaced_drug_is_not_flagged_as_duplicate(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", patient=PatientContext(current_medications=["furosemide"])),
        audit=False)
    for c in rec.candidates:
        g = _gate(c, GateName.INTERACTIONS)
        assert g.status is GateStatus.PASS and "being replaced" in g.rationale


def test_same_class_duplicate_therapy(container: Container) -> None:
    rec = container.service.recommend(SubstitutionRequest(
        drug="atorvastatin", route="oral", patient=PatientContext(current_medications=["simvastatin"])), audit=False)
    ros = next(c for c in rec.candidates + rec.not_recommended if c.ingredient == "rosuvastatin")
    g = _gate(ros, GateName.INTERACTIONS)
    assert g.status in (GateStatus.CAUTION, GateStatus.BLOCK)
    assert g.facts["duplicate_therapy"] == ["simvastatin"] and "duplicate therapy" in g.rationale


def test_sulfa_allergen_does_not_corrupt_drug_names(container: Container) -> None:
    # v1 rewrote 'sulfamethoxazole' into 'sulfonamide antibacterialmethoxazole'; it must match nothing here
    rec = container.service.recommend(SubstitutionRequest(
        drug="atorvastatin", route="oral", patient=PatientContext(allergies=["sulfamethoxazole"])), audit=False)
    for c in rec.candidates:
        assert _gate(c, GateName.ALLERGY).status is GateStatus.PASS


@pytest.fixture(scope="module")
def sulfa_container(settings: Settings) -> Container:
    records = [
        make_label("fx-f", "furosemide", {"indications_and_usage": "Edema.",
                                          "dosage_and_administration": "20 to 40 mg."},
                   epc="Loop Diuretic [EPC]", route="INTRAVENOUS"),
        make_label("fx-b", "bumetanide", {
            "indications_and_usage": "Edema.",
            "warnings_and_cautions": "Patients allergic to sulfonamides may show hypersensitivity to bumetanide.",
        }, epc="Loop Diuretic [EPC]", route="INTRAVENOUS"),
        make_label("fx-s", "sulfamethoxazole", {"indications_and_usage": "Urinary tract infections."},
                   epc="Sulfonamide Antibacterial [EPC]"),
        make_label("fx-t", "trimethoprim", {
            "indications_and_usage": "Urinary tract infections.",
            "contraindications": "Contraindicated in patients with known hypersensitivity to sulfonamides.",
        }, epc="Dihydrofolate Reductase Inhibitor Antibacterial [EPC]"),
    ]
    return build_container(settings, build_store(settings, records, shortages=[]))


@pytest.mark.parametrize("allergen", ["sulfa", "Sulfa drugs", "sulfonamides", "sulpha"])
def test_sulfa_class_allergy_warns_from_label_text(sulfa_container: Container, allergen: str) -> None:
    rec = sulfa_container.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", patient=PatientContext(allergies=[allergen])), audit=False)
    bum = next(c for c in rec.candidates + rec.not_recommended if c.ingredient == "bumetanide")
    g = _gate(bum, GateName.ALLERGY)
    assert g.status is GateStatus.CAUTION and g.citations and "hypersensitivity" in g.rationale
    assert g.facts["matched_allergens"] == [allergen]


def test_class_allergy_blocks_members_of_that_class(sulfa_container: Container) -> None:
    from shortfall_bridge.engine.gates import GateContext, GateEvaluator

    ev = GateEvaluator(GateContext(sulfa_container.store, PatientContext(allergies=["sulfa"]), "oral"))
    assert ev.allergy("sulfamethoxazole").status is GateStatus.BLOCK
    contra = ev.allergy("trimethoprim")
    assert contra.status is GateStatus.BLOCK and contra.citations  # plural "sulfonamides" in label text


def test_gate_evaluator_still_accepts_plain_ingredient(container: Container) -> None:
    from shortfall_bridge.engine.gates import GateContext, GateEvaluator

    gates = GateEvaluator(GateContext(container.store, PatientContext(), "intravenous")).evaluate("bumetanide")
    assert len(gates) == 8
