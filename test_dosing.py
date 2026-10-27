import pytest

from shortfall_bridge.clinical.dosing import doses_per_day, extract_max_daily_doses, fmt_mg, parse_dose_mg
from shortfall_bridge.config import Settings
from shortfall_bridge.domain.enums import SectionType
from shortfall_bridge.domain.models import Chunk, DoseInput, SubstitutionRequest

from .conftest import build_container, build_store, make_label


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("once daily", 1), ("daily", 1), ("OD", 1), ("nocte", 1), ("at bedtime", 1),
        ("twice daily", 2), ("BID", 2), ("bd", 2), ("every 12 hours", 2), ("q12h", 2), ("2x daily", 2),
        ("three times a day", 3), ("tds", 3), ("q8h", 3), ("every 8 hours", 3),
        ("four times daily", 4), ("QID", 4), ("every 6 hours", 4), ("q4h", 6),
        ("every other day", 0.5), ("once weekly", 1 / 7),
        ("every 4 to 6 hours as needed", None), ("prn", None), ("with meals", None), (None, None), ("", None),
    ],
)
def test_doses_per_day(text: str | None, expected: float | None) -> None:
    got = doses_per_day(text)
    assert (got is None and expected is None) or got == pytest.approx(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("2 mg", (2, 2)), ("7.5 mg to 11 mg every 6 hours", (7.5, 11)), ("20 to 40 mg daily", (20, 40)),
     ("0.5 g", (500, 500)), ("250 mcg", (0.25, 0.25)), ("no dose", None), (None, None)],
)
def test_parse_dose_mg(text: str | None, expected: tuple[float, float] | None) -> None:
    assert parse_dose_mg(text) == (pytest.approx(expected) if expected else None)


def test_fmt_mg() -> None:
    assert fmt_mg(2000) == "2 g" and fmt_mg(0.25) == "250 mcg" and fmt_mg(7.5) == "7.5 mg"


def _ch(text: str) -> Chunk:
    return Chunk(chunk_id="c1", set_id="s", section=SectionType.DOSAGE_AND_ADMINISTRATION, ordinal=0, text=text)


def test_extract_max_daily_doses_patterns_and_exclusions() -> None:
    text = (
        "The maximum recommended daily dose is 10 mg. Do not exceed 600 mg per day. "
        "Doses up to 2 mg/kg per day may be used in children. The maximum single dose is 40 mg. "
        "May be increased up to 4 g per day in divided doses."
    )
    maxima = extract_max_daily_doses([_ch(text)])
    assert [m.mg_per_day for m in maxima] == [4000, 600, 10]
    assert all("mg/kg" not in m.sentence for m in maxima)


def _loop_store(settings: Settings, bumetanide_dosing: str):
    records = [
        make_label("fx-furo", "furosemide", {"indications_and_usage": "Edema associated with heart failure.",
                                             "dosage_and_administration": "20 to 40 mg given as a single dose."},
                   epc="Loop Diuretic [EPC]", route="INTRAVENOUS"),
        make_label("fx-bum", "bumetanide", {"indications_and_usage": "Edema associated with heart failure.",
                                            "dosage_and_administration": bumetanide_dosing},
                   epc="Loop Diuretic [EPC]", route="INTRAVENOUS"),
    ]
    return build_container(settings, build_store(settings, records, shortages=[]))


def test_ceiling_flags_conversion_above_label_maximum(settings: Settings) -> None:
    c = _loop_store(settings, "The usual dose is 0.5 to 1 mg. Do not exceed 6 mg per day.")
    rec = c.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", current_dose=DoseInput(amount=40, frequency="four times daily")),
        audit=False)
    bum = next(x for x in rec.candidates + rec.not_recommended if x.ingredient == "bumetanide")
    dc = bum.dose_conversion
    assert dc and dc.feasible and dc.suggested_dose.startswith("2 mg")
    assert dc.doses_per_day == 4 and dc.suggested_daily_total == "8 mg"
    assert dc.label_max_daily == "6 mg per day" and dc.ceiling_exceeded
    assert dc.warnings[0].startswith("Suggested daily total (8 mg) exceeds")
    assert dc.ceiling_citations and dc.ceiling_citations[0] in bum.citations
    terms = {t.term for t in bum.score_breakdown}
    assert "dose_ceiling" in terms and "dose_available" not in terms
    assert any("exceeds the labelled maximum" in s.text for s in rec.narrative.sentences)


def test_ceiling_within_label_maximum(settings: Settings) -> None:
    c = _loop_store(settings, "The maximum daily dose is 10 mg.")
    rec = c.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", current_dose=DoseInput(amount=40, frequency="twice daily")), audit=False)
    dc = next(x for x in rec.candidates if x.ingredient == "bumetanide").dose_conversion
    assert dc and dc.suggested_daily_total == "4 mg" and dc.label_max_daily == "10 mg per day"
    assert not dc.ceiling_exceeded


def test_ceiling_not_checked_for_as_needed(settings: Settings) -> None:
    c = _loop_store(settings, "Do not exceed 6 mg per day.")
    rec = c.service.recommend(SubstitutionRequest(
        drug="furosemide", route="intravenous", current_dose=DoseInput(amount=40, frequency="as needed")), audit=False)
    dc = next(x for x in rec.candidates if x.ingredient == "bumetanide").dose_conversion
    assert dc and dc.doses_per_day is None and not dc.ceiling_exceeded
    assert any("no fixed daily total" in w for w in dc.warnings)
