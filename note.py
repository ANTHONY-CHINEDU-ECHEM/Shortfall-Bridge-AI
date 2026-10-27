"""Clinical note export for the patient record or a pharmacy intervention log.

The note is rendered from the stored audit record, never from the live engine, so what a
clinician files is exactly what was shown and is covered by the audit hash printed in its footer.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import GateStatus
from ..domain.models import Recommendation, SubstitutionRequest

_GATE_LABEL = {
    "availability": "Supply", "allergy": "Allergy", "renal": "Renal", "hepatic": "Hepatic",
    "pregnancy_lactation": "Pregnancy and lactation", "interactions": "Interactions",
    "boxed_warning": "Boxed warning", "age": "Age",
}
_STATUS = {"pass": "clear", "caution": "caution", "block": "blocked", "unknown": "needs data"}


def _patient_lines(req: SubstitutionRequest, rec: Recommendation) -> list[str]:
    p = req.patient
    bits: list[str] = []
    if p.age_years is not None:
        bits.append(f"age {p.age_years:g} years")
    if p.sex.value != "unknown":
        bits.append(p.sex.value)
    if p.weight_kg is not None:
        bits.append(f"weight {p.weight_kg:g} kg")
    if rec.renal and rec.renal.value_ml_min is not None:
        bits.append(f"renal function {rec.renal.value_ml_min:g} mL/min ({rec.renal.method})")
    if p.hepatic_impairment.value != "none":
        bits.append(f"{p.hepatic_impairment.value} hepatic impairment")
    if p.pregnant:
        bits.append("pregnant")
    if p.breastfeeding:
        bits.append("breastfeeding")
    lines = [", ".join(bits).capitalize() + "." if bits else "No patient context entered."]
    if p.allergies:
        lines.append("Allergies: " + ", ".join(p.allergies) + ".")
    if p.current_medications:
        lines.append("Current medicines: " + ", ".join(p.current_medications) + ".")
    return lines


def render_note(audit: dict[str, Any], fmt: str = "markdown") -> str:
    req = SubstitutionRequest.model_validate(audit["request"])
    rec = Recommendation.model_validate(audit["response"])
    md = fmt == "markdown"
    h = (lambda t: f"## {t}") if md else (lambda t: t.upper())
    b = (lambda t: f"**{t}**") if md else (lambda t: t)
    bullet = "- " if md else "* "
    name = rec.query.display_name if rec.query else req.drug

    out: list[str] = [
        f"# Shortage substitution review: {name}" if md else f"SHORTAGE SUBSTITUTION REVIEW: {name.upper()}",
        "",
        f"Reference {rec.request_id}, generated {rec.created_at:%d %b %Y %H:%M} UTC by Shortfall Bridge AI "
        f"{rec.engine_version or ''}".rstrip() + ".",
        "",
        h("Request"),
        f"{b('Drug')}: {req.drug}" + (f", {req.route}" if req.route else "") + (f", {req.dosage_form}" if req.dosage_form else "")
        + (f", {req.strength}" if req.strength else "") + ".",
    ]
    if req.current_dose:
        d = req.current_dose
        out.append(f"{b('Current dose')}: {d.amount:g} {d.unit}" + (f" {d.frequency}" if d.frequency else "") + ".")
    out += [f"{b('Patient')}: " + " ".join(_patient_lines(req, rec))]
    if req.clinical_question:
        out.append(f"{b('Question')}: {req.clinical_question}")
    out.append("")

    out.append(h("Shortage status"))
    s = rec.shortage
    if s is None:
        out.append("Not evaluated.")
    elif s.in_shortage:
        out.append(f"In current FDA shortage: {s.matching_records} entr{'y' if s.matching_records == 1 else 'ies'}"
                   + (f", last updated {s.last_updated:%d %b %Y}" if s.last_updated else "") + "."
                   + (f" Reason: {'; '.join(s.reasons)}." if s.reasons else ""))
    else:
        out.append("No current FDA shortage entry.")
    out.append("")

    if rec.status.value == "abstain":
        out.append(h("Outcome"))
        out += [bullet + r for r in rec.abstain_reasons]
    else:
        out.append(h("Options reviewed, ranked"))
        for i, c in enumerate(rec.candidates[:5], 1):
            dc = c.dose_conversion
            dose = ""
            if dc and dc.feasible and dc.suggested_dose:
                dose = f"; suggested start {dc.suggested_dose}"
                if dc.suggested_daily_total:
                    dose += f" (daily total {dc.suggested_daily_total})"
            flags = [f"{_GATE_LABEL[g.gate.value]} {_STATUS[g.status.value]}" for g in c.gates
                     if g.status is not GateStatus.PASS]
            out.append(f"{i}. {b(c.display_name)}, {c.tier.label.lower()}{dose}."
                       + (f" Checks: {', '.join(flags)}." if flags else " All eight checks clear."))
            if dc and dc.feasible and dc.warnings:
                out.append("   Dose notes: " + " ".join(dc.warnings[:2]))
    if rec.not_recommended:
        out += ["", h("Not recommended for this patient")]
        for c in rec.not_recommended:
            reasons = "; ".join(g.rationale for g in c.gates if g.status is GateStatus.BLOCK)
            out.append(f"{bullet}{c.display_name}: {reasons}")
    if rec.warnings:
        out += ["", h("Notes")] + [bullet + w for w in rec.warnings]

    cited = list(rec.citations.values())
    if cited:
        out += ["", h("Label evidence cited")]
        for i, c in enumerate(cited, 1):
            when = f", effective {c.effective_time:%d %b %Y}" if c.effective_time else ""
            link = f"[DailyMed]({c.dailymed_url})" if md else c.dailymed_url
            out.append(f"{i}. {c.title}, {c.section.value.replace('_', ' ')}{when}. {link}")

    out += ["", h("Data and integrity")]
    versions = {k: v for k, v in rec.data_versions.items() if k != "timings_ms"}
    if versions:
        out.append("Data versions: " + "; ".join(f"{k} {v}" for k, v in sorted(versions.items())) + ".")
    if audit.get("record_hash"):
        out.append(f"Audit record {audit.get('seq', '')} hash {audit['record_hash']}.")
    out += ["", rec.disclaimer,
            "Reviewed by: ____________________   Role: ____________   Date and time: ____________"]
    return "\n".join(out).rstrip() + "\n"
