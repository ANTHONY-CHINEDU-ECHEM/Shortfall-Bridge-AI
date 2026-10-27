"""Narrative composition: LLM under verification, with a deterministic template fallback."""

from __future__ import annotations

import logging

from ..domain.models import Narrative, NarrativeSentence
from ..logging_setup import log_event
from .evidence_pack import EvidencePack, render_pack
from .llm import LLMClient
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE
from .verifier import GroundingVerifier, parse_llm_json

logger = logging.getLogger(__name__)


def template_narrative(pack: EvidencePack) -> Narrative:
    """Every sentence is an engine fact carrying the chunk ids that justified it: grounded by construction."""
    sentences = [
        NarrativeSentence(text=text, citations=pack.fact_citations.get(key, []), engine_facts=[text])
        for key, text in pack.facts.items()
    ]
    return Narrative(sentences=sentences, generator="template", verification={"grounded_by_construction": True})


class NarrativeComposer:
    def __init__(self, llm: LLMClient | None, verifier: GroundingVerifier, min_keep_ratio: float = 0.6) -> None:
        self.llm, self.verifier, self.min_keep_ratio = llm, verifier, min_keep_ratio

    def compose(self, pack: EvidencePack, drug: str, question: str | None) -> Narrative:
        if self.llm is None or not pack.facts:
            return template_narrative(pack)
        try:
            raw = self.llm.complete(
                SYSTEM_PROMPT,
                USER_TEMPLATE.format(drug=drug, question=question or "Suggest safe alternatives.", pack=render_pack(pack)),
            )
            items = parse_llm_json(raw)
        except Exception as exc:
            log_event(logger, "llm_failed_fallback_template", level=logging.WARNING, error=str(exc)[:300])
            n = template_narrative(pack)
            n.verification["llm_error"] = str(exc)[:120]
            return n
        accepted, report = self.verifier.verify(items, pack)
        log_event(logger, "narrative_verified", **report.as_dict())
        if not accepted or report.acceptance_rate < self.min_keep_ratio:
            n = template_narrative(pack)
            n.verification.update({"fallback_reason": "verification_threshold", **report.as_dict()})
            return n
        return Narrative(sentences=accepted, generator=self.llm.name, verification=report.as_dict())

