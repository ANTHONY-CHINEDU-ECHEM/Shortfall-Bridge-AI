import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { ShortageRecord } from "../types";

const PAGE = 25;

interface Props {
  onFindAlternatives: (r: ShortageRecord) => void;
}

export function ShortageMonitor({ onFindAlternatives }: Props) {
  const [status, setStatus] = useState("Current");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ items: ShortageRecord[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => { setQuery(q.trim()); setOffset(0); }, 250);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.shortages({ status: status || undefined, q: query || undefined, limit: PAGE, offset })
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [status, query, offset]);

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="page">
      <header className="page__head">
        <h1>Shortage monitor</h1>
        <p className="muted">Presentation level entries from the FDA Drug Shortages database, refreshed by the ingestion job.</p>
      </header>
      <div className="filters" role="search">
        <div className="field">
          <label htmlFor="sm-q">Search</label>
          <input id="sm-q" type="search" value={q} placeholder="Drug, manufacturer or category" onChange={(e) => setQ(e.target.value)} />
        </div>
        <fieldset className="segmented">
          <legend>Status</legend>
          {[["Current", "Current"], ["To Be Discontinued", "Discontinuing"], ["Resolved", "Resolved"], ["", "All"]].map(([v, l]) => (
            <label key={l} className={status === v ? "is-on" : ""}>
              <input type="radio" name="status" value={v} checked={status === v} onChange={() => { setStatus(v); setOffset(0); }} />
              {l}
            </label>
          ))}
        </fieldset>
      </div>
      <p className="muted" aria-live="polite">
        {loading ? "Loading entries" : data ? `${data.total.toLocaleString("en-GB")} entr${data.total === 1 ? "y" : "ies"}` : ""}
      </p>
      {error && <div className="error-panel" role="alert"><p>{error}</p></div>}
      {data && data.items.length === 0 && !loading && (
        <div className="empty"><p>No entries match. Clear the search or choose another status.</p></div>
      )}
      {data && data.items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">FDA drug shortage entries, page {page} of {pages}</caption>
            <thead>
              <tr>
                <th scope="col">Drug and presentation</th>
                <th scope="col">Manufacturer</th>
                <th scope="col">Status</th>
                <th scope="col">Reason</th>
                <th scope="col">Updated</th>
                <th scope="col"><span className="visually-hidden">Action</span></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.record_id}>
                  <th scope="row">
                    <span className="table__primary">{r.generic_name}</span>
                    {r.presentation && <span className="table__secondary">{r.presentation}</span>}
                  </th>
                  <td>{r.company_name ?? "Not stated"}</td>
                  <td><span className={`status-pill status-pill--${r.status.replace(/\s/g, "").toLowerCase()}`}>{r.status}</span></td>
                  <td>{r.shortage_reason ?? <span className="muted">Not stated</span>}</td>
                  <td className="nowrap">{r.update_date ? new Date(r.update_date).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : ""}</td>
                  <td>
                    <button className="btn btn--quiet btn--small" onClick={() => onFindAlternatives(r)}>
                      Find alternatives<span className="visually-hidden"> for {r.generic_name}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && pages > 1 && (
        <nav className="pager" aria-label="Pages">
          <button className="btn btn--quiet" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span>Page {page} of {pages}</span>
          <button className="btn btn--quiet" disabled={page >= pages} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </nav>
      )}
    </div>
  );
}
