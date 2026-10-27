import { forwardRef, useId, useState } from "react";
import type { HepaticImpairment, SubstitutionRequest } from "../types";
import { ChipInput } from "./ChipInput";
import { DrugCombobox } from "./DrugCombobox";

export interface OrderState {
  drug: string;
  route: string;
  dosageForm: string;
  strength: string;
  doseAmount: string;
  doseUnit: string;
  frequency: string;
  age: string;
  weight: string;
  crcl: string;
  egfr: string;
  hepatic: HepaticImpairment;
  pregnant: boolean;
  breastfeeding: boolean;
  allergies: string[];
  meds: string[];
  question: string;
  includeAltClass: boolean;
}

export const EMPTY_ORDER: OrderState = {
  drug: "", route: "", dosageForm: "", strength: "", doseAmount: "", doseUnit: "mg", frequency: "",
  age: "", weight: "", crcl: "", egfr: "", hepatic: "none", pregnant: false, breastfeeding: false,
  allergies: [], meds: [], question: "", includeAltClass: true,
};

const ROUTES = ["", "oral", "intravenous", "intramuscular", "subcutaneous", "inhalation", "topical", "transdermal", "ophthalmic"];

const num = (s: string) => (s.trim() === "" ? null : Number(s));

export function toRequest(o: OrderState): SubstitutionRequest {
  const amount = num(o.doseAmount);
  return {
    drug: o.drug.trim(),
    route: o.route || null,
    dosage_form: o.dosageForm.trim() || null,
    strength: o.strength.trim() || null,
    current_dose: amount ? { amount, unit: o.doseUnit, frequency: o.frequency.trim() || null } : null,
    patient: {
      age_years: num(o.age), weight_kg: num(o.weight),
      creatinine_clearance_ml_min: num(o.crcl), egfr_ml_min_1_73m2: num(o.egfr),
      hepatic_impairment: o.hepatic, pregnant: o.pregnant, breastfeeding: o.breastfeeding,
      allergies: o.allergies, current_medications: o.meds,
    },
    clinical_question: o.question.trim() || null,
    max_candidates: 8,
    include_alternative_classes: o.includeAltClass,
  };
}

export function validate(o: OrderState): Partial<Record<keyof OrderState, string>> {
  const e: Partial<Record<keyof OrderState, string>> = {};
  if (o.drug.trim().length < 2) e.drug = "Enter the drug in shortage, at least two characters.";
  const range = (k: keyof OrderState, v: string, lo: number, hi: number, what: string) => {
    if (v.trim() === "") return;
    const n = Number(v);
    if (!Number.isFinite(n) || n < lo || n > hi) e[k] = `${what} must be a number from ${lo} to ${hi}.`;
  };
  range("doseAmount", o.doseAmount, 0.001, 100000, "Dose");
  range("age", o.age, 0, 125, "Age");
  range("weight", o.weight, 0.3, 400, "Weight");
  range("crcl", o.crcl, 0, 250, "Creatinine clearance");
  range("egfr", o.egfr, 0, 250, "eGFR");
  if (o.pregnant && o.age && Number(o.age) < 10) e.age = "Check age: pregnancy is recorded for a patient under 10.";
  return e;
}

interface Props {
  order: OrderState;
  setOrder: (o: OrderState) => void;
  onSubmit: () => void;
  busy: boolean;
  errors: Partial<Record<keyof OrderState, string>>;
}

export const OrderPanel = forwardRef<HTMLInputElement, Props>(function OrderPanel(
  { order, setOrder, onSubmit, busy, errors },
  drugRef,
) {
  const id = useId();
  const [patientOpen, setPatientOpen] = useState(true);
  const set = <K extends keyof OrderState>(k: K, v: OrderState[K]) => setOrder({ ...order, [k]: v });
  const err = (k: keyof OrderState) =>
    errors[k] ? <p className="field__error" id={`${id}-${k}-err`} role="alert">{errors[k]}</p> : null;
  const describe = (k: keyof OrderState) => (errors[k] ? `${id}-${k}-err` : undefined);
  const contextCount =
    [order.age, order.weight, order.crcl, order.egfr].filter(Boolean).length +
    (order.hepatic !== "none" ? 1 : 0) + (order.pregnant ? 1 : 0) + (order.breastfeeding ? 1 : 0) +
    order.allergies.length + order.meds.length;

  return (
    <form
      className="order"
      aria-label="Substitution request"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <fieldset className="order__group">
        <legend>Drug in shortage</legend>
        <div className="field">
          <label htmlFor="drug-input">
            Drug <span className="req" aria-hidden="true">required</span>
          </label>
          <DrugCombobox ref={drugRef} value={order.drug} onChange={(v) => set("drug", v)} invalid={!!errors.drug} describedBy={describe("drug")} />
          {err("drug")}
          <p className="field__hint">Press <kbd>/</kbd> from anywhere to jump here.</p>
        </div>
        <div className="row-2">
          <div className="field">
            <label htmlFor={`${id}-route`}>Route</label>
            <select id={`${id}-route`} value={order.route} onChange={(e) => set("route", e.target.value)}>
              {ROUTES.map((r) => (
                <option key={r} value={r}>{r ? r[0].toUpperCase() + r.slice(1) : "Infer from shortage"}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-form`}>Dosage form</label>
            <input id={`${id}-form`} value={order.dosageForm} placeholder="e.g. injection" onChange={(e) => set("dosageForm", e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label htmlFor={`${id}-strength`}>Strength</label>
          <input id={`${id}-strength`} value={order.strength} placeholder="e.g. 10 mg/mL" onChange={(e) => set("strength", e.target.value)} />
        </div>
        <div className="field">
          <span className="field__label" id={`${id}-dose-l`}>Current dose</span>
          <div className="dose" role="group" aria-labelledby={`${id}-dose-l`}>
            <input aria-label="Dose amount" inputMode="decimal" value={order.doseAmount} placeholder="40"
              aria-invalid={!!errors.doseAmount || undefined} aria-describedby={describe("doseAmount")}
              onChange={(e) => set("doseAmount", e.target.value)} />
            <select aria-label="Dose unit" value={order.doseUnit} onChange={(e) => set("doseUnit", e.target.value)}>
              <option value="mg">mg</option>
              <option value="mcg">mcg</option>
              <option value="g">g</option>
            </select>
            <input aria-label="Frequency" value={order.frequency} placeholder="twice daily" onChange={(e) => set("frequency", e.target.value)} />
          </div>
          {err("doseAmount")}
          <p className="field__hint">Needed to calculate an equivalent starting dose.</p>
        </div>
      </fieldset>

      <fieldset className="order__group">
        <legend>
          <button type="button" className="disclosure" aria-expanded={patientOpen} onClick={() => setPatientOpen(!patientOpen)}>
            Patient context
            {contextCount > 0 && <span className="count" aria-label={`${contextCount} items entered`}>{contextCount}</span>}
          </button>
        </legend>
        {patientOpen && (
          <div className="order__patient">
            <p className="field__hint">Safety checks show <em>Needs data</em> until the relevant value is entered. Nothing is assumed.</p>
            <div className="row-2">
              <div className="field">
                <label htmlFor={`${id}-age`}>Age, years</label>
                <input id={`${id}-age`} inputMode="decimal" value={order.age} aria-invalid={!!errors.age || undefined}
                  aria-describedby={describe("age")} onChange={(e) => set("age", e.target.value)} />
                {err("age")}
              </div>
              <div className="field">
                <label htmlFor={`${id}-wt`}>Weight, kg</label>
                <input id={`${id}-wt`} inputMode="decimal" value={order.weight} aria-invalid={!!errors.weight || undefined}
                  aria-describedby={describe("weight")} onChange={(e) => set("weight", e.target.value)} />
                {err("weight")}
              </div>
            </div>
            <div className="row-2">
              <div className="field">
                <label htmlFor={`${id}-crcl`}>CrCl, mL/min</label>
                <input id={`${id}-crcl`} inputMode="decimal" value={order.crcl} aria-invalid={!!errors.crcl || undefined}
                  aria-describedby={describe("crcl")} onChange={(e) => set("crcl", e.target.value)} />
                {err("crcl")}
              </div>
              <div className="field">
                <label htmlFor={`${id}-egfr`}>eGFR</label>
                <input id={`${id}-egfr`} inputMode="decimal" value={order.egfr} aria-invalid={!!errors.egfr || undefined}
                  aria-describedby={describe("egfr")} onChange={(e) => set("egfr", e.target.value)} />
                {err("egfr")}
              </div>
            </div>
            <p className="field__hint">Most US labels dose by creatinine clearance, so CrCl is used when both are entered.</p>
            <div className="field">
              <label htmlFor={`${id}-hep`}>Hepatic impairment</label>
              <select id={`${id}-hep`} value={order.hepatic} onChange={(e) => set("hepatic", e.target.value as HepaticImpairment)}>
                <option value="none">None known</option>
                <option value="mild">Mild (Child Pugh A)</option>
                <option value="moderate">Moderate (Child Pugh B)</option>
                <option value="severe">Severe (Child Pugh C)</option>
              </select>
            </div>
            <div className="checks">
              <label className="check">
                <input type="checkbox" checked={order.pregnant} onChange={(e) => set("pregnant", e.target.checked)} /> Pregnant
              </label>
              <label className="check">
                <input type="checkbox" checked={order.breastfeeding} onChange={(e) => set("breastfeeding", e.target.checked)} /> Breastfeeding
              </label>
            </div>
            <ChipInput label="Allergies" hint="Drug or class, for example penicillin. Press Enter after each." values={order.allergies}
              onChange={(v) => set("allergies", v)} placeholder="Add an allergy" />
            <ChipInput label="Current medicines" hint="Checked against both labels' interaction sections." values={order.meds}
              onChange={(v) => set("meds", v)} placeholder="Add a medicine" />
          </div>
        )}
      </fieldset>

      <fieldset className="order__group">
        <legend>Question</legend>
        <div className="field">
          <label htmlFor={`${id}-q`}>Clinical question, optional</label>
          <textarea id={`${id}-q`} rows={3} value={order.question} maxLength={1000}
            placeholder="e.g. Patient on haemodialysis, needs IV option"
            onChange={(e) => set("question", e.target.value)} />
        </div>
        <label className="check">
          <input type="checkbox" checked={order.includeAltClass} onChange={(e) => set("includeAltClass", e.target.checked)} />
          Include drugs from other classes
        </label>
      </fieldset>

      <div className="order__actions">
        <button type="submit" className="btn btn--primary" disabled={busy} aria-busy={busy}>
          {busy ? "Checking alternatives" : "Find alternatives"}
        </button>
        <button type="button" className="btn btn--quiet" onClick={() => setOrder(EMPTY_ORDER)} disabled={busy}>
          Clear
        </button>
      </div>
    </form>
  );
});
