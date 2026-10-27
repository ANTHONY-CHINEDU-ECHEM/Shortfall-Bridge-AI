"""Deterministic extraction of clinically actionable rules from label text.

These extractors read the full relevant sections of each candidate's label (not just the top
retrieved chunks) so that a safety gate can never miss a contraindication because retrieval
ranked it ninth. Every extracted rule keeps the sentence and chunk id it came from, which become
the citations shown to the clinician.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from ..domain.enums import HepaticImpairment
from ..domain.models import Chunk
from ..domain.normalize import word_boundary_contains

_SENT_SPLIT = re.compile(r"(?<=[.;])\s+(?=[A-Z0-9(])|\n+")


def sentences(chunk: Chunk) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(chunk.text) if len(s.strip()) > 12]


class Action(IntEnum):
    MONITOR = 1
    ADJUST = 2
    AVOID = 3
    CONTRAINDICATED = 4


_CONTRA = re.compile(r"contraindicat|must not be used|do not (?:use|administer|initiate|co.?administer)", re.I)
_AVOID = re.compile(r"\bavoid\b|not recommended|should not be (?:used|given|administered)|is not advised", re.I)
_ADJUST = re.compile(
    r"reduc|adjust|decreas|lower (?:the )?(?:starting )?dos|dosage modification|extend(?:ed)? (?:the )?(?:dosing )?interval|"
    r"every (?:24|36|48|72) hours|maximum (?:daily )?dose|do not exceed|half the|50 ?%",
    re.I,
)
_MONITOR = re.compile(r"monitor|caution|closely observe|careful", re.I)


def classify_action(sentence: str) -> Action | None:
    if _CONTRA.search(sentence):
        return Action.CONTRAINDICATED
    if _AVOID.search(sentence):
        return Action.AVOID
    if _ADJUST.search(sentence):
        return Action.ADJUST
    if _MONITOR.search(sentence):
        return Action.MONITOR
    return None


# Renal ----------------------------------------------------------------------------------------

_RENAL_TERM = re.compile(
    r"renal|kidney|creatinine clearance|\bcrcl\b|\bclcr\b|\begfr\b|\bgfr\b|dialysis|end.?stage renal|\besrd\b|anuria|anuric",
    re.I,
)
_LT = r"(?:<|≤|=<|less than(?: or equal to)?|below|lower than|under|of less than|not exceeding)"
_RENAL_THRESHOLD = re.compile(
    rf"(?:crcl|clcr|creatinine clearance|egfr|gfr|estimated glomerular filtration rate)[^.;]{{0,60}}?{_LT}\s*(\d{{1,3}})\s*(?:ml|mL)",
    re.I,
)
_LT_ML = re.compile(rf"{_LT}\s*(\d{{1,3}})\s*(?:ml|mL)\s*/\s*min", re.I)
_RANGE_ML = re.compile(r"(\d{1,3})\s*(?:to|-|–)\s*(\d{1,3})\s*(?:ml|mL)\s*/\s*min", re.I)


@dataclass
class RenalRule:
    threshold: float  # rule applies when patient renal function is below this value
    action: Action
    sentence: str
    chunk_id: str


def extract_renal_rules(chunks: list[Chunk]) -> list[RenalRule]:
    rules: list[RenalRule] = []
    for ch in chunks:
        for s in sentences(ch):
            if not _RENAL_TERM.search(s):
                continue
            action = classify_action(s)
            if action is None:
                continue
            thresholds: list[float] = [float(m.group(1)) for m in _RENAL_THRESHOLD.finditer(s)]
            thresholds += [float(m.group(1)) for m in _LT_ML.finditer(s)]
            # A range "30 to 50 mL/min" applies below its upper bound.
            thresholds += [float(max(int(m.group(1)), int(m.group(2)))) + 0.001 for m in _RANGE_ML.finditer(s)]
            low = s.lower()
            if not thresholds:
                if re.search(r"end.?stage|esrd|dialysis|anuri", low):
                    thresholds.append(15.0)
                elif "severe renal" in low or "severe kidney" in low:
                    thresholds.append(30.0)
                elif "moderate renal" in low or "moderate kidney" in low:
                    thresholds.append(60.0)
                elif "mild renal" in low:
                    thresholds.append(90.0)
                elif re.search(r"renal (?:impairment|insufficiency|dysfunction|failure)", low):
                    thresholds.append(60.0)
            for t in thresholds:
                if 5 <= t <= 130:
                    rules.append(RenalRule(threshold=t, action=action, sentence=s, chunk_id=ch.chunk_id))
    # keep the strongest action per threshold
    best: dict[float, RenalRule] = {}
    for r in rules:
        if r.threshold not in best or r.action > best[r.threshold].action:
            best[r.threshold] = r
    return sorted(best.values(), key=lambda r: r.threshold)


# Hepatic --------------------------------------------------------------------------------------

_HEP_TERM = re.compile(r"hepatic|liver|child.?pugh|cirrhosis", re.I)


@dataclass
class HepaticRule:
    min_severity: HepaticImpairment
    action: Action
    sentence: str
    chunk_id: str


_SEVERITY_ORDER = [HepaticImpairment.NONE, HepaticImpairment.MILD, HepaticImpairment.MODERATE, HepaticImpairment.SEVERE]


def hepatic_rank(level: HepaticImpairment) -> int:
    return _SEVERITY_ORDER.index(level)


def extract_hepatic_rules(chunks: list[Chunk]) -> list[HepaticRule]:
    rules: list[HepaticRule] = []
    for ch in chunks:
        for s in sentences(ch):
            if not _HEP_TERM.search(s):
                continue
            action = classify_action(s)
            if action is None:
                continue
            low = s.lower()
            if re.search(r"child.?pugh (?:class )?c\b|severe hepatic|severe liver|decompensated|hepatic failure", low):
                sev = HepaticImpairment.SEVERE
            elif re.search(r"child.?pugh (?:class )?b\b|moderate hepatic|moderate liver", low):
                sev = HepaticImpairment.MODERATE
            elif re.search(r"child.?pugh (?:class )?a\b|mild hepatic|mild liver", low):
                sev = HepaticImpairment.MILD
            elif re.search(r"hepatic (?:impairment|disease|insufficiency)|liver disease|cirrhosis", low):
                sev = HepaticImpairment.MODERATE
            else:
                continue
            rules.append(HepaticRule(min_severity=sev, action=action, sentence=s, chunk_id=ch.chunk_id))
    return rules


# Pregnancy and lactation ----------------------------------------------------------------------

_PREG = re.compile(r"pregnan|fetal|foetal|embryo|teratogen", re.I)
_FETAL_HARM = re.compile(r"fetal harm|fetal toxicity|embryo.?fetal toxicity|teratogenic|birth defects|fetal death", re.I)
_LACT_STOP = re.compile(
    r"(?:not|do not|should not) (?:to )?breast.?feed|breast.?feeding is not recommended|discontinue (?:nursing|breast.?feeding)|"
    r"advise (?:women|patients) not to breast.?feed|lactation is not recommended",
    re.I,
)


@dataclass
class PregnancyFinding:
    kind: str  # contraindicated | fetal_harm | lactation_avoid
    sentence: str
    chunk_id: str


def extract_pregnancy_findings(chunks: list[Chunk]) -> list[PregnancyFinding]:
    out: list[PregnancyFinding] = []
    for ch in chunks:
        for s in sentences(ch):
            if _PREG.search(s):
                if _CONTRA.search(s) and re.search(r"pregnan", s, re.I):
                    out.append(PregnancyFinding("contraindicated", s, ch.chunk_id))
                elif _FETAL_HARM.search(s):
                    out.append(PregnancyFinding("fetal_harm", s, ch.chunk_id))
            if _LACT_STOP.search(s):
                out.append(PregnancyFinding("lactation_avoid", s, ch.chunk_id))
    return out


# Interactions ---------------------------------------------------------------------------------

_CYP = re.compile(r"cytochrome p450 (\w+) (inhibitors|inducers)", re.I)
_CLASS_ALIASES: dict[str, list[str]] = {
    "nonsteroidal anti-inflammatory drug": ["nsaid", "nsaids", "nonsteroidal anti-inflammatory"],
    "monoamine oxidase inhibitor": ["mao inhibitor", "mao inhibitors", "maoi", "maois"],
    "vitamin k antagonist": ["warfarin", "vitamin k antagonist", "coumarin"],
    "selective serotonin reuptake inhibitor": ["ssri", "ssris", "selective serotonin reuptake inhibitor"],
    "opioid agonist": ["opioid", "opioids"],
    "benzodiazepine": ["benzodiazepine", "benzodiazepines", "cns depressant"],
    "potassium-sparing diuretic": ["potassium-sparing diuretic", "potassium sparing diuretic"],
    "angiotensin converting enzyme inhibitor": ["ace inhibitor", "ace inhibitors", "angiotensin converting enzyme"],
    "angiotensin 2 receptor blocker": ["angiotensin receptor blocker", "arb", "arbs"],
    "aminoglycoside antibacterial": ["aminoglycoside", "aminoglycosides"],
    "platelet aggregation inhibitor": ["antiplatelet", "platelet aggregation inhibitor"],
    "anticoagulant": ["anticoagulant", "anticoagulants"],
}


def class_mention_terms(class_names: list[str]) -> list[str]:
    """Turn EPC/MoA class names into the phrases labels actually use in interaction text."""
    terms: list[str] = []
    for raw in class_names:
        name = re.sub(r"\s*\[(?:EPC|MoA|PE|CS)\]\s*$", "", raw).strip().lower()
        if not name:
            continue
        m = _CYP.search(name)
        if m:
            enzyme, kind = m.group(1).upper(), m.group(2).lower()
            singular = kind[:-1]
            terms += [f"cyp{enzyme.lower()} {singular}", f"cyp{enzyme.lower()} {kind}"]
            if len(enzyme) == 3 and enzyme.endswith("4"):
                terms.append(f"cyp{enzyme[:2].lower()} {kind}")
            continue
        for key, aliases in _CLASS_ALIASES.items():
            if key in name:
                terms.extend(aliases)
        if len(name) > 6:
            terms.append(name)
    out: list[str] = []
    for t in terms:
        if t not in out:
            out.append(t)
    return out


@dataclass
class InteractionHit:
    other: str
    matched_term: str
    action: Action
    sentence: str
    chunk_id: str


def find_interactions(chunks: list[Chunk], other: str, terms: list[str]) -> list[InteractionHit]:
    hits: list[InteractionHit] = []
    for ch in chunks:
        for s in sentences(ch):
            for term in terms:
                if word_boundary_contains(s, term):
                    action = classify_action(s) or Action.MONITOR
                    hits.append(InteractionHit(other, term, action, s, ch.chunk_id))
                    break
    hits.sort(key=lambda h: -h.action)
    return hits


# Paediatric -----------------------------------------------------------------------------------

_PEDS_NOT_ESTABLISHED = re.compile(r"(?:safety and )?effectiveness[^.]{0,80}not (?:been )?established|not recommended for (?:use in )?(?:pediatric|children)", re.I)


def paediatric_unestablished(chunks: list[Chunk]) -> tuple[str, str] | None:
    for ch in chunks:
        for s in sentences(ch):
            if _PEDS_NOT_ESTABLISHED.search(s):
                return s, ch.chunk_id
    return None
