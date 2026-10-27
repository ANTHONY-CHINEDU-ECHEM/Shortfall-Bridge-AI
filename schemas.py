"""API specific response schemas (domain models are reused for the main payloads)."""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.models import Citation, LabelDocument, ShortageRecord


class DrugSuggestion(BaseModel):
    ingredient: str
    display: str
    score: float
    in_current_shortage: bool = False


class ShortagePage(BaseModel):
    items: list[ShortageRecord]
    total: int
    limit: int
    offset: int


class EvidenceResponse(BaseModel):
    citation: Citation
    label: LabelDocument | None


class GateDefinition(BaseModel):
    gate: str
    question: str
    sections: list[str]


class MetaResponse(BaseModel):
    version: str
    corpus: dict[str, int]
    data_versions: dict[str, str]
    embedding_model: str
    llm_provider: str
    tiers: list[dict[str, str]]
    gates: list[GateDefinition]
