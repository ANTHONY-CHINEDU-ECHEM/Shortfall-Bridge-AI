"""API specific request and response schemas (domain models are reused for the main payloads)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.models import (
    Citation,
    DoseInput,
    LabelDocument,
    Recommendation,
    ShortageRecord,
    SubstitutionRequest,
)


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
    store_mode: str = "indexed"
    runtime: str = "server"
    auth_required: bool = False
    features: list[str] = Field(default_factory=list)


class BatchRequest(BaseModel):
    """Up to 25 requests evaluated together, for example every patient on a ward affected by one shortage."""

    requests: list[SubstitutionRequest] = Field(min_length=1, max_length=25)
    labels: list[str] | None = Field(default=None, description="Optional caller labels (bed or episode), same order")


class BatchItem(BaseModel):
    label: str | None = None
    recommendation: Recommendation


class BatchResponse(BaseModel):
    results: list[BatchItem]
    summary: dict[str, Any]


class ConvertRequest(BaseModel):
    from_drug: str = Field(min_length=2, max_length=120)
    to_drug: str = Field(min_length=2, max_length=120)
    dose: DoseInput
    from_route: str | None = None
    to_route: str | None = None


class FhirSubstitutionResponse(BaseModel):
    mapping: dict[str, Any]
    recommendation: Recommendation


class AuditVerification(BaseModel):
    ok: bool
    checked: int
    legacy_unchained: int = 0
    head_hash: str | None = None
    first_break: str | None = None
    reason: str | None = None
