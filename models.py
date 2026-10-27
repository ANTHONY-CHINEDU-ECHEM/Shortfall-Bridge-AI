"""Core domain models shared by ingestion, engine, API and evaluation."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from .enums import (
    CandidateTier,
    GateName,
    GateStatus,
    HepaticImpairment,
    RecommendationStatus,
    SectionType,
    ShortageStatus,
)

# Knowledge records -----------------------------------------------------------------------------


class LabelDocument(BaseModel):
    """One structured product label (SPL) version."""

    set_id: str
    spl_id: str | None = None
    version: str | None = None
    effective_time: date | None = None
    title: str
    generic_names: list[str] = Field(default_factory=list)
    brand_names: list[str] = Field(default_factory=list)
    ingredients: list[str] = Field(default_factory=list)  # normalised substance names, lower case
    manufacturer: str | None = None
    routes: list[str] = Field(default_factory=list)
    product_type: str | None = None
    rxcuis: list[str] = Field(default_factory=list)
    pharm_class_epc: list[str] = Field(default_factory=list)
    pharm_class_moa: list[str] = Field(default_factory=list)
    application_numbers: list[str] = Field(default_factory=list)
    source: str = "openfda_label"

    @property
    def dailymed_url(self) -> str:
        return f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={self.set_id}"


class Chunk(BaseModel):
    """A retrievable passage of a label section."""

    chunk_id: str
    set_id: str
    section: SectionType
    ordinal: int
    text: str
    ingredients: list[str] = Field(default_factory=list)
    title: str = ""
    effective_time: date | None = None

    @staticmethod
    def make_id(set_id: str, section: str, ordinal: int) -> str:
        digest = hashlib.sha1(f"{set_id}|{section}|{ordinal}".encode()).hexdigest()[:16]
        return f"c_{digest}"


class ShortageRecord(BaseModel):
    """One presentation level entry from the FDA Drug Shortages database."""

    record_id: str
    generic_name: str
    ingredients: list[str] = Field(default_factory=list)
    proprietary_name: str | None = None
    company_name: str | None = None
    presentation: str | None = None
    dosage_form: str | None = None
    strength: list[str] = Field(default_factory=list)
    status: ShortageStatus = ShortageStatus.UNKNOWN
    availability: str | None = None
    shortage_reason: str | None = None
    related_info: str | None = None
    therapeutic_category: list[str] = Field(default_factory=list)
    package_ndc: str | None = None
    routes: list[str] = Field(default_factory=list)
    rxcuis: list[str] = Field(default_factory=list)
    pharm_class_epc: list[str] = Field(default_factory=list)
    initial_posting_date: date | None = None
    update_date: date | None = None


class OrangeBookProduct(BaseModel):
    """One row of the Orange Book Products file."""

    ingredient: str
    ingredients: list[str] = Field(default_factory=list)
    dosage_form: str
    route: str
    trade_name: str
    applicant: str
    applicant_full_name: str | None = None
    strength: str
    appl_type: str  # N (NDA) or A (ANDA)
    appl_no: str
    product_no: str
    te_code: str | None = None
    approval_date: str | None = None
    rld: bool = False
    rs: bool = False
    marketing_type: str  # RX, OTC, DISCN

    @property
    def is_marketed(self) -> bool:
        return self.marketing_type.upper() in {"RX", "OTC"}

    @property
    def is_a_rated(self) -> bool:
        return bool(self.te_code) and self.te_code.upper().startswith("A")


class DrugClass(BaseModel):
    class_id: str
    class_name: str
    class_type: str  # EPC, MOA, ATC1-4, etc
    source: str  # DAILYMED, ATC, FDASPL, LABEL


class DrugConcept(BaseModel):
    """A resolved query drug."""

    query: str
    ingredients: list[str]
    display_name: str
    rxcui: str | None = None
    classes: list[DrugClass] = Field(default_factory=list)
    label_set_ids: list[str] = Field(default_factory=list)
    resolution_method: str = "local_index"
    confidence: float = 1.0


# Request side ---------------------------------------------------------------------------------


class DoseInput(BaseModel):
    amount: float = Field(gt=0)
    unit: str = "mg"
    frequency: str | None = None  # e.g. "once daily", "every 12 hours"


class PatientContext(BaseModel):
    age_years: float | None = Field(default=None, ge=0, le=125)
    weight_kg: float | None = Field(default=None, gt=0, le=400)
    creatinine_clearance_ml_min: float | None = Field(default=None, ge=0, le=250)
    egfr_ml_min_1_73m2: float | None = Field(default=None, ge=0, le=250)
    hepatic_impairment: HepaticImpairment = HepaticImpairment.NONE
    pregnant: bool = False
    breastfeeding: bool = False
    allergies: list[str] = Field(default_factory=list)
    current_medications: list[str] = Field(default_factory=list)

    @field_validator("allergies", "current_medications")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for item in v:
            s = item.strip()
            if s and s.lower() not in [x.lower() for x in seen]:
                seen.append(s)
        return seen

    @property
    def renal_function(self) -> float | None:
        """Creatinine clearance is preferred because most US labels dose by CrCl."""
        if self.creatinine_clearance_ml_min is not None:
            return self.creatinine_clearance_ml_min
        return self.egfr_ml_min_1_73m2


class SubstitutionRequest(BaseModel):
    drug: str = Field(min_length=2, max_length=200)
    rxcui: str | None = None
    strength: str | None = None
    route: str | None = None
    dosage_form: str | None = None
    current_dose: DoseInput | None = None
    patient: PatientContext = Field(default_factory=PatientContext)
    clinical_question: str | None = Field(default=None, max_length=1000)
    max_candidates: int = Field(default=8, ge=1, le=20)
    include_alternative_classes: bool = True


# Response side --------------------------------------------------------------------------------


class Citation(BaseModel):
    chunk_id: str
    set_id: str
    section: SectionType
    title: str
    snippet: str
    dailymed_url: str
    effective_time: date | None = None


class GateResult(BaseModel):
    gate: GateName
    status: GateStatus
    rationale: str
    citations: list[str] = Field(default_factory=list)  # chunk ids


class DoseConversion(BaseModel):
    feasible: bool
    from_drug: str
    to_drug: str
    from_dose: str | None = None
    suggested_dose: str | None = None
    suggested_range: str | None = None
    method: str
    reference: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    ingredient: str
    display_name: str
    tier: CandidateTier
    rationale: str
    products: list[str] = Field(default_factory=list)  # human readable product descriptors
    te_codes: list[str] = Field(default_factory=list)
    shared_classes: list[str] = Field(default_factory=list)
    gates: list[GateResult] = Field(default_factory=list)
    dose_conversion: DoseConversion | None = None
    citations: list[str] = Field(default_factory=list)
    score: float = 0.0
    requires_specialist_review: bool = False

    @property
    def worst_gate(self) -> GateStatus:
        if not self.gates:
            return GateStatus.UNKNOWN
        return max((g.status for g in self.gates), key=lambda s: s.severity)

    @property
    def blocked(self) -> bool:
        return any(g.status is GateStatus.BLOCK for g in self.gates)


class ShortageSummary(BaseModel):
    in_shortage: bool
    status: ShortageStatus
    matching_records: int
    presentations: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    availability_notes: list[str] = Field(default_factory=list)
    last_updated: date | None = None


class NarrativeSentence(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)  # label chunk ids
    engine_facts: list[str] = Field(default_factory=list)  # engine fact texts the sentence relies on


class Narrative(BaseModel):
    sentences: list[NarrativeSentence]
    generator: str  # "llm:<model>" or "template"
    verification: dict[str, int | float | bool | str] = Field(default_factory=dict)


class Recommendation(BaseModel):
    request_id: str
    created_at: datetime
    status: RecommendationStatus
    query: DrugConcept | None
    shortage: ShortageSummary | None
    candidates: list[Candidate]
    not_recommended: list[Candidate]
    narrative: Narrative
    citations: dict[str, Citation]
    abstain_reasons: list[str] = Field(default_factory=list)
    data_versions: dict[str, str] = Field(default_factory=dict)
    disclaimer: str = (
        "Decision support only. Verify every substitution against the current product label, local "
        "formulary and the patient record before prescribing, dispensing or administering."
    )
    latency_ms: float = 0.0
