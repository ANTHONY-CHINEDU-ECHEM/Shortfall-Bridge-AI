"""Evidence pack: the only material a language model may use.

Engine facts (F ids) are statements the deterministic engine has already decided, such as the
tier of a candidate or a calculated dose. Evidence passages (C ids) are label chunks. The
narrative must cite one of these ids in every sentence, and the verifier checks that numbers
and drug names in each sentence are supported by what it cites.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.enums import GateStatus
from ..domain.models import Candidate, Chunk, DrugConcept, ShortageSummary


@dataclass
class EvidencePack:
    facts: dict[str, str] = field(default_factory=dict)  # F1 -> text
    passages: dict[str, Chunk] = field(default_factory=dict)  # C1 -> chunk
    allowed_drugs: set[str] = field(default_factory=set)
    fact_citations: dict[str, list[str]] = field(default_factory=dict)  # F1 -> chunk ids

    def chunk_alias(self, chunk_id: str) -> str | None:
        for alias, ch in self.passages.items():
            if ch.chunk_id == chunk_id:
                return alias
        return None

    def source_text(self, alias: str) -> str:
        if alias in self.facts:
            return self.facts[alias]
        if alias in self.passages:
            return self.passages[alias].text
        return ""


def build_pack(
    concept: DrugConcept,
    shortage: ShortageSummary | None,
    ranked: list[Candidate],
    blocked: list[Candidate],
    chunks: dict[str, Chunk],
    patient_terms: list[str],
    max_candidates: int = 5,
) -> EvidencePack:
    pack = EvidencePack()
    pack.allowed_drugs = {concept.ingredients[0], *[c.ingredient for c in ranked + blocked],
                          *[t.lower() for t in patient_terms]}

    def fact(text: str, cites: list[str] | None = None) -> str:
        for k, v in pack.facts.items():
            if v == text:  # identical findings for the same ingredient across tiers are stated once
                return k
        key = f"F{len(pack.facts) + 1}"
        pack.facts[key] = text
        pack.fact_citations[key] = [c for c in (cites or []) if c in chunks]
        return key

    def passage(chunk_id: str) -> str | None:
        ch = chunks.get(chunk_id)
        if ch is None:
            return None
        existing = pack.chunk_alias(chunk_id)
        if existing:
            return existing
        key = f"C{len(pack.passages) + 1}"
        pack.passages[key] = ch
        return key

    if shortage is not None:
        if shortage.in_shortage:
            fact(
                f"{concept.display_name} has {shortage.matching_records} current FDA shortage entries"
                + (f"; reasons reported: {', '.join(shortage.reasons[:3])}" if shortage.reasons else "")
                + "."
            )
        else:
            fact(f"{concept.display_name} has no current FDA shortage entry.")
    for i, c in enumerate(ranked[:max_candidates], 1):
        cautions = [g for g in c.gates if g.status in (GateStatus.CAUTION, GateStatus.UNKNOWN)]
        fact(
            f"Option {i} is {c.display_name}, tier {c.tier.label.lower()}, score {c.score:.2f}. {c.rationale}",
            c.citations[:2],
        )
        if c.dose_conversion:
            dc = c.dose_conversion
            if dc.feasible:
                fact(f"For {c.ingredient}, the suggested starting dose is {dc.suggested_dose} "
                     f"({dc.suggested_range}) converted from {dc.from_dose} of {dc.from_drug}.")
            elif dc.method.endswith("no_input_dose"):
                fact("Enter the current dose to calculate equivalent starting doses for interchange options.")
            else:
                fact(f"Dose conversion to {c.ingredient} is not provided: {' '.join(dc.warnings[:1])}")
        for g in cautions:
            fact(f"{c.ingredient} {g.gate.value.replace('_', ' ')} check is {g.status.value}: {g.rationale}",
                 g.citations)
            for cid in g.citations:
                passage(cid)
        for cid in c.citations[:2]:
            passage(cid)
    for c in blocked[:4]:
        reasons = "; ".join(g.rationale for g in c.gates if g.status is GateStatus.BLOCK)
        fact(f"{c.display_name} is not recommended: {reasons}",
             [cid for g in c.gates if g.status is GateStatus.BLOCK for cid in g.citations])
        for g in c.gates:
            if g.status is GateStatus.BLOCK:
                for cid in g.citations:
                    passage(cid)
    return pack


def render_pack(pack: EvidencePack) -> str:
    lines = ["ENGINE FACTS"]
    lines += [f"[{k}] {v}" for k, v in pack.facts.items()]
    lines.append("")
    lines.append("LABEL EVIDENCE")
    for k, ch in pack.passages.items():
        lines.append(f"[{k}] ({ch.title}; section {ch.section.value}) {ch.text}")
    return "\n".join(lines)
