"""Controlled vocabularies used across the system."""

from __future__ import annotations

from enum import StrEnum


class SectionType(StrEnum):
    """Label sections indexed for retrieval, aligned to openFDA label fields and SPL LOINC codes."""

    BOXED_WARNING = "boxed_warning"
    INDICATIONS = "indications_and_usage"
    DOSAGE_AND_ADMINISTRATION = "dosage_and_administration"
    DOSAGE_FORMS_AND_STRENGTHS = "dosage_forms_and_strengths"
    CONTRAINDICATIONS = "contraindications"
    WARNINGS_AND_CAUTIONS = "warnings_and_cautions"
    WARNINGS = "warnings"
    PRECAUTIONS = "precautions"
    ADVERSE_REACTIONS = "adverse_reactions"
    DRUG_INTERACTIONS = "drug_interactions"
    SPECIFIC_POPULATIONS = "use_in_specific_populations"
    PREGNANCY = "pregnancy"
    LACTATION = "lactation"
    PEDIATRIC_USE = "pediatric_use"
    GERIATRIC_USE = "geriatric_use"
    CLINICAL_PHARMACOLOGY = "clinical_pharmacology"
    PHARMACOKINETICS = "pharmacokinetics"
    MECHANISM_OF_ACTION = "mechanism_of_action"
    OVERDOSAGE = "overdosage"
    HOW_SUPPLIED = "how_supplied"
    DESCRIPTION = "description"


# SPL LOINC section codes (DailyMed XML) mapped to the same section vocabulary.
LOINC_TO_SECTION: dict[str, SectionType] = {
    "34066-1": SectionType.BOXED_WARNING,
    "34067-9": SectionType.INDICATIONS,
    "34068-7": SectionType.DOSAGE_AND_ADMINISTRATION,
    "43678-2": SectionType.DOSAGE_FORMS_AND_STRENGTHS,
    "34070-3": SectionType.CONTRAINDICATIONS,
    "43685-7": SectionType.WARNINGS_AND_CAUTIONS,
    "34071-1": SectionType.WARNINGS,
    "42232-9": SectionType.PRECAUTIONS,
    "34084-4": SectionType.ADVERSE_REACTIONS,
    "34073-7": SectionType.DRUG_INTERACTIONS,
    "43684-0": SectionType.SPECIFIC_POPULATIONS,
    "42228-7": SectionType.PREGNANCY,
    "77290-5": SectionType.LACTATION,
    "34080-2": SectionType.LACTATION,  # nursing mothers (older labels)
    "34081-0": SectionType.PEDIATRIC_USE,
    "34082-8": SectionType.GERIATRIC_USE,
    "34090-1": SectionType.CLINICAL_PHARMACOLOGY,
    "43682-4": SectionType.PHARMACOKINETICS,
    "43679-0": SectionType.MECHANISM_OF_ACTION,
    "34088-5": SectionType.OVERDOSAGE,
    "34069-5": SectionType.HOW_SUPPLIED,
    "34089-3": SectionType.DESCRIPTION,
}

# openFDA label JSON field names that map onto sections (nursing_mothers is the legacy lactation field).
OPENFDA_FIELD_TO_SECTION: dict[str, SectionType] = {s.value: s for s in SectionType}
OPENFDA_FIELD_TO_SECTION["nursing_mothers"] = SectionType.LACTATION


class CandidateTier(StrEnum):
    """Substitution tiers, ordered from least to most clinical disruption."""

    THERAPEUTIC_EQUIVALENT = "T1_therapeutic_equivalent"
    ALTERNATIVE_FORMULATION = "T2_alternative_formulation"
    THERAPEUTIC_INTERCHANGE = "T3_therapeutic_interchange"
    ALTERNATIVE_CLASS = "T4_alternative_class"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    @property
    def label(self) -> str:
        return {
            "T1_therapeutic_equivalent": "Therapeutic equivalent",
            "T2_alternative_formulation": "Same ingredient, alternative formulation",
            "T3_therapeutic_interchange": "Therapeutic interchange within class",
            "T4_alternative_class": "Alternative class for the same indication",
        }[self.value]


class GateStatus(StrEnum):
    PASS = "pass"
    CAUTION = "caution"
    BLOCK = "block"
    UNKNOWN = "unknown"

    @property
    def severity(self) -> int:
        return {"pass": 0, "unknown": 1, "caution": 2, "block": 3}[self.value]


class GateName(StrEnum):
    AVAILABILITY = "availability"
    ALLERGY = "allergy"
    RENAL = "renal"
    HEPATIC = "hepatic"
    PREGNANCY_LACTATION = "pregnancy_lactation"
    INTERACTIONS = "interactions"
    BOXED_WARNING = "boxed_warning"
    AGE = "age"


class ShortageStatus(StrEnum):
    CURRENT = "Current"
    RESOLVED = "Resolved"
    TO_BE_DISCONTINUED = "To Be Discontinued"
    UNKNOWN = "Unknown"

    @classmethod
    def parse(cls, raw: str | None) -> ShortageStatus:
        if not raw:
            return cls.UNKNOWN
        key = raw.strip().lower()
        for member in cls:
            if member.value.lower() == key:
                return member
        if "discontinu" in key:
            return cls.TO_BE_DISCONTINUED
        if "resolv" in key:
            return cls.RESOLVED
        if "current" in key or "shortage" in key:
            return cls.CURRENT
        return cls.UNKNOWN


class HepaticImpairment(StrEnum):
    NONE = "none"
    MILD = "mild"  # Child Pugh A
    MODERATE = "moderate"  # Child Pugh B
    SEVERE = "severe"  # Child Pugh C


class Sex(StrEnum):
    """Sex used only by creatinine clearance and eGFR equations (Cockcroft-Gault, CKD-EPI 2021)."""

    FEMALE = "female"
    MALE = "male"
    UNKNOWN = "unknown"


class CreatinineUnit(StrEnum):
    MG_DL = "mg/dL"
    UMOL_L = "umol/L"


class ReviewDecision(StrEnum):
    """Pharmacist review outcome recorded against one ranked option."""

    AGREE = "agree"
    OVERRIDE = "override"
    REJECT = "reject"


class RecommendationStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"  # candidates exist but every one carries cautions
    ABSTAIN = "abstain"
