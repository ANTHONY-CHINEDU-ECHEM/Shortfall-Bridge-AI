import { useEffect, useId, useState } from "react";
import { engine, readApiKey, type EngineState } from "../engine/transport";
import { IconSettings } from "./Icons";

const STAGE_ORDER = ["runtime", "packages", "engine", "data-source", "ready"] as const;

/** Banner for the in browser engine: boot progress, data source, and an optional openFDA key. */
export function EngineStatus() {
  const [s, setS] = useState<EngineState>(engine.state);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(readApiKey() ?? "");
  const [saved, setSaved] = useState(false);
  const id = useId();
  useEffect(() => engine.subscribe(setS), []);
  useEffect(() => { engine.start(); }, []);
  if (s.mode !== "browser") return null;

  const step = Math.max(0, STAGE_ORDER.indexOf((s.stage ?? "runtime") as (typeof STAGE_ORDER)[number]));
  const source = s.base?.startsWith("/") ? "openFDA via this site's edge cache" : "openFDA directly";

  return (
    <div className={`engine-bar engine-bar--${s.phase}`} role="status" aria-live="polite">
      {s.phase === "booting" && (
        <>
          <span className="engine-bar__spinner" aria-hidden="true" />
          <span><strong>Starting the engine in your browser</strong> {s.detail ?? ""} ({step + 1} of 4)</span>
          <span className="engine-bar__meter" aria-hidden="true"><span style={{ width: `${(step / 4) * 100}%` }} /></span>
        </>
      )}
      {s.phase === "ready" && (
        <span>
          <strong>Live demo.</strong> The Python engine runs in this tab on current FDA data ({source})
          {s.bootMs ? `, started in ${(s.bootMs / 1000).toFixed(1)} s` : ""}. Not for clinical use.
        </span>
      )}
      {s.phase === "error" && (
        <span><strong>The in browser engine could not start.</strong> {s.error} Reload to try again; a recent browser is required.</span>
      )}
      <button type="button" className="icon-btn engine-bar__btn" aria-expanded={open} aria-controls={`${id}-p`}
        onClick={() => setOpen(!open)} aria-label="Demo data settings">
        <IconSettings size={16} />
      </button>
      {open && (
        <div id={`${id}-p`} className="engine-bar__panel">
          <form onSubmit={(e) => { e.preventDefault(); engine.setApiKey(key.trim() || null); setSaved(true); }}>
            <label htmlFor={`${id}-k`}>openFDA API key, optional</label>
            <p className="field__hint">
              Without a key openFDA allows about 1,000 requests a day per IP address (roughly 40 to 60 recommendations).
              A free key from <a href="https://open.fda.gov/apis/authentication/" target="_blank" rel="noreferrer">open.fda.gov</a> raises
              that limit. It is kept in this browser only and sent only to openFDA.
            </p>
            <div className="engine-bar__row">
              <input id={`${id}-k`} value={key} onChange={(e) => { setKey(e.target.value); setSaved(false); }}
                autoComplete="off" spellCheck={false} />
              <button className="btn btn--secondary btn--small" type="submit">Save</button>
            </div>
            {saved && <p className="muted">{key.trim() ? "Key saved for this browser." : "Key removed."}</p>}
          </form>
          <p className="muted">Live requests this session: {s.fetches}. Engine bundle {s.version ?? "loading"}.</p>
        </div>
      )}
    </div>
  );
}
