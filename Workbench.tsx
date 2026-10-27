import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { EMPTY_ORDER, OrderPanel, type OrderState, toRequest, validate } from "../components/OrderPanel";
import { Results } from "../components/Results";
import type { Citation, Recommendation } from "../types";

const STAGES = ["Matching the drug", "Checking FDA shortages", "Finding alternatives", "Running safety checks", "Reading label evidence"];

const STARTERS: { label: string; order: Partial<OrderState> }[] = [
  { label: "IV furosemide, CKD stage 3b", order: { drug: "furosemide", route: "intravenous", doseAmount: "40", frequency: "twice daily", crcl: "42", age: "72", meds: ["lisinopril"] } },
  { label: "Methylprednisolone 125 mg IV", order: { drug: "methylprednisolone", route: "intravenous", doseAmount: "125" } },
  { label: "Amoxicillin suspension, penicillin allergy", order: { drug: "amoxicillin", route: "oral", dosageForm: "suspension", age: "6", allergies: ["penicillin"] } },
];

interface Props {
  prefill: Partial<OrderState> | null;
  consumePrefill: () => void;
}

export function Workbench({ prefill, consumePrefill }: Props) {
  const [order, setOrder] = useState<OrderState>(EMPTY_ORDER);
  const [errors, setErrors] = useState<Partial<Record<keyof OrderState, string>>>({});
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [drawer, setDrawer] = useState<{ ids: string[]; heading: string; focus: string | null } | null>(null);
  const drugRef = useRef<HTMLInputElement>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (prefill) {
      setOrder({ ...EMPTY_ORDER, ...prefill });
      consumePrefill();
      drugRef.current?.focus();
    }
  }, [prefill, consumePrefill]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) {
        e.preventDefault();
        drugRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!busy) return;
    setStage(0);
    const t = window.setInterval(() => setStage((s) => Math.min(s + 1, STAGES.length - 1)), 650);
    return () => window.clearInterval(t);
  }, [busy]);

  const submit = useCallback(async () => {
    const e = validate(order);
    setErrors(e);
    if (Object.keys(e).length) {
      const first = Object.keys(e)[0];
      if (first === "drug") drugRef.current?.focus();
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      const r = await api.recommend(toRequest(order));
      setRec(r);
      window.requestAnimationFrame(() => resultsHeading.current?.focus());
    } catch (err) {
      setFailure(err instanceof ApiError ? err : new ApiError(0, String(err)));
    } finally {
      setBusy(false);
    }
  }, [order]);

  const citations: Citation[] = drawer && rec ? drawer.ids.map((id) => rec.citations[id]).filter(Boolean) : [];

  return (
    <div className="workbench">
      <aside className="workbench__order" aria-label="Request">
        <OrderPanel ref={drugRef} order={order} setOrder={setOrder} onSubmit={submit} busy={busy} errors={errors} />
      </aside>
      <section className="workbench__out" aria-labelledby="results-title" aria-busy={busy}>
        <h1 id="results-title" ref={resultsHeading} tabIndex={-1} className="out-title">
          {rec?.query ? `Alternatives for ${rec.query.display_name}` : "Shortage substitution"}
        </h1>
        <div aria-live="polite" className="visually-hidden">
          {busy ? STAGES[stage] : rec ? `${rec.candidates.length} options found, ${rec.not_recommended.length} not recommended.` : ""}
        </div>
        {busy && (
          <ol className="progress" aria-hidden="true">
            {STAGES.map((s, i) => (
              <li key={s} className={i < stage ? "is-done" : i === stage ? "is-now" : ""}>{s}</li>
            ))}
          </ol>
        )}
        {!busy && failure && (
          <div className="error-panel" role="alert">
            <h2>The request did not complete</h2>
            <p>{failure.message}</p>
            {failure.requestId && <p className="muted">Reference {failure.requestId}</p>}
            <button className="btn btn--secondary" onClick={submit}>Try again</button>
          </div>
        )}
        {!busy && !failure && rec && (
          <Results rec={rec} openEvidence={(ids, heading, focus) => setDrawer({ ids, heading, focus: focus ?? null })} />
        )}
        {!busy && !failure && !rec && (
          <div className="empty">
            <p className="empty__lead">
              Enter the drug that is short. Shortfall Bridge checks the FDA shortage list, finds equivalent products and
              alternatives, and runs eight safety checks against each one using the official label text.
            </p>
            <p className="muted">Start from a recent shortage:</p>
            <ul className="starters">
              {STARTERS.map((s) => (
                <li key={s.label}>
                  <button className="btn btn--quiet" onClick={() => { setOrder({ ...EMPTY_ORDER, ...s.order }); drugRef.current?.focus(); }}>
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
      {drawer && (
        <EvidenceDrawer citations={citations} focusId={drawer.focus} heading={drawer.heading} onClose={() => setDrawer(null)} />
      )}
    </div>
  );
}
