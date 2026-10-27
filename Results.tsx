import { useState } from "react";
import { api } from "../api";
import type { Candidate, Citation, GateResult, Recommendation, ReviewRecord } from "../types";
import { GATE_LABEL, GATE_ORDER, STATUS_LABEL, TIER_HELP, TIER_LABEL, titleCase } from "../labels";
import { GateMatrix } from "./GateMatrix";
import { GateMark, GateStrip } from "./GateStrip";
import { IconAlert, IconChevron, IconCopy, IconDownload, IconLink, IconPrint } from "./Icons";
import { ReviewControls } from "./ReviewControls";
import { ScoreBreakdown } from "./ScoreBreakdown";

interface Props {
  rec: Recommendation;
  openEvidence: (ids: string[], heading: string, focus?: string) => void;
  shareLink?: string | null;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Clipboard API is unavailable in insecure contexts; fall back to a hidden textarea.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function RequestNotes({ rec }: { rec: Recommendation }) {
  const notes = rec.warnings ?? [];
  const renal = rec.renal;
  if (!notes.length && !(renal && renal.value_ml_min != null)) return null;
  const estimated = renal && ["cockcroft_gault", "ckd_epi_2021", "bedside_schwartz", "dialysis"].includes(renal.source);
  return (
    <section className="notes" aria-label="Notes about this request">
      {renal && renal.value_ml_min != null && (
        <p className="notes__renal">
          <span className="chip-stat">Renal {renal.value_ml_min} mL/min</span>
          <span className="muted">{estimated ? `estimated by ${renal.method}` : renal.method}
            {renal.formula ? ` (${renal.formula})` : ""}</span>
        </p>
      )}
      {notes.length > 0 && (
        <ul className="notes__list">
          {notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </section>
  );
}

function fmtDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "unknown date";
}

function ShortageBanner({ rec }: { rec: Recommendation }) {
  const s = rec.shortage;
  const name = titleCase(rec.query?.display_name ?? "This drug");
  if (!s) return null;
  if (!s.in_shortage) {
    return (
      <section className="banner banner--calm" aria-label="Shortage status">
        <p><strong>{name}</strong> has no current FDA shortage entry. Alternatives are still listed below.</p>
      </section>
    );
  }
  return (
    <section className="banner banner--shortage" aria-label="Shortage status">
      <IconAlert />
      <div>
        <p>
          <strong>{name}</strong> is in shortage: {s.matching_records} current FDA entr{s.matching_records === 1 ? "y" : "ies"}, updated {fmtDate(s.last_updated)}.
        </p>
        {s.reasons.length > 0 && <p className="muted">Reported reason: {s.reasons.join("; ")}.</p>}
        {s.presentations.length > 0 && (
          <details className="banner__more">
            <summary>Affected presentations ({s.presentations.length})</summary>
            <ul>{s.presentations.map((p) => <li key={p}>{p}</li>)}</ul>
          </details>
        )}
      </div>
    </section>
  );
}

function Summary({ rec, openEvidence }: Props) {
  const ids = Object.keys(rec.citations);
  const refNo = (id: string) => ids.indexOf(id) + 1;
  const llm = rec.narrative.generator.startsWith("llm");
  return (
    <section className="summary" aria-labelledby="summary-h">
      <div className="section-head">
        <h2 id="summary-h">Summary</h2>
        <span className="provenance" title={llm ? "Written by a language model and checked sentence by sentence against the cited sources" : "Composed directly from engine decisions"}>
          {llm ? "Model written, citation verified" : "Composed from engine decisions"}
        </span>
      </div>
      <ul className="summary__list">
        {rec.narrative.sentences.slice(0, 8).map((s, i) => (
          <li key={i}>
            {s.text}
            {s.citations.map((c) => (
              <button key={c} className="cite" onClick={() => openEvidence(ids, "Evidence for this decision", c)}
                aria-label={`Show evidence passage ${refNo(c)}`}>
                {refNo(c)}
              </button>
            ))}
          </li>
        ))}
      </ul>
      {rec.narrative.sentences.length > 8 && (
        <details>
          <summary>{rec.narrative.sentences.length - 8} more statements</summary>
          <ul className="summary__list">
            {rec.narrative.sentences.slice(8).map((s, i) => <li key={i}>{s.text}</li>)}
          </ul>
        </details>
      )}
    </section>
  );
}

function GateDetail({ g, onCite }: { g: GateResult; onCite: (id: string) => void }) {
  return (
    <li className={`gate-row is-${g.status}`}>
      <span className="gate-row__mark"><GateMark status={g.status} /></span>
      <span className="gate-row__name">{GATE_LABEL[g.gate]}</span>
      <span className="gate-row__status">{STATUS_LABEL[g.status]}</span>
      <p className="gate-row__why">
        {g.rationale}
        {g.citations.length > 0 && (
          <button className="link-btn" onClick={() => onCite(g.citations[0])}>
            View label text
          </button>
        )}
      </p>
    </li>
  );
}

function CandidateRow({ c, rank, rec, openEvidence, review, onReviewed }: {
  c: Candidate; rank: number | null; review?: ReviewRecord; onReviewed: (r: ReviewRecord) => void;
} & Props) {
  const [open, setOpen] = useState(rank === 1);
  const panelId = `cand-${rank ?? "x"}-${c.ingredient.replace(/\W/g, "")}`;
  const gates = [...c.gates].sort((a, b) => GATE_ORDER.indexOf(a.gate) - GATE_ORDER.indexOf(b.gate));
  const flagged = gates.filter((g) => g.status !== "pass");
  const dc = c.dose_conversion;
  const evidenceIds = c.citations.filter((id) => rec.citations[id]);
  const show = (focus?: string) => openEvidence(evidenceIds, `Evidence for ${titleCase(c.display_name)}`, focus);

  return (
    <li className={`option ${c.gates.some((g) => g.status === "block") ? "option--blocked" : ""}`}>
      <div className="option__head">
        {rank !== null && <span className="option__rank" aria-label={`Rank ${rank}`}>{rank}</span>}
        <div className="option__title">
          <h3>{titleCase(c.display_name)}</h3>
          <p className="option__tier">
            <span className={`tier tier--${c.tier.slice(0, 2).toLowerCase()}`} title={TIER_HELP[c.tier]}>{TIER_LABEL[c.tier]}</span>
            {c.requires_specialist_review && <span className="tag tag--review">Specialist review</span>}
            {c.route_match === false && (
              <span className="tag tag--route" title={`Available by ${(c.routes_available ?? []).join(", ")}`}>
                Route change
              </span>
            )}
            {dc?.ceiling_exceeded && <span className="tag tag--ceiling">Over label maximum</span>}
            {c.te_codes.length > 0 && <span className="muted">TE {c.te_codes.join(", ")}</span>}
          </p>
        </div>
        <div className="option__dose">
          {dc?.feasible && dc.suggested_dose ? (
            <>
              <span className="option__dose-value">{dc.suggested_dose}</span>
              <span className="muted">starting dose</span>
            </>
          ) : dc && !dc.feasible ? (
            <span className="muted">No automatic dose</span>
          ) : null}
        </div>
        <GateStrip gates={c.gates} compact />
        <button className="icon-btn option__toggle" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
          <IconChevron />
          <span className="visually-hidden">{open ? "Hide" : "Show"} details for {c.display_name}</span>
        </button>
      </div>
      {!open && flagged.length > 0 && (
        <p className="option__flags">
          {flagged.map((g) => `${GATE_LABEL[g.gate]}: ${STATUS_LABEL[g.status].toLowerCase()}`).join("; ")}
        </p>
      )}
      {open && (
        <div id={panelId} className="option__body">
          <p>{c.rationale}</p>
          {dc && (
            <div className={`dose-card ${dc.feasible ? "" : "dose-card--refused"}`}>
              <h4>{dc.feasible ? "Dose conversion" : "Dose conversion not provided"}</h4>
              {dc.feasible ? (
                <p className="dose-card__line">
                  {dc.from_dose} {dc.from_drug} <span aria-hidden="true">to</span><span className="visually-hidden">converts to</span>{" "}
                  <strong>{dc.suggested_dose} {dc.to_drug}</strong>
                </p>
              ) : null}
              {dc.suggested_range && <p className="muted">{dc.suggested_range}</p>}
              {dc.suggested_daily_total && (
                <p className={`dose-card__daily ${dc.ceiling_exceeded ? "is-over" : ""}`}>
                  Daily total {dc.suggested_daily_total}
                  {dc.label_max_daily ? `; labelled maximum ${dc.label_max_daily}` : "; no labelled maximum found"}
                  {dc.ceiling_citations && dc.ceiling_citations.length > 0 && (
                    <button className="link-btn" onClick={() => show(dc.ceiling_citations![0])}>View label text</button>
                  )}
                </p>
              )}
              {dc.warnings.length > 0 && <ul className="dose-card__warn">{dc.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
              {dc.reference && <p className="dose-card__ref">Reference: {dc.reference}</p>}
            </div>
          )}
          <h4 className="sub-h">Safety checks</h4>
          <ul className="gate-list">
            {gates.map((g) => <GateDetail key={g.gate} g={g} onCite={(id) => show(id)} />)}
          </ul>
          {c.products.length > 0 && (
            <details className="products">
              <summary>Products indexed ({c.products.length})</summary>
              <ul>{c.products.map((p) => <li key={p}>{p}</li>)}</ul>
            </details>
          )}
          <ScoreBreakdown terms={c.score_breakdown ?? []} score={c.score} />
          {evidenceIds.length > 0 && (
            <button className="btn btn--secondary" onClick={() => show()}>
              Read {evidenceIds.length} label passage{evidenceIds.length === 1 ? "" : "s"}
            </button>
          )}
          <ReviewControls requestId={rec.request_id} candidate={c} existing={review} onSaved={onReviewed} />
        </div>
      )}
    </li>
  );
}

function Acknowledge({ rec, shareLink }: { rec: Recommendation; shareLink?: string | null }) {
  const [ack, setAck] = useState(false);
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState("");
  const download = async () => {
    try {
      const md = await api.note(rec.request_id, "markdown");
      const url = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `shortfall-${rec.request_id}.md`;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus("Note downloaded");
    } catch (e) {
      setStatus(`Could not create the note: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const text = [
    `Shortfall Bridge AI decision support (ref ${rec.request_id})`,
    rec.shortage?.in_shortage ? `${rec.query?.display_name}: in current FDA shortage.` : "",
    ...rec.candidates.slice(0, 5).map((c, i) =>
      `${i + 1}. ${titleCase(c.display_name)} (${TIER_LABEL[c.tier]})${c.dose_conversion?.feasible ? `, start ${c.dose_conversion.suggested_dose}` : ""}`),
    rec.disclaimer,
  ].filter(Boolean).join("\n");
  return (
    <section className="ack" aria-label="Use this result">
      <label className="check">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
        I have checked the cited label text and the patient record, and take responsibility for the final choice.
      </label>
      <div className="ack__actions">
        <button className="btn btn--secondary" disabled={!ack} onClick={async () => {
          if (await copyText(text)) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2500);
          }
        }}>
          <IconCopy size={16} /> {copied ? "Copied" : "Copy note for record"}
        </button>
        <button className="btn btn--quiet" disabled={!ack} onClick={download}><IconDownload size={16} /> Full note (.md)</button>
        <button className="btn btn--quiet" onClick={() => window.print()}><IconPrint size={16} /> Print</button>
        {shareLink && (
          <button className="btn btn--quiet" onClick={async () => {
            if (await copyText(shareLink)) setStatus("Link to this request copied (no patient identifiers)");
          }}><IconLink size={16} /> Copy link</button>
        )}
      </div>
      <span role="status" className="visually-hidden">{copied ? "Note copied to clipboard" : status}</span>
      {status && <p className="muted ack__status">{status}</p>}
    </section>
  );
}

export function Results({ rec, openEvidence, shareLink }: Props) {
  const allEvidence: Citation[] = Object.values(rec.citations);
  const [view, setView] = useState<"list" | "matrix">("list");
  const [reviews, setReviews] = useState<Record<string, ReviewRecord>>({});
  const onReviewed = (r: ReviewRecord) => setReviews((m) => ({ ...m, [r.candidate_ingredient]: r }));
  const total = rec.timings_ms ? Object.values(rec.timings_ms).reduce((a, b) => a + b, 0) : null;
  return (
    <div className="results">
      <ShortageBanner rec={rec} />
      <RequestNotes rec={rec} />
      {rec.status === "abstain" ? (
        <section className="abstain" aria-labelledby="abstain-h">
          <h2 id="abstain-h">No safe substitution to recommend</h2>
          <ul>{rec.abstain_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </section>
      ) : (
        <>
          <section aria-labelledby="options-h">
            <div className="section-head">
              <h2 id="options-h">Options, ranked</h2>
              <div className="section-head__tools">
                <span className="muted">{rec.candidates.length} considered safe to review</span>
                <fieldset className="segmented segmented--small">
                  <legend>View</legend>
                  {(["list", "matrix"] as const).map((v) => (
                    <label key={v} className={view === v ? "is-on" : ""}>
                      <input type="radio" name="view" value={v} checked={view === v} onChange={() => setView(v)} />
                      {v === "list" ? "Details" : "Compare"}
                    </label>
                  ))}
                </fieldset>
              </div>
            </div>
            {rec.status === "partial" && (
              <p className="note">Every option carries at least one caution or missing value. Read the checks before choosing.</p>
            )}
            {view === "matrix" ? (
              <GateMatrix candidates={rec.candidates} blocked={rec.not_recommended} />
            ) : (
              <ol className="options">
                {rec.candidates.map((c, i) => (
                  <CandidateRow key={c.tier + c.display_name} c={c} rank={i + 1} rec={rec} openEvidence={openEvidence}
                    review={reviews[c.ingredient]} onReviewed={onReviewed} />
                ))}
              </ol>
            )}
          </section>
          <Summary rec={rec} openEvidence={openEvidence} />
        </>
      )}
      {rec.not_recommended.length > 0 && (
        <details className="blocked">
          <summary>
            <h2>Not recommended for this patient ({rec.not_recommended.length})</h2>
          </summary>
          <ul className="options">
            {rec.not_recommended.map((c) => (
              <CandidateRow key={c.tier + c.display_name} c={c} rank={null} rec={rec} openEvidence={openEvidence}
                review={reviews[c.ingredient]} onReviewed={onReviewed} />
            ))}
          </ul>
        </details>
      )}
      {rec.status !== "abstain" && <Acknowledge rec={rec} shareLink={shareLink} />}
      <footer className="results__foot">
        <p>{rec.disclaimer}</p>
        <p className="muted">
          Reference {rec.request_id}, {rec.latency_ms.toFixed(0)} ms
          {total !== null && rec.timings_ms?.prefetch ? ` (live data ${rec.timings_ms.prefetch.toFixed(0)} ms)` : ""}
          {rec.engine_version ? `, engine ${rec.engine_version}` : ""}.{" "}
          {allEvidence.length > 0 && (
            <button className="link-btn" onClick={() => openEvidence(Object.keys(rec.citations), "All cited evidence")}>
              All {allEvidence.length} cited passages
            </button>
          )}
        </p>
      </footer>
    </div>
  );
}
