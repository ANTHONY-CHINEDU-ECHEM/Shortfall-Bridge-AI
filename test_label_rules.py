from datetime import date

from shortfall_bridge.domain.enums import HepaticImpairment, SectionType
from shortfall_bridge.domain.models import Chunk
from shortfall_bridge.engine.label_rules import (
    Action,
    class_mention_terms,
    extract_hepatic_rules,
    extract_pregnancy_findings,
    extract_renal_rules,
    find_interactions,
)


def ch(text: str, section: SectionType = SectionType.DOSAGE_AND_ADMINISTRATION) -> Chunk:
    return Chunk(chunk_id="c1", set_id="s", section=section, ordinal=0, text=text, effective_time=date(2024, 1, 1))


def test_renal_numeric_threshold_and_action() -> None:
    rules = extract_renal_rules([ch("Contraindicated in patients with CrCl less than 30 mL/min.")])
    assert rules and rules[0].threshold == 30 and rules[0].action is Action.CONTRAINDICATED


def test_renal_range_applies_below_upper_bound() -> None:
    rules = extract_renal_rules([ch("For creatinine clearance 30 to 50 mL/min, reduce the dose by half.")])
    assert rules and 50 < rules[0].threshold < 51 and rules[0].action is Action.ADJUST


def test_renal_qualitative_severity() -> None:
    rules = extract_renal_rules([ch("Avoid use in patients with severe renal impairment.")])
    assert rules[0].threshold == 30 and rules[0].action is Action.AVOID
    rules = extract_renal_rules([ch("Not recommended in patients on dialysis.")])
    assert rules[0].threshold == 15


def test_non_renal_sentence_ignored() -> None:
    assert extract_renal_rules([ch("Reduce the dose in patients with hepatic impairment.")]) == []


def test_hepatic_child_pugh() -> None:
    rules = extract_hepatic_rules([ch("Contraindicated in patients with Child-Pugh C hepatic impairment.")])
    assert rules[0].min_severity is HepaticImpairment.SEVERE and rules[0].action is Action.CONTRAINDICATED


def test_pregnancy_findings() -> None:
    f = extract_pregnancy_findings([ch("Statins may cause fetal harm when administered to a pregnant woman.")])
    assert f[0].kind == "fetal_harm"
    f = extract_pregnancy_findings([ch("Advise women not to breastfeed during treatment.")])
    assert f[0].kind == "lactation_avoid"


def test_cyp_class_terms() -> None:
    terms = class_mention_terms(["Cytochrome P450 3A4 Inhibitors [MoA]", "Nonsteroidal Anti-inflammatory Drug [EPC]"])
    assert "cyp3a4 inhibitors" in terms and "cyp3a inhibitors" in terms and "nsaids" in terms


def test_interaction_hit_severity() -> None:
    hits = find_interactions(
        [ch("Simvastatin is contraindicated with strong CYP3A4 inhibitors such as clarithromycin.",
            SectionType.CONTRAINDICATIONS)],
        "clarithromycin", ["clarithromycin"],
    )
    assert hits and hits[0].action is Action.CONTRAINDICATED
