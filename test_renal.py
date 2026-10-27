import math

import pytest
from pydantic import ValidationError

from shortfall_bridge.api.container import Container
from shortfall_bridge.clinical.renal import (
    choose_weight,
    ckd_epi_2021,
    cockcroft_gault,
    creatinine_mg_dl,
    estimate_renal_function,
    ideal_body_weight,
    with_estimated_renal_function,
)
from shortfall_bridge.domain.enums import CreatinineUnit, GateName, GateStatus, Sex
from shortfall_bridge.domain.models import PatientContext, SubstitutionRequest


def test_cockcroft_gault_reference_values() -> None:
    # (140 - 72) x 70 / (72 x 1.0) = 66.1 for a man; female factor 0.85
    assert cockcroft_gault(72, 70, 1.0, female=False) == pytest.approx(66.11, abs=0.01)
    assert cockcroft_gault(72, 70, 1.0, female=True) == pytest.approx(66.11 * 0.85, abs=0.01)


def test_ckd_epi_2021_published_example() -> None:
    # 50 year old man with SCr 1.0 mg/dL: about 92 mL/min/1.73 m2 in the 2021 race free equation
    assert ckd_epi_2021(50, 1.0, female=False) == pytest.approx(91.7, abs=0.5)
    # female branch uses kappa 0.7, alpha -0.241 and the 1.012 multiplier
    assert ckd_epi_2021(50, 0.7, female=True) == pytest.approx(142 * 0.9938**50 * 1.012, rel=1e-6)


def test_unit_conversion_and_ibw() -> None:
    assert creatinine_mg_dl(88.42, CreatinineUnit.UMOL_L) == pytest.approx(1.0)
    assert ideal_body_weight(152.4, Sex.MALE) == pytest.approx(50.0)
    assert ideal_body_weight(177.8, Sex.FEMALE) == pytest.approx(45.5 + 2.3 * 10, abs=0.01)


@pytest.mark.parametrize(
    ("weight", "expect_word"),
    [(55, "actual"), (72, "ideal"), (120, "adjusted")],
)
def test_weight_selection(weight: float, expect_word: str) -> None:
    _, note, warnings = choose_weight(weight, 175, Sex.MALE)
    assert note.startswith(expect_word) and not warnings


def test_weight_without_height_warns() -> None:
    w, note, warnings = choose_weight(110, None, Sex.MALE)
    assert w == 110 and "actual" in note and "overestimates" in warnings[0]


def test_estimate_priorities() -> None:
    assert estimate_renal_function(PatientContext()).source == "none"
    assert estimate_renal_function(PatientContext(creatinine_clearance_ml_min=33, serum_creatinine=2.0,
                                                  age_years=70)).source == "entered_crcl"
    e = estimate_renal_function(PatientContext(egfr_ml_min_1_73m2=40))
    assert e.source == "entered_egfr" and "1.73" in e.warnings[0]
    d = estimate_renal_function(PatientContext(on_dialysis=True, creatinine_clearance_ml_min=60))
    assert d.source == "dialysis" and d.value_ml_min == 5


def test_estimate_cockcroft_gault_full_record() -> None:
    p = PatientContext(age_years=72, weight_kg=80, height_cm=175, sex=Sex.MALE, serum_creatinine=150,
                       serum_creatinine_unit=CreatinineUnit.UMOL_L)
    e = estimate_renal_function(p)
    assert e.source == "cockcroft_gault" and e.value_ml_min == pytest.approx(39.2, abs=0.1)
    assert "ideal body weight" in (e.weight_used or "") and e.egfr_ckd_epi_2021 is not None
    assert with_estimated_renal_function(p, e).renal_function == e.value_ml_min


def test_estimate_unknown_sex_is_conservative() -> None:
    base = dict(age_years=60, weight_kg=70, serum_creatinine=1.2)
    unknown = estimate_renal_function(PatientContext(**base))
    male = estimate_renal_function(PatientContext(**base, sex=Sex.MALE))
    assert unknown.value_ml_min < male.value_ml_min and any("cautious" in w for w in unknown.warnings)


def test_estimate_without_weight_uses_ckd_epi() -> None:
    e = estimate_renal_function(PatientContext(age_years=50, sex=Sex.MALE, serum_creatinine=1.0))
    assert e.source == "ckd_epi_2021" and e.value_ml_min == pytest.approx(91.7, abs=0.5)


def test_paediatric_uses_bedside_schwartz() -> None:
    e = estimate_renal_function(PatientContext(age_years=8, height_cm=128, serum_creatinine=0.5))
    assert e.source == "bedside_schwartz" and e.value_ml_min == pytest.approx(0.413 * 128 / 0.5, abs=0.1)
    assert estimate_renal_function(PatientContext(age_years=8, serum_creatinine=0.5)).value_ml_min is None


def test_older_adult_low_creatinine_warning() -> None:
    e = estimate_renal_function(PatientContext(age_years=88, weight_kg=50, sex=Sex.FEMALE, serum_creatinine=0.5))
    assert any("muscle mass" in w for w in e.warnings)


def test_implausible_creatinine_unit_rejected() -> None:
    with pytest.raises(ValidationError):
        PatientContext(serum_creatinine=150)  # clearly umol/L entered as mg/dL


def test_engine_uses_estimate_for_renal_gate(container: Container) -> None:
    unknown = container.service.recommend(SubstitutionRequest(drug="atorvastatin", route="oral"), audit=False)
    ros = next(c for c in unknown.candidates if c.ingredient == "rosuvastatin")
    assert next(g for g in ros.gates if g.gate is GateName.RENAL).status is GateStatus.UNKNOWN

    rec = container.service.recommend(SubstitutionRequest(
        drug="atorvastatin", route="oral",
        patient=PatientContext(age_years=84, weight_kg=52, height_cm=160, sex=Sex.FEMALE, serum_creatinine=2.4),
    ), audit=False)
    assert rec.renal and rec.renal.source == "cockcroft_gault" and rec.renal.value_ml_min < 30
    ros = next(c for c in rec.candidates + rec.not_recommended if c.ingredient == "rosuvastatin")
    renal = next(g for g in ros.gates if g.gate is GateName.RENAL)
    assert renal.status in (GateStatus.CAUTION, GateStatus.BLOCK)
    assert math.isclose(renal.facts["renal_function"], rec.renal.value_ml_min)
    assert any("Cockcroft-Gault" in w for w in rec.warnings)
    assert any("Renal function used" in s.text for s in rec.narrative.sentences)
