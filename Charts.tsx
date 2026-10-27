import { useState } from "react";
import type { Ranked } from "../types";

/** Horizontal bars for one ranked series. Value at the tip; the list itself is the table view. */
export function BarList({ items, label, onPick }: { items: Ranked[]; label: string; onPick?: (name: string) => void }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  if (!items.length) return <p className="muted">No entries.</p>;
  return (
    <ul className="bars" aria-label={label}>
      {items.map((i) => (
        <li key={i.name} className="bars__row">
          {onPick && i.name !== "Not stated" ? (
            <button type="button" className="bars__name link-btn" onClick={() => onPick(i.name)} title={`Show ${i.name} in the monitor`}>
              {i.name}
            </button>
          ) : <span className="bars__name">{i.name}</span>}
          <span className="bars__track" aria-hidden="true">
            <span className="bars__fill" style={{ width: `${(i.count / max) * 100}%` }} />
          </span>
          <span className="bars__value">{i.count.toLocaleString("en-GB")}</span>
        </li>
      ))}
    </ul>
  );
}

interface Point { label: string; value: number }

/** Monthly columns for one series, with a hover readout and a table fallback. */
export function ColumnChart({ points, title, unit }: { points: Point[]; title: string; unit: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const W = 960, H = 220, padL = 36, padR = 12, padB = 24, padT = 14;
  const max = Math.max(1, ...points.map((p) => p.value));
  const nice = max <= 4 ? 4 : Math.ceil(max / 4) * 4; // whole number ticks: counts are integers
  const band = (W - padL - padR) / Math.max(1, points.length);
  const bw = Math.min(24, band - 2);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / nice);
  const ticks = [0, nice / 2, nice];
  const last = points.length - 1;
  const shown = hover !== null ? points[hover] : null;
  return (
    <figure className="chart">
      <figcaption className="chart__head">
        <span>{title}</span>
        <span className="muted chart__readout" aria-live="polite">
          {shown ? `${shown.label}: ${shown.value} ${unit}` : `peak ${max} ${unit}`}
        </span>
        <button type="button" className="link-btn" onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button>
      </figcaption>
      {table ? (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th scope="col">Month</th><th scope="col">{unit}</th></tr></thead>
            <tbody>{points.map((p) => <tr key={p.label}><th scope="row">{p.label}</th><td>{p.value}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} className="chart__svg" role="img" aria-label={`${title}. Use Show table for values.`}
          onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="chart__grid" />
              <text x={padL - 6} y={y(t) + 4} className="chart__tick" textAnchor="end">{t}</text>
            </g>
          ))}
          {points.map((p, i) => {
            const x = padL + i * band + (band - bw) / 2;
            const top = y(p.value);
            const h = Math.max(0, H - padB - top);
            const r = Math.min(4, h / 2, bw / 2);
            return (
              <g key={p.label} onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={-1}>
                <rect x={padL + i * band} y={padT} width={band} height={H - padT - padB} fill="transparent" />
                {h > 0 && (
                  <path className={`chart__bar ${hover === i ? "is-hover" : ""}`}
                    d={`M${x},${H - padB} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${H - padB} Z`} />
                )}
                {(i % 3 === 0 || i === last) && (i === last || last - i >= 2) && (
                  <text x={i === last ? x + bw : x + bw / 2} y={H - 6} className="chart__tick"
                    textAnchor={i === last ? "end" : "middle"}>{p.label.slice(2)}</text>
                )}
              </g>
            );
          })}
          <line x1={padL} x2={W - padR} y1={H - padB} y2={H - padB} className="chart__axis" />
        </svg>
      )}
    </figure>
  );
}
