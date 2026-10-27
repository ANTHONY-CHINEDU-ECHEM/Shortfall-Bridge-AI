import type { GateResult, GateStatus } from "../types";
import { GATE_LABEL, GATE_ORDER, STATUS_LABEL } from "../labels";

/** One blister cell. Shape encodes status so meaning never depends on colour alone. */
export function GateMark({ status, size = 14 }: { status: GateStatus; size?: number }) {
  const s = size;
  const c = s / 2;
  switch (status) {
    case "pass":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true">
          <circle cx={c} cy={c} r={c - 1.5} fill="currentColor" />
        </svg>
      );
    case "caution":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true">
          <path d={`M${c} 1.5 L${s - 1.5} ${s - 2} L1.5 ${s - 2} Z`} fill="currentColor" />
        </svg>
      );
    case "block":
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true">
          <circle cx={c} cy={c} r={c - 1.5} fill="none" stroke="currentColor" strokeWidth="2" />
          <path d={`M${c - 3} ${c - 3} L${c + 3} ${c + 3} M${c + 3} ${c - 3} L${c - 3} ${c + 3}`} stroke="currentColor" strokeWidth="2" />
        </svg>
      );
    default:
      return (
        <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true">
          <circle cx={c} cy={c} r={c - 1.5} fill="none" stroke="currentColor" strokeWidth="1.6" strokeDasharray="2.4 2" />
        </svg>
      );
  }
}

export function GateStrip({ gates, compact = false }: { gates: GateResult[]; compact?: boolean }) {
  const byName = new Map(gates.map((g) => [g.gate, g]));
  const summary = GATE_ORDER.map((n) => `${GATE_LABEL[n]} ${STATUS_LABEL[byName.get(n)?.status ?? "unknown"]}`).join(", ");
  return (
    <ul className={`blister ${compact ? "blister--compact" : ""}`} aria-label={`Safety checks: ${summary}`}>
      {GATE_ORDER.map((name) => {
        const g = byName.get(name);
        const status = g?.status ?? "unknown";
        return (
          <li key={name} className={`blister__cell is-${status}`} title={`${GATE_LABEL[name]}: ${STATUS_LABEL[status]}`}>
            <GateMark status={status} />
            {!compact && <span className="blister__label">{GATE_LABEL[name]}</span>}
          </li>
        );
      })}
    </ul>
  );
}
