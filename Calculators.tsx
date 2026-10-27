import { useEffect, useId, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { CreatinineUnit, DoseConversion, EquivalenceTable, RenalEstimate, Sex } from "../types";
import { titleCase } from "../labels";

const ROUTE_LABEL: Record<string, string> = { oral: "Oral", intravenous: "Intravenous", injection: "Injection" };

function DoseConverter({ tables }: { tables: EquivalenceTable[] }) {
  const id = useId();
  const [tableId, setTableId] = useState(tables[0]?.id ?? "");
  const table = tables.find((t) => t.id === tableId);
  const drugs = useMemo(() => (table ? [...Object.keys(table.members), ...Object.keys(table.excluded)] : []), [table]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [dose, setDose] = useState("");
  const [freq, setFreq] = useState("");
  const [fromRoute, setFromRoute] = useState("oral");
  const [toRoute, setToRoute] = useState("oral");
  const [out, setOut] = useState<DoseConversion | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!table) return;
    const keys = Object.keys(table.members);
    setFrom(keys[0] ?? "");
    setTo(keys[1] ?? keys[0] ?? "");
    setOut(null);
  }, [table]);

  const routesFor = (drug: string) => {
    const m = table?.members[drug];
    if (!m || table?.type === "band") return ["oral"];
    return Object.keys(m);
  };

  const run = async () => {
    setError(null);
    const amount = Number(dose);
    if (!Number.isFinite(amount) || amount <= 0) { setError("Enter the current dose as a positive number."); return; }
    try {
      setOut(await api.convert({ from_drug: from, to_drug: to, dose: { amount, unit: "mg", frequency: freq || null },
                                 from_route: fromRoute, to_route: toRoute }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  if (!table) return null;
  return (
    <section className="panel calc" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Dose equivalence</h2>
      <p className="muted">The same published tables the engine uses. A starting point to titrate, never a final dose.</p>
      <form className="calc__form" onSubmit={(e) => { e.preventDefault(); void run(); }}>
        <div className="field">
          <label htmlFor={`${id}-t`}>Reference table</label>
          <select id={`${id}-t`} value={tableId} onChange={(e) => setTableId(e.target.value)}>
            {tables.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
          </select>
        </div>
        <div className="row-2">
          <div className="field">
            <label htmlFor={`${id}-f`}>From</label>
            <select id={`${id}-f`} value={from} onChange={(e) => { setFrom(e.target.value); setFromRoute(routesFor(e.target.value)[0]); }}>
              {drugs.map((d) => <option key={d} value={d}>{titleCase(d)}{table.excluded[d] ? " (refused)" : ""}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-fr`}>Route</label>
            <select id={`${id}-fr`} value={fromRoute} onChange={(e) => setFromRoute(e.target.value)}>
              {routesFor(from).map((r) => <option key={r} value={r}>{ROUTE_LABEL[r] ?? r}</option>)}
            </select>
          </div>
        </div>
        <div className="row-2">
          <div className="field">
            <label htmlFor={`${id}-to`}>To</label>
            <select id={`${id}-to`} value={to} onChange={(e) => { setTo(e.target.value); setToRoute(routesFor(e.target.value)[0]); }}>
              {drugs.map((d) => <option key={d} value={d}>{titleCase(d)}{table.excluded[d] ? " (refused)" : ""}</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-tr`}>Route</label>
            <select id={`${id}-tr`} value={toRoute} onChange={(e) => setToRoute(e.target.value)}>
              {routesFor(to).map((r) => <option key={r} value={r}>{ROUTE_LABEL[r] ?? r}</option>)}
            </select>
          </div>
        </div>
        <div className="row-2">
          <div className="field">
            <label htmlFor={`${id}-d`}>Current dose, {table.unit}</label>
            <input id={`${id}-d`} inputMode="decimal" value={dose} onChange={(e) => setDose(e.target.value)} placeholder="40" />
          </div>
          <div className="field">
            <label htmlFor={`${id}-q`}>Frequency, optional</label>
            <input id={`${id}-q`} value={freq} onChange={(e) => setFreq(e.target.value)} placeholder="twice daily" />
          </div>
        </div>
        <button className="btn btn--primary" type="submit">Convert</button>
      </form>
      {error && <p className="field__error" role="alert">{error}</p>}
      {out && (
        <div className={`dose-card ${out.feasible ? "" : "dose-card--refused"}`} aria-live="polite">
          <h3>{out.feasible ? `${out.suggested_dose} ${out.to_drug}` : "Conversion not provided"}</h3>
          {out.suggested_range && <p className="muted">{out.suggested_range}</p>}
          {out.warnings.length > 0 && <ul className="dose-card__warn">{out.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
          {out.reference && <p className="dose-card__ref">Reference: {out.reference}</p>}
        </div>
      )}
      <p className="muted"><a href={table.reference_url} target="_blank" rel="noreferrer">Open the reference</a></p>
    </section>
  );
}

function RenalCalculator() {
  const id = useId();
  const [age, setAge] = useState("");
  const [weight, setWeight] = useState("");
  const [height, setHeight] = useState("");
  const [sex, setSex] = useState<Sex>("unknown");
  const [scr, setScr] = useState("");
  const [unit, setUnit] = useState<CreatinineUnit>("mg/dL");
  const [out, setOut] = useState<RenalEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = (s: string) => (s.trim() === "" ? null : Number(s));

  const run = async () => {
    setError(null);
    try {
      setOut(await api.renal({ age_years: n(age), weight_kg: n(weight), height_cm: n(height), sex, serum_creatinine: n(scr),
        serum_creatinine_unit: unit, hepatic_impairment: "none", pregnant: false, breastfeeding: false, allergies: [],
        current_medications: [] }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <section className="panel calc" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>Renal function</h2>
      <p className="muted">Cockcroft-Gault with ideal or adjusted body weight, CKD-EPI 2021 (race free) and bedside Schwartz for children.</p>
      <form className="calc__form" onSubmit={(e) => { e.preventDefault(); void run(); }}>
        <div className="row-2">
          <div className="field"><label htmlFor={`${id}-a`}>Age, years</label>
            <input id={`${id}-a`} inputMode="decimal" value={age} onChange={(e) => setAge(e.target.value)} /></div>
          <div className="field"><label htmlFor={`${id}-s`}>Sex</label>
            <select id={`${id}-s`} value={sex} onChange={(e) => setSex(e.target.value as Sex)}>
              <option value="unknown">Not entered</option><option value="female">Female</option><option value="male">Male</option>
            </select></div>
        </div>
        <div className="row-2">
          <div className="field"><label htmlFor={`${id}-w`}>Weight, kg</label>
            <input id={`${id}-w`} inputMode="decimal" value={weight} onChange={(e) => setWeight(e.target.value)} /></div>
          <div className="field"><label htmlFor={`${id}-h2`}>Height, cm</label>
            <input id={`${id}-h2`} inputMode="decimal" value={height} onChange={(e) => setHeight(e.target.value)} /></div>
        </div>
        <div className="field">
          <span className="field__label" id={`${id}-c`}>Serum creatinine</span>
          <div className="dose dose--2" role="group" aria-labelledby={`${id}-c`}>
            <input aria-label="Serum creatinine value" inputMode="decimal" value={scr} onChange={(e) => setScr(e.target.value)} />
            <select aria-label="Unit" value={unit} onChange={(e) => setUnit(e.target.value as CreatinineUnit)}>
              <option value="mg/dL">mg/dL</option><option value="umol/L">umol/L</option>
            </select>
          </div>
        </div>
        <button className="btn btn--primary" type="submit">Estimate</button>
      </form>
      {error && <p className="field__error" role="alert">{error}</p>}
      {out && (
        <div className="dose-card" aria-live="polite">
          <h3>{out.value_ml_min != null ? `${out.value_ml_min} mL/min` : "Not enough data"}</h3>
          {out.method && <p>{out.method}{out.weight_used ? `, ${out.weight_used}` : ""}</p>}
          {out.formula && <p className="muted">{out.formula}</p>}
          {out.egfr_ckd_epi_2021 != null && <p className="muted">CKD-EPI 2021 eGFR {out.egfr_ckd_epi_2021} mL/min/1.73 m²</p>}
          {out.warnings.length > 0 && <ul className="dose-card__warn">{out.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        </div>
      )}
    </section>
  );
}

export function Calculators() {
  const [tables, setTables] = useState<EquivalenceTable[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.equivalenceTables().then(setTables).catch((e) => setError(String(e.message ?? e))); }, []);
  return (
    <div className="page">
      <header className="page__head">
        <h1>Calculators</h1>
        <p className="muted">The engine's own dose equivalence and renal function calculations, available on their own.</p>
      </header>
      {error && <div className="error-panel" role="alert"><p>{error}</p></div>}
      <div className="grid-2">
        {tables && <DoseConverter tables={tables} />}
        <RenalCalculator />
      </div>
    </div>
  );
}
