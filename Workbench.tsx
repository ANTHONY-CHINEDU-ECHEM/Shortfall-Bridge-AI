import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { EMPTY_ORDER, OrderPanel, type OrderState, toRequest, validate } from "../components/OrderPanel";
import { Results } from "../components/Results";
import { engine, type EngineState } from "../engine/transport";
import { clearRecent, pushRecent, readRecent, type RecentItem } from "../history";
import { shareUrl } from "../share";
import type { Citation, Recommendation } from "../types";

const STAGES = ["Matching the drug", "Checking FDA shortages", "Finding alternatives", "Running safety checks", "Reading label evidence"];

const STARTERS: { label: string; order: Partial<OrderState> }[] = [
  { label: "IV furosemide, CKD stage 3b", order: { drug: "furosemide", route: "intravenous", doseAmount: "40", frequency: "twice daily", crcl: "42", age: "72", meds: ["lisinopril"] } },
  { label: "Methylprednisolone 125 mg IV", order: { drug: "methylprednisolone", route: "intravenous", doseAmount: "125" } },
  { label: "Atorvastatin, patient on clarithromycin", order: { drug: "atorvastatin", route: "oral", doseAmount: "40", frequency: "once daily", meds: ["clarithromycin"] } },
  { label: "Renal estimate from creatinine (84 y, 52 kg)", order: { drug: "atorvastatin", route: "oral", doseAmount: "20", frequency: "once daily", age: "84", weight: "52", height: "160", sex: "female", scr: "2.4" } },
  { label: "Oxycodone 10 mg every 6 hours", order: { drug: "oxycodone", route: "oral", doseAmount: "10", frequency: "every 6 hours" } },
  { label: "Amoxicillin suspension, penicillin allergy", order: { drug: "amoxicillin", route: "oral", dosageForm: "suspension", age: "6", allergies: ["penicillin"] } },
];

interface Props {
  prefill: Partial<OrderState> | null;
  autoRun: boolean;
  openRef: string | null;
  consumePrefill: () => void;
}

export function Workbench({ prefill, autoRun, openRef, consumePrefill }: Props) {
  const [order, setOrder] = useState<OrderState>(EMPTY_ORDER);
  const [errors, setErrors] = useState<Partial<Record<keyof OrderState, string>>>({});
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState(0);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [ranOrder, setRanOrder] = useState<OrderState | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [drawer, setDrawer] = useState<{ ids: string[]; heading: string; focus: string | null } | null>(null);
  const [recent, setRecent] = useState<RecentItem[]>(readRecent);
  const [eng, setEng] = useState<EngineState>(engine.state);
  const fetchesAtStart = useRef(0);
  const drugRef = useRef<HTMLInputElement>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  const pendingRun = useRef<OrderState | null>(null);

  useEffect(() => engine.subscribe(setEng), []);

  const run = useCallback(async (o: OrderState) => {
    const e = validate(o);
    setErrors(e);
    if (Object.keys(e).length) {
      if (Object.keys(e)[0] === "drug") drugRef.current?.focus();
      return;
    }
    setBusy(true);
    setFailure(null);
    fetchesAtStart.current = engine.state.fetches;
    try {
      const r = await api.recommend(toRequest(o));
      setRec(r);
      setRanOrder(o);
      setRecent(pushRecent(o, r.status, r.candidates[0]?.display_name ?? null));
      window.requestAnimationFrame(() => resultsHeading.current?.focus());
    } catch (err) {
      setFailure(err instanceof ApiError ? err : new ApiError(0, String(err)));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (prefill) {
      const next = { ...EMPTY_ORDER, ...prefill };
      setOrder(next);
      consumePrefill();
      if (autoRun) pendingRun.current = next;
      else drugRef.current?.focus();
    }
  }, [prefill, autoRun, consumePrefill]);

  useEffect(() => {
    if (pendingRun.current) {
      const o = pendingRun.current;
      pendingRun.current = null;
      void run(o);
    }
  });

  useEffect(() => {
    if (!openRef) return;
    setBusy(true);
    api.audit(openRef)
      .then((a) => { setRec(a.response); setRanOrder(null); })
      .catch((err) => setFailure(err instanceof ApiError ? err : new ApiError(0, String(err))))
      .finally(() => setBusy(false));
  }, [openRef]);

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

  const submit = useCallback(() => { void run(order); }, [order, run]);
  const citations: Citation[] = drawer && rec ? drawer.ids.map((id) => rec.citations[id]).filter(Boolean) : [];
  const liveFetches = eng.fetches - fetchesAtStart.current;
  const booting = eng.mode === "browser" && eng.phase === "booting";

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
          <>
            <ol className="progress" aria-hidden="true">
              {STAGES.map((s, i) => (
                <li key={s} className={i < stage ? "is-done" : i === stage ? "is-now" : ""}>{s}</li>
              ))}
            </ol>
            {eng.mode === "browser" && (
              <p className="muted live-count">
                {booting ? "Starting the in browser engine first. This happens once per visit." :
                  `${liveFetches} live openFDA request${liveFetches === 1 ? "" : "s"}${eng.lastFetch ? `, last: ${eng.lastFetch}` : ""}`}
              </p>
            )}
          </>
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
          <Results rec={rec} shareLink={ranOrder ? shareUrl(ranOrder) : null}
            openEvidence={(ids, heading, focus) => setDrawer({ ids, heading, focus: focus ?? null })} />
        )}
        {!busy && !failure && !rec && (
          <div className="empty">
            <p className="empty__lead">
              Enter the drug that is short. Shortfall Bridge checks the FDA shortage list, finds equivalent products and
              alternatives, and runs eight safety checks against each one using the official label text.
            </p>
            {eng.mode === "browser" && (
              <p className="note note--info">
                Demo mode: the complete Python engine runs in your browser and reads live data from openFDA. Every
                result is computed now, from current FDA labels and shortages. Not for clinical use.
              </p>
            )}
            <p className="muted">Start from a recent shortage:</p>
            <ul className="starters">
              {STARTERS.map((s) => (
                <li key={s.label}>
                  <button className="btn btn--quiet" onClick={() => { const o = { ...EMPTY_ORDER, ...s.order }; setOrder(o); void run(o); }}>
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
            {recent.length > 0 && (
              <section className="recent" aria-labelledby="recent-h">
                <div className="section-head">
                  <h2 id="recent-h">Recent on this device</h2>
                  <button className="link-btn" onClick={() => { clearRecent(); setRecent([]); }}>Clear</button>
                </div>
                <ul>
                  {recent.map((r) => (
                    <li key={r.at}>
                      <button className="link-btn" onClick={() => setOrder({ ...EMPTY_ORDER, ...r.order })}>{r.label}</button>
                      <span className="muted"> {r.status}{r.top ? `, top option ${r.top}` : ""}</span>
                    </li>
                  ))}
                </ul>
                <p className="muted">Only the drug and dose are kept, never patient details.</p>
              </section>
            )}
          </div>
        )}
      </section>
      {drawer && (
        <EvidenceDrawer citations={citations} focusId={drawer.focus} heading={drawer.heading} onClose={() => setDrawer(null)} />
      )}
    </div>
  );
}
