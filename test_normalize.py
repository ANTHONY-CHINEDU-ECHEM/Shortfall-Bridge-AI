import pytest

from shortfall_bridge.domain.enums import ShortageStatus
from shortfall_bridge.domain.normalize import (
    active_moiety,
    ingredients_from_shortage_name,
    normalize_dosage_form,
    parse_strength,
    route_family,
    same_strength,
    split_ingredients,
    split_orange_book_form_route,
    word_boundary_contains,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Methylprednisolone Sodium Succinate", "methylprednisolone"),
        ("ATORVASTATIN CALCIUM TRIHYDRATE", "atorvastatin"),
        ("Oxycodone Hydrochloride", "oxycodone"),
        ("Doxycycline Hyclate", "doxycycline"),
        ("furosemide", "furosemide"),
        ("Sodium Bicarbonate", "bicarbonate"),
    ],
)
def test_active_moiety(raw: str, expected: str) -> None:
    assert active_moiety(raw) == expected


def test_split_ingredients_handles_combinations() -> None:
    assert split_ingredients("AMLODIPINE BESYLATE; BENAZEPRIL HYDROCHLORIDE") == [
        "amlodipine besylate", "benazepril hydrochloride",
    ]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Amoxicillin Oral Powder for Suspension", ["amoxicillin"]),
        ("Furosemide Injection", ["furosemide"]),
        ("Heparin Sodium Injection", ["heparin"]),
        ("Dextrose 50% Injection", ["dextrose"]),
    ],
)
def test_shortage_names(name: str, expected: list[str]) -> None:
    assert ingredients_from_shortage_name(name) == expected


def test_route_families() -> None:
    assert route_family("INTRAVENOUS") == "parenteral"
    assert route_family("injection") == "parenteral"
    assert route_family("ORAL") == "oral"
    assert route_family("sublingual") == "oromucosal"
    assert route_family(None) is None


def test_dosage_form_release_flag() -> None:
    er = normalize_dosage_form("TABLET, EXTENDED RELEASE")
    ir = normalize_dosage_form("Tablet, film coated")
    assert er and er.base == "tablet" and er.modified_release
    assert ir and ir.base == "tablet" and not ir.modified_release
    assert normalize_dosage_form("INJECTABLE").base == "injection"


def test_strength_equivalence_across_units() -> None:
    assert same_strength("1 g", "1000MG")
    assert same_strength("10MG/ML", "10 mg/mL")
    assert same_strength("40 mg/4 mL", "10 mg/mL")
    assert not same_strength("20MG", "40MG")
    assert parse_strength("EQ 5MG BASE").value == 5


def test_orange_book_form_route() -> None:
    assert split_orange_book_form_route("TABLET, FILM COATED;ORAL") == ("TABLET, FILM COATED", "ORAL")


def test_word_boundary() -> None:
    assert word_boundary_contains("Avoid use with clarithromycin.", "clarithromycin")
    assert not word_boundary_contains("erythromycins are", "mycin")
    assert not word_boundary_contains("x", "ab")


def test_shortage_status_parsing() -> None:
    assert ShortageStatus.parse("Current") is ShortageStatus.CURRENT
    assert ShortageStatus.parse("To Be Discontinued") is ShortageStatus.TO_BE_DISCONTINUED
    assert ShortageStatus.parse("resolved 2024") is ShortageStatus.RESOLVED
    assert ShortageStatus.parse(None) is ShortageStatus.UNKNOWN
