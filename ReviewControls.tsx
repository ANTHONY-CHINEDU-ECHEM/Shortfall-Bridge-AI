import { useId, useState } from "react";
import { api, ApiError } from "../api";
import type { Candidate, ReviewDecision, ReviewRecord } from "../types";

interface Props {
  requestId: string;
  candidate: Candidate;
  existing?: ReviewRecord;
  onSaved: (r: ReviewRecord) => void;
}

/** Pharmacist agreement or override for one option. Overrides need a reason: that is the labelled data. */
export function ReviewControls({ requestId, candidate, existing, onSaved }: Props) {
  const id = useId();
  const [mode, setMode] = useState<ReviewDecision | null>(null);
  const [reason, setReason] = useState("");
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (decision: ReviewDecision) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.review({
        request_id: requestId, candidate_ingredient: candidate.ingredient, candidate_tier: candidate.tier, decision,
        reason: decision === "agree" ? null : reason.trim(), chosen_alternative: chosen.trim() || null,
      });
      onSaved(r);
      setMode(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (existing) {
    return (
      <p className={`review-done review-done--${existing.decision}`} role="status">
        Reviewed: {existing.decision === "agree" ? "agreed" : existing.decision === "override" ? "overridden" : "rejected"}
        {existing.reason ? ` (${existing.reason})` : ""}.
      </p>
    );
  }
  return (
    <div className="review">
      <span className="review__label">Pharmacist review</span>
      <div className="review__buttons">
        <button type="button" className="btn btn--quiet btn--small" disabled={busy} onClick={() => save("agree")}>Agree</button>
        <button type="button" className="btn btn--quiet btn--small" disabled={busy} aria-expanded={mode === "override"}
          onClick={() => setMode(mode === "override" ? null : "override")}>Override</button>
        <button type="button" className="btn btn--quiet btn--small" disabled={busy} aria-expanded={mode === "reject"}
          onClick={() => setMode(mode === "reject" ? null : "reject")}>Reject</button>
      </div>
      {mode && (
        <form className="review__form" onSubmit={(e) => { e.preventDefault(); if (reason.trim()) void save(mode); }}>
          <div className="field">
            <label htmlFor={`${id}-r`}>Reason, required</label>
            <input id={`${id}-r`} value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)}
              placeholder={mode === "override" ? "e.g. No IV stock on site" : "e.g. Patient cannot take oral"} />
          </div>
          <div className="field">
            <label htmlFor={`${id}-c`}>Used instead, optional</label>
            <input id={`${id}-c`} value={chosen} maxLength={200} onChange={(e) => setChosen(e.target.value)} />
          </div>
          <button type="submit" className="btn btn--secondary btn--small" disabled={busy || !reason.trim()}>
            Save {mode}
          </button>
        </form>
      )}
      {error && <p className="field__error" role="alert">{error}</p>}
    </div>
  );
}
