import type { Candidate } from "../types";
import { GATE_LABEL, GATE_ORDER, STATUS_LABEL, TIER_LABEL, titleCase } from "../labels";
import { GateMark } from "./GateStrip";

/** Options against checks in one grid: the fastest way to compare several alternatives at a glance. */
export function GateMatrix({ candidates, blocked }: { candidates: Candidate[]; blocked: Candidate[] }) {
  const rows = [...candidates.map((c, i) => ({ c, rank: i + 1 as number | null })), ...blocked.map((c) => ({ c, rank: null }))];
  return (
    <div className="table-wrap matrix-wrap">
      <table className="table matrix">
        <caption className="visually-hidden">Safety checks for every option</caption>
        <thead>
          <tr>
            <th scope="col">Option</th>
            {GATE_ORDER.map((g) => <th key={g} scope="col" className="matrix__gate">{GATE_LABEL[g]}</th>)}
            <th scope="col">Start dose</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ c, rank }) => {
            const by = new Map(c.gates.map((g) => [g.gate, g]));
            const dc = c.dose_conversion;
            return (
              <tr key={c.tier + c.display_name} className={rank === null ? "is-blocked" : ""}>
                <th scope="row">
                  <span className="table__primary">{rank !== null ? `${rank}. ` : ""}{titleCase(c.display_name)}</span>
                  <span className="table__secondary">{rank === null ? "Not recommended" : TIER_LABEL[c.tier]}</span>
                </th>
                {GATE_ORDER.map((name) => {
                  const g = by.get(name);
                  const st = g?.status ?? "unknown";
                  return (
                    <td key={name} className={`matrix__cell is-${st}`} title={g?.rationale}>
                      <GateMark status={st} />
                      <span className="visually-hidden">{STATUS_LABEL[st]}</span>
                    </td>
                  );
                })}
                <td className="nowrap">{dc?.feasible ? dc.suggested_dose : <span className="muted">none</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
