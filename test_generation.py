import json

from shortfall_bridge.domain.enums import SectionType
from shortfall_bridge.domain.models import Chunk
from shortfall_bridge.generation.composer import NarrativeComposer
from shortfall_bridge.generation.evidence_pack import EvidencePack
from shortfall_bridge.generation.verifier import GroundingVerifier, parse_llm_json


def _pack() -> EvidencePack:
    p = EvidencePack()
    p.facts = {"F1": "Option 1 is bumetanide, tier therapeutic interchange within class, score 0.66.",
               "F2": "For bumetanide, the suggested starting dose is 2 mg converted from 40 mg of furosemide."}
    p.passages = {"C1": Chunk(chunk_id="c_abc", set_id="s", section=SectionType.CONTRAINDICATIONS, ordinal=0,
                              text="Bumetanide is contraindicated in anuria.")}
    p.allowed_drugs = {"bumetanide", "furosemide"}
    return p


class FakeLLM:
    name = "llm:fake"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, system: str, user: str) -> str:
        assert "ENGINE FACTS" in user and "[C1]" in user
        return self.payload


VOCAB = {"bumetanide", "furosemide", "torsemide", "warfarin"}


def test_parse_llm_json_with_fences() -> None:
    raw = '```json\n{"sentences": [{"text": "x", "cites": ["F1"]}]}\n```'
    assert parse_llm_json(raw)[0]["text"] == "x"


def test_verifier_rejects_ungrounded_number_and_drug() -> None:
    items = [
        {"text": "Bumetanide 2 mg is the suggested starting dose.", "cites": ["F2"]},
        {"text": "Bumetanide 5 mg is the suggested starting dose.", "cites": ["F2"]},
        {"text": "Torsemide is also an option.", "cites": ["F1"]},
        {"text": "Bumetanide is contraindicated in anuria.", "cites": ["C1"]},
        {"text": "No citation here.", "cites": []},
        {"text": "Invented citation.", "cites": ["C9"]},
    ]
    accepted, report = GroundingVerifier(VOCAB).verify(items, _pack())
    assert [s.text for s in accepted] == [
        "Bumetanide 2 mg is the suggested starting dose.", "Bumetanide is contraindicated in anuria.",
    ]
    assert accepted[1].citations == ["c_abc"]
    assert report.reasons["ungrounded_number"] == 1
    assert report.reasons["unsupported_drug"] == 1
    assert report.reasons["no_citation"] == 1 and report.reasons["invalid_citation"] == 1


def test_composer_uses_llm_when_grounded() -> None:
    payload = json.dumps({"sentences": [
        {"text": "Bumetanide is the first option.", "cites": ["F1"]},
        {"text": "Start bumetanide at 2 mg.", "cites": ["F2"]},
    ]})
    n = NarrativeComposer(FakeLLM(payload), GroundingVerifier(VOCAB)).compose(_pack(), "furosemide", None)
    assert n.generator == "llm:fake" and len(n.sentences) == 2


def test_composer_falls_back_when_llm_hallucinates() -> None:
    payload = json.dumps({"sentences": [
        {"text": "Use torsemide 20 mg instead.", "cites": ["F1"]},
        {"text": "Give 99 mg.", "cites": ["F2"]},
    ]})
    n = NarrativeComposer(FakeLLM(payload), GroundingVerifier(VOCAB)).compose(_pack(), "furosemide", None)
    assert n.generator == "template" and n.verification["fallback_reason"] == "verification_threshold"


def test_composer_falls_back_on_llm_error() -> None:
    class Broken:
        name = "llm:broken"

        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("timeout")

    n = NarrativeComposer(Broken(), GroundingVerifier(VOCAB)).compose(_pack(), "furosemide", None)
    assert n.generator == "template" and "llm_error" in n.verification
