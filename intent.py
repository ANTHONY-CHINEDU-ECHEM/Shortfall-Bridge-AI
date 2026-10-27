"""Clinical intent detection and label section priors.

The clinician's free text question ("patient on dialysis, also taking warfarin") and the
structured patient context both imply which label sections matter. Those sections receive a
prior in fusion so that, for example, a renal question surfaces section 8.6 and the renal
dosing table rather than a generic adverse reaction paragraph that happens to mention kidneys.
"""

from __future__ import annotations

import re
from enum import StrEnum

from ..domain.enums import HepaticImpairment, SectionType
from ..domain.models import PatientContext


class Intent(StrEnum):
    SAFETY = "safety"
    DOSING = "dosing"
    RENAL = "renal"
    HEPATIC = "hepatic"
    INTERACTIONS = "interactions"
    PREGNANCY = "pregnancy"
    PAEDIATRIC = "paediatric"
    GERIATRIC = "geriatric"
    INDICATION = "indication"


INTENT_SECTIONS: dict[Intent, tuple[SectionType, ...]] = {
    Intent.SAFETY: (
        SectionType.BOXED_WARNING, SectionType.CONTRAINDICATIONS,
        SectionType.WARNINGS_AND_CAUTIONS, SectionType.WARNINGS,
    ),
    Intent.DOSING: (SectionType.DOSAGE_AND_ADMINISTRATION, SectionType.DOSAGE_FORMS_AND_STRENGTHS),
    Intent.RENAL: (
        SectionType.SPECIFIC_POPULATIONS, SectionType.DOSAGE_AND_ADMINISTRATION,
        SectionType.PHARMACOKINETICS, SectionType.CONTRAINDICATIONS,
    ),
    Intent.HEPATIC: (
        SectionType.SPECIFIC_POPULATIONS, SectionType.DOSAGE_AND_ADMINISTRATION,
        SectionType.CONTRAINDICATIONS, SectionType.WARNINGS_AND_CAUTIONS,
    ),
    Intent.INTERACTIONS: (SectionType.DRUG_INTERACTIONS, SectionType.CONTRAINDICATIONS),
    Intent.PREGNANCY: (
        SectionType.PREGNANCY, SectionType.LACTATION, SectionType.SPECIFIC_POPULATIONS, SectionType.BOXED_WARNING,
    ),
    Intent.PAEDIATRIC: (SectionType.PEDIATRIC_USE, SectionType.DOSAGE_AND_ADMINISTRATION),
    Intent.GERIATRIC: (SectionType.GERIATRIC_USE, SectionType.DOSAGE_AND_ADMINISTRATION),
    Intent.INDICATION: (SectionType.INDICATIONS,),
}

_LEXICON: dict[Intent, re.Pattern[str]] = {
    Intent.RENAL: re.compile(r"renal|kidney|crcl|creatinine|egfr|dialysis|haemodialysis|hemodialysis|ckd|aki", re.I),
    Intent.HEPATIC: re.compile(r"hepatic|liver|cirrhosis|child.?pugh|transaminase", re.I),
    Intent.INTERACTIONS: re.compile(r"interact|taking|co.?administ|concomitant|also on|alongside", re.I),
    Intent.PREGNANCY: re.compile(r"pregnan|breast.?feed|lactat|nursing|trimester", re.I),
    Intent.PAEDIATRIC: re.compile(r"child|paediatric|pediatric|infant|neonat|adolescent", re.I),
    Intent.GERIATRIC: re.compile(r"elderly|older adult|geriatric|frail", re.I),
    Intent.DOSING: re.compile(r"dose|dosing|titrat|convert|equivalen|mg|frequency", re.I),
    Intent.SAFETY: re.compile(r"safe|risk|warning|contraindicat|allerg|boxed", re.I),
}


def detect_intents(question: str | None, patient: PatientContext) -> list[Intent]:
    intents: list[Intent] = [Intent.SAFETY, Intent.DOSING]
    if question:
        for intent, pattern in _LEXICON.items():
            if pattern.search(question) and intent not in intents:
                intents.append(intent)
    if patient.renal_function is not None and Intent.RENAL not in intents:
        intents.append(Intent.RENAL)
    if patient.hepatic_impairment is not HepaticImpairment.NONE and Intent.HEPATIC not in intents:
        intents.append(Intent.HEPATIC)
    if patient.current_medications and Intent.INTERACTIONS not in intents:
        intents.append(Intent.INTERACTIONS)
    if (patient.pregnant or patient.breastfeeding) and Intent.PREGNANCY not in intents:
        intents.append(Intent.PREGNANCY)
    if patient.age_years is not None and patient.age_years < 18 and Intent.PAEDIATRIC not in intents:
        intents.append(Intent.PAEDIATRIC)
    if patient.age_years is not None and patient.age_years >= 65 and Intent.GERIATRIC not in intents:
        intents.append(Intent.GERIATRIC)
    return intents


def section_priors(intents: list[Intent]) -> dict[SectionType, float]:
    """Earlier intents and earlier sections within an intent receive larger priors."""
    priors: dict[SectionType, float] = {}
    for rank, intent in enumerate(intents):
        for pos, section in enumerate(INTENT_SECTIONS[intent]):
            w = 1.0 / (1 + 0.15 * rank + 0.1 * pos)
            priors[section] = max(priors.get(section, 0.0), w)
    return priors


def build_query(drug: str, intents: list[Intent], patient: PatientContext, question: str | None) -> str:
    parts = [drug]
    if question:
        parts.append(question)
    if Intent.RENAL in intents:
        parts.append("renal impairment creatinine clearance dose adjustment")
    if Intent.HEPATIC in intents:
        parts.append("hepatic impairment Child-Pugh dose")
    if Intent.PREGNANCY in intents:
        parts.append("pregnancy lactation risk")
    if patient.current_medications:
        parts.append("concomitant use " + " ".join(patient.current_medications))
    parts.append("contraindications warnings dosage")
    return " ".join(parts)
