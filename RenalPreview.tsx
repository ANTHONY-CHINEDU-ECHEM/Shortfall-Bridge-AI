import { useEffect, useState } from "react";
import { api } from "../api";
import type { RenalEstimate } from "../types";
import { type OrderState, toPatient } from "./OrderPanel";

const SOURCE: Record<string, string> = {
  cockcroft_gault: "Cockcroft-Gault", ckd_epi_2021: "CKD-EPI 2021", bedside_schwartz: "bedside Schwartz",
  dialysis: "dialysis", entered_crcl: "entered CrCl", entered_egfr: "entered eGFR",
};

/** Live preview of the renal value the engine will use. Computed by the engine itself, never in the UI. */
export function RenalPreview({ order }: { order: OrderState }) {
  const [est, setEst] = useState<RenalEstimate | null>(null);
  const relevant = [order.age, order.weight, order.height, order.sex, order.scr, order.scrUnit, order.dialysis,
    order.crcl, order.egfr];
  useEffect(() => {
    const p = toPatient(order);
    if (p.serum_creatinine == null && !p.on_dialysis && p.creatinine_clearance_ml_min == null && p.egfr_ml_min_1_73m2 == null) {
      setEst(null);
      return;
    }
    let live = true;
    const t = window.setTimeout(() => {
      api.renal(p).then((e) => live && setEst(e)).catch(() => live && setEst(null));
    }, 300);
    return () => { live = false; window.clearTimeout(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, relevant);

  if (!est) return null;
  if (est.value_ml_min == null) {
    return <p className="renal-preview renal-preview--empty" aria-live="polite">
      {est.warnings[0] ?? "Enter age and serum creatinine to estimate renal function."}</p>;
  }
  return (
    <div className="renal-preview" aria-live="polite">
      <p>
        <strong>{est.value_ml_min.toLocaleString("en-GB")} mL/min</strong>{" "}
        <span className="muted">{SOURCE[est.source] ?? est.source}{est.weight_used ? `, ${est.weight_used}` : ""}</span>
      </p>
      {est.egfr_ckd_epi_2021 != null && est.source === "cockcroft_gault" && (
        <p className="muted">CKD-EPI 2021 eGFR {est.egfr_ckd_epi_2021} mL/min/1.73 m²</p>
      )}
      {est.warnings.map((w) => <p key={w} className="renal-preview__warn">{w}</p>)}
    </div>
  );
}
