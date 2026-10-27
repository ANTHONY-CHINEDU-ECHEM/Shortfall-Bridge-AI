import math

import pytest

from shortfall_bridge.domain.models import DoseInput
from shortfall_bridge.engine.equivalence import (
    convert,
    interchange_partners,
    is_conversion_refused,
    load_tables,
    practical_round,
    round_trip_error,
)


def test_tables_load_and_reference_sources() -> None:
    tables = load_tables()
    assert len(tables) >= 6
    for t in tables:
        assert t.reference and t.reference_url.startswith("https://")


def test_loop_diuretic_iv_furosemide_to_iv_bumetanide() -> None:
    dc = convert("furosemide", "bumetanide", DoseInput(amount=40), "intravenous", "intravenous")
    assert dc and dc.feasible and dc.suggested_dose.startswith("2 mg")


def test_furosemide_iv_to_oral_doubles_dose() -> None:
    dc = convert("furosemide", "furosemide", DoseInput(amount=40), "intravenous", "oral")
    assert dc and dc.feasible and dc.suggested_dose.startswith("80 mg")
    assert any("Route change" in w for w in dc.warnings)


def test_corticosteroid_conversion() -> None:
    dc = convert("methylprednisolone sodium succinate", "dexamethasone", DoseInput(amount=125), "intravenous", "intravenous")
    assert dc and dc.feasible and dc.suggested_dose.startswith("23 mg")


def test_opioid_rotation_applies_cross_tolerance_reduction() -> None:
    dc = convert("oxycodone", "morphine", DoseInput(amount=10, frequency="every 6 hours"), "oral", "oral")
    assert dc and dc.feasible
    assert dc.suggested_dose.startswith("7.5 mg to 11 mg")
    assert "MME" in dc.suggested_range


@pytest.mark.parametrize("drug", ["methadone", "fentanyl", "buprenorphine"])
def test_high_risk_opioids_are_refused(drug: str) -> None:
    dc = convert("morphine", drug, DoseInput(amount=30), "oral", "oral")
    assert dc is not None and not dc.feasible and dc.method.startswith("refused")
    assert is_conversion_refused(drug)


def test_oral_benzodiazepine_table_is_not_extrapolated_to_iv() -> None:
    dc = convert("lorazepam", "diazepam", DoseInput(amount=2), "intravenous", "intravenous")
    assert dc and not dc.feasible and "route" in dc.method


def test_statin_band_conversion() -> None:
    dc = convert("atorvastatin", "rosuvastatin", DoseInput(amount=40), "oral", "oral")
    assert dc and dc.feasible and dc.suggested_dose.startswith("20 to 40 mg")
    dc2 = convert("rosuvastatin", "pitavastatin", DoseInput(amount=20), "oral", "oral")
    assert dc2 and not dc2.feasible and "band" in dc2.method


def test_no_table_returns_none() -> None:
    assert convert("furosemide", "atorvastatin", DoseInput(amount=40)) is None


def test_missing_dose_is_explicit() -> None:
    dc = convert("furosemide", "bumetanide", None)
    assert dc and not dc.feasible and dc.method.endswith("no_input_dose")


@pytest.mark.parametrize(("x", "y"), [(0.3, 0.25), (1.6, 1.5), (7.3, 7.5), (23.4, 23), (113, 115)])
def test_practical_round(x: float, y: float) -> None:
    assert practical_round(x) == y


@pytest.mark.parametrize(("a", "b", "amount"), [("prednisone", "dexamethasone", 40), ("omeprazole", "pantoprazole", 20)])
def test_round_trip_stable(a: str, b: str, amount: float) -> None:
    err = round_trip_error(a, b, amount)
    assert not math.isnan(err) and err <= 0.05


def test_interchange_partners() -> None:
    assert "bumetanide" in interchange_partners("furosemide")
    assert "methadone" not in interchange_partners("morphine")
