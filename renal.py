"""Renal function estimation from routinely available patient data.

Most US labels state renal thresholds as creatinine clearance (CrCl, mL/min) estimated by the
Cockcroft-Gault equation, so that is the primary estimate. CKD-EPI 2021 (race free) eGFR is
reported alongside it, and the bedside Schwartz equation is used for children because
Cockcroft-Gault is not validated below 18 years.

Every estimate records the equation, the weight it used and its caveats, and the engine shows
that record to the clinician. When required inputs are missing the estimator returns ``None``
rather than assuming values, so the renal gate stays at Needs data.

References
    Cockcroft DW, Gault MH. Prediction of creatinine clearance from serum creatinine.
        Nephron 1976;16:31-41.
    Inker LA, Eneanya ND, Coresh J, et al. New creatinine- and cystatin C-based equations to
        estimate GFR without race. N Engl J Med 2021;385:1737-1749.
    Schwartz GJ, Munoz A, Schneider MF, et al. New equations to estimate GFR in children with
        CKD. J Am Soc Nephrol 2009;20:629-637.
    Devine BJ. Gentamicin therapy. Drug Intell Clin Pharm 1974;8:650-655 (ideal body weight).
"""

from __future__ import annotations

from ..domain.enums import CreatinineUnit, Sex
from ..domain.models import PatientContext, RenalEstimate

UMOL_PER_MG_DL = 88.42
DIALYSIS_EQUIVALENT_ML_MIN = 5.0


def creatinine_mg_dl(value: float, unit: CreatinineUnit) -> float:
    return value / UMOL_PER_MG_DL if unit is CreatinineUnit.UMOL_L else value


def ideal_body_weight(height_cm: float, sex: Sex) -> float:
    """Devine formula: 50 kg (male) or 45.5 kg (female) plus 2.3 kg per inch above 5 feet."""
    base = 50.0 if sex is Sex.MALE else 45.5
    inches_over_five_feet = max(0.0, (height_cm - 152.4) / 2.54)
    return base + 2.3 * inches_over_five_feet


def choose_weight(weight_kg: float, height_cm: float | None, sex: Sex) -> tuple[float, str, list[str]]:
    """Weight for Cockcroft-Gault following common pharmacy practice.

    Actual weight when it is below ideal weight; ideal weight when actual is within 120 percent
    of ideal; adjusted weight (IBW + 0.4 x excess) when actual exceeds 120 percent of ideal.
    Without height only actual weight is available, which overestimates CrCl in obesity.
    """
    if height_cm is None or sex is Sex.UNKNOWN:
        return weight_kg, f"actual body weight {weight_kg:g} kg", [
            "Height or sex not entered, so actual body weight was used; this overestimates clearance in obesity."
        ]
    ibw = ideal_body_weight(height_cm, sex)
    if weight_kg < ibw:
        return weight_kg, f"actual body weight {weight_kg:g} kg (below ideal {ibw:.1f} kg)", []
    if weight_kg > 1.2 * ibw:
        adj = ibw + 0.4 * (weight_kg - ibw)
        return adj, f"adjusted body weight {adj:.1f} kg (actual {weight_kg:g} kg, ideal {ibw:.1f} kg)", []
    return ibw, f"ideal body weight {ibw:.1f} kg", []


def cockcroft_gault(age: float, weight_kg: float, scr_mg_dl: float, female: bool) -> float:
    crcl = (140.0 - age) * weight_kg / (72.0 * scr_mg_dl)
    return crcl * 0.85 if female else crcl


def ckd_epi_2021(age: float, scr_mg_dl: float, female: bool) -> float:
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = scr_mg_dl / kappa
    egfr = 142.0 * min(ratio, 1.0) ** alpha * max(ratio, 1.0) ** -1.200 * 0.9938**age
    return egfr * 1.012 if female else egfr


def bedside_schwartz(height_cm: float, scr_mg_dl: float) -> float:
    return 0.413 * height_cm / scr_mg_dl


def estimate_renal_function(p: PatientContext) -> RenalEstimate:
    """Return the renal function value the gates should use, with full provenance."""
    if p.on_dialysis:
        return RenalEstimate(
            value_ml_min=DIALYSIS_EQUIVALENT_ML_MIN, source="dialysis", method="dialysis recorded",
            warnings=["Patient is on dialysis: treated as end stage renal disease for every labelled threshold. "
                      "Check the label's dialysis specific dosing and timing relative to sessions."],
        )
    if p.creatinine_clearance_ml_min is not None:
        return RenalEstimate(value_ml_min=p.creatinine_clearance_ml_min, source="entered_crcl",
                             method="creatinine clearance entered by the user")
    if p.egfr_ml_min_1_73m2 is not None:
        return RenalEstimate(
            value_ml_min=p.egfr_ml_min_1_73m2, source="entered_egfr", method="eGFR entered by the user",
            warnings=["eGFR is normalised to 1.73 m2. Labels usually dose by Cockcroft-Gault CrCl; "
                      "the two can differ materially at extremes of body size and in older adults."],
        )
    if p.serum_creatinine is None or p.age_years is None:
        return RenalEstimate(value_ml_min=None, source="none")

    scr = creatinine_mg_dl(p.serum_creatinine, p.serum_creatinine_unit)
    warnings: list[str] = []

    if p.age_years < 18:
        if p.height_cm is None:
            return RenalEstimate(value_ml_min=None, source="none",
                                 warnings=["Height is required for the paediatric (bedside Schwartz) estimate."])
        value = bedside_schwartz(p.height_cm, scr)
        return RenalEstimate(
            value_ml_min=round(value, 1), source="bedside_schwartz", method="bedside Schwartz eGFR",
            formula=f"0.413 x {p.height_cm:g} cm / {scr:.2f} mg/dL",
            warnings=["Paediatric estimate (mL/min/1.73 m2). Confirm paediatric dosing in the label."],
        )

    if p.sex is Sex.UNKNOWN:
        warnings.append("Sex not entered: the lower (female) estimate is used, which is the more cautious value.")
    female = p.sex is not Sex.MALE

    egfr = round(ckd_epi_2021(p.age_years, scr, female), 1)
    if p.weight_kg is None:
        warnings.append("Weight not entered, so Cockcroft-Gault cannot be calculated; CKD-EPI 2021 eGFR is used.")
        return RenalEstimate(value_ml_min=egfr, source="ckd_epi_2021", method="CKD-EPI 2021 eGFR (race free)",
                             formula=f"CKD-EPI 2021, age {p.age_years:g}, SCr {scr:.2f} mg/dL",
                             egfr_ckd_epi_2021=egfr, warnings=warnings)

    weight, weight_note, weight_warnings = choose_weight(p.weight_kg, p.height_cm, p.sex)
    warnings.extend(weight_warnings)
    crcl = cockcroft_gault(p.age_years, weight, scr, female)
    if p.age_years >= 65 and scr < 0.7:
        warnings.append("Low serum creatinine in an older adult may reflect low muscle mass and overestimate clearance.")
    if p.age_years >= 90:
        warnings.append("Cockcroft-Gault is poorly validated above 90 years.")
    return RenalEstimate(
        value_ml_min=round(crcl, 1), source="cockcroft_gault", method="Cockcroft-Gault creatinine clearance",
        weight_used=weight_note,
        formula=f"(140 - {p.age_years:g}) x {weight:.1f} kg{' x 0.85' if female else ''} / (72 x {scr:.2f} mg/dL)",
        egfr_ckd_epi_2021=egfr, warnings=warnings,
    )


def with_estimated_renal_function(p: PatientContext, estimate: RenalEstimate) -> PatientContext:
    """A copy of the patient context carrying the estimate as creatinine clearance for the gates."""
    if estimate.value_ml_min is None or estimate.source in {"entered_crcl", "entered_egfr"}:
        return p
    return p.model_copy(update={"creatinine_clearance_ml_min": estimate.value_ml_min})
