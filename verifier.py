"""Citation and grounding verifier for generated narrative.

Checks applied to every sentence:
  citation_present   at least one F or C id
  citation_valid     every id exists in the evidence pack
  numeric_grounding  every number in the sentence appears in the text it cites
  drug_grounding     every known drug name in the sentence is part of this decision
Sentences failing any check are dropped. If fewer than ``min_keep_ratio`` survive, the caller
falls back to the deterministic template.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from ..domain.models import NarrativeSentence
from .evidence_pack import EvidencePack

_NUM = re.compile(r"(?<![A-Za-z])(\d+(?:\.\d+)?)")
_ID = re.compile(r"^[FC]\d+$")
_WORD = re.compile(r"[a-z][a-z0-9\-]+")


@dataclass
class VerificationReport:
    total: int = 0
    accepted: int = 0
    reasons: Counter = field(default_factory=Counter)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, int | float]:
        return {"sentences_total": self.total, "sentences_accepted": self.accepted,
                "acceptance_rate": round(self.acceptance_rate, 3), **{f"rejected_{k}": v for k, v in self.reasons.items()}}


def parse_llm_json(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model output")
    data = json.loads(text[start : end + 1])
    sentences = data.get("sentences")
    if not isinstance(sentences, list):
        raise ValueError("JSON lacks a sentences array")
    return [s for s in sentences if isinstance(s, dict) and isinstance(s.get("text"), str)]


def _numbers(text: str) -> set[str]:
    out = set()
    for m in _NUM.finditer(text):
        v = m.group(1)
        out.add(v.rstrip("0").rstrip(".") if "." in v else v)
    return out


def _mentioned_drugs(text: str, vocabulary: set[str]) -> set[str]:
    words = _WORD.findall(text.lower())
    found = {w for w in words if w in vocabulary}
    found |= {f"{a} {b}" for a, b in zip(words, words[1:], strict=False) if f"{a} {b}" in vocabulary}
    return found


class GroundingVerifier:
    def __init__(self, drug_vocabulary: set[str]) -> None:
        self.vocab = {v for v in drug_vocabulary if len(v) >= 5}

    def verify(self, items: list[dict], pack: EvidencePack) -> tuple[list[NarrativeSentence], VerificationReport]:
        report = VerificationReport(total=len(items))
        accepted: list[NarrativeSentence] = []
        allowed = {d.lower() for d in pack.allowed_drugs}
        for it in items:
            text = " ".join(str(it.get("text", "")).split())
            cites = [str(c).strip("[] ") for c in (it.get("cites") or [])]
            if not text:
                continue
            if not cites:
                report.reasons["no_citation"] += 1
                continue
            if any(not _ID.match(c) or (c not in pack.facts and c not in pack.passages) for c in cites):
                report.reasons["invalid_citation"] += 1
                continue
            source = " ".join(pack.source_text(c) for c in cites)
            missing = _numbers(text) - _numbers(source)
            if missing:
                report.reasons["ungrounded_number"] += 1
                continue
            stray = {d for d in _mentioned_drugs(text, self.vocab) if not any(d in a or a in d for a in allowed)}
            if stray:
                report.reasons["unsupported_drug"] += 1
                continue
            accepted.append(
                NarrativeSentence(
                    text=text,
                    citations=[pack.passages[c].chunk_id for c in cites if c in pack.passages],
                    engine_facts=[pack.facts[c] for c in cites if c in pack.facts],
                )
            )
            report.accepted += 1
        return accepted, report
