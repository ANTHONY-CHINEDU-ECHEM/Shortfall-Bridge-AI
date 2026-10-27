import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { BarList, ColumnChart } from "../components/Charts";
import type { Insights as InsightsData, ShortageBrief } from "../types";

function fmt(d: string | null) {
  return d ? new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "";
}

function BriefTable({ rows, caption, showDays }: { rows: ShortageBrief[]; caption: string; showDays?: boolean }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Drug and presentation</th>
            <th scope="col">Manufacturer</th>
            <th scope="col">{showDays ? "Listed" : "Updated"}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.record_id}>
              <th scope="row">
                <span className="table__primary">{r.generic_name}</span>
                {r.presentation && <span className="table__secondary">{r.presentation}</span>}
              </th>
              <td>{r.company_name ?? "Not stated"}</td>
              <td className="nowrap">{showDays ? `${r.days_listed?.toLocaleString("en-GB") ?? "?"} days` : fmt(r.update_date)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Insights({ onSearch }: { onSearch: (q: string) => void }) {
  const [data, setData] = useState<InsightsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.insights().then(setData).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  return (
    <div className="page">
      <header className="page__head">
        <h1>Shortage insights</h1>
        <p className="muted">
          The FDA Drug Shortages table summarised for pharmacy leadership: which areas are affected, why, for how long and
          by whom. Computed from the same records as the supply check, so the numbers never disagree.
        </p>
      </header>
      {error && <div className="error-panel" role="alert"><p>{error}</p></div>}
      {!data && !error && <p className="muted" aria-live="polite">Loading shortage data</p>}
      {data && (
        <>
          <dl className="stats stats--4">
            {[
              ["Current entries", data.current.entries],
              ["Ingredients affected", data.current.ingredients],
              ["Manufacturers", data.current.companies],
              ["Median days listed", data.current.days_listed.median ?? "n/a"],
            ].map(([k, v]) => (
              <div key={k as string}><dt>{k}</dt><dd>{typeof v === "number" ? v.toLocaleString("en-GB") : v}</dd></div>
            ))}
          </dl>
          <p className="muted">
            {Object.entries(data.by_status).map(([k, v]) => `${k} ${v}`).join(", ")}. As of {fmt(data.as_of)}.
            {data.current.days_listed.p90 != null && ` One in ten current entries has been listed for ${data.current.days_listed.p90} days or more.`}
          </p>
          <div className="grid-2">
            <section className="panel" aria-labelledby="in-cat">
              <h2 id="in-cat">Therapeutic categories</h2>
              <BarList items={data.current.by_category} label="Current entries by therapeutic category" onPick={onSearch} />
            </section>
            <section className="panel" aria-labelledby="in-reason">
              <h2 id="in-reason">Reported reasons</h2>
              <BarList items={data.current.by_reason} label="Current entries by shortage reason" />
            </section>
            <section className="panel" aria-labelledby="in-route">
              <h2 id="in-route">Route of administration</h2>
              <BarList items={data.current.by_route_family} label="Current entries by route family" />
            </section>
            <section className="panel" aria-labelledby="in-ing">
              <h2 id="in-ing">Most affected ingredients</h2>
              <BarList items={data.current.most_affected_ingredients} label="Ingredients with the most current entries" onPick={onSearch} />
            </section>
          </div>
          <section className="panel" aria-labelledby="in-time">
            <h2 id="in-time">New shortage postings by month</h2>
            <ColumnChart title="Initial postings, last 24 months" unit="postings"
              points={data.timeline.map((t) => ({ label: t.month, value: t.new_postings }))} />
          </section>
          <div className="grid-2">
            <section className="panel" aria-labelledby="in-long">
              <h2 id="in-long">Longest running</h2>
              <BriefTable rows={data.current.longest_running} caption="Longest running current shortages" showDays />
            </section>
            <section className="panel" aria-labelledby="in-recent">
              <h2 id="in-recent">Recently updated</h2>
              <BriefTable rows={data.recently_updated} caption="Most recently updated entries" />
            </section>
          </div>
          <section className="panel" aria-labelledby="in-co">
            <h2 id="in-co">Manufacturers with the most current entries</h2>
            <BarList items={data.current.top_companies} label="Current entries by manufacturer" onPick={onSearch} />
          </section>
        </>
      )}
    </div>
  );
}
