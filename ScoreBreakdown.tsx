import type { ScoreTerm } from "../types";

const TERM: Record<string, string> = {
  tier: "Substitution tier",
  caution: "Caution",
  needs_data: "Needs patient data",
  supply_same_route: "Also in shortage by this route",
  supply_market: "Presentation short, other makers",
  route_mismatch: "Not available by the requested route",
  dose_ceiling: "Exceeds labelled daily maximum",
  dose_available: "Published dose conversion",
  evidence: "Findings carry label citations",
  specialist_review: "Specialist review required",
};

const GATE: Record<string, string> = {
  availability: "supply", allergy: "allergy", renal: "renal", hepatic: "hepatic",
  pregnancy_lactation: "pregnancy", interactions: "interactions", boxed_warning: "boxed warning", age: "age",
};

/** Every additive term of the rank score, so the order of options is never a black box. */
export function ScoreBreakdown({ terms, score }: { terms: ScoreTerm[]; score: number }) {
  if (!terms.length) return null;
  return (
    <details className="breakdown">
      <summary>Why this rank (score {score.toFixed(2)})</summary>
      <table className="breakdown__table">
        <caption className="visually-hidden">Score terms</caption>
        <tbody>
          {terms.map((t, i) => (
            <tr key={`${t.term}-${i}`} className={t.value < 0 ? "is-neg" : "is-pos"}>
              <th scope="row">
                {TERM[t.term] ?? t.term}
                {t.term === "caution" || t.term === "needs_data" ? `: ${GATE[t.detail] ?? t.detail}` : ""}
                {t.term !== "caution" && t.term !== "needs_data" && t.detail && t.term !== "tier" && (
                  <span className="breakdown__detail">{t.detail}</span>
                )}
              </th>
              <td>{t.value > 0 ? "+" : ""}{t.value.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr><th scope="row">Score</th><td>{score.toFixed(2)}</td></tr>
        </tfoot>
      </table>
    </details>
  );
}
