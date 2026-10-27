import { useEffect, useState } from "react";
import { api } from "../api";
import { GATE_LABEL, TIER_HELP, TIER_LABEL } from "../labels";
import { GateMark } from "../components/GateStrip";
import type { Meta } from "../types";

export function Method() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <div className="page page--prose">
      <header className="page__head">
        <h1>Method and safety</h1>
        <p className="lead">
          A rules engine decides. Retrieval supplies the label text that justifies each decision. A language model, when
          enabled, only writes the summary, and every sentence it writes is checked against its citations.
        </p>
      </header>

      <section aria-labelledby="m-tiers">
        <h2 id="m-tiers">How options are found</h2>
        <ol className="tiers">
          {(Object.keys(TIER_LABEL) as (keyof typeof TIER_LABEL)[]).map((t) => (
            <li key={t}>
              <strong>{TIER_LABEL[t]}.</strong> {TIER_HELP[t]}
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="m-gates">
        <h2 id="m-gates">Safety checks on every option</h2>
        <p>
          Checks read the whole relevant label section, not a search result, so a contraindication cannot be missed
          because it ranked low. A check with missing patient data shows <em>Needs data</em>; nothing is assumed.
        </p>
        <ul className="legend">
          {(["pass", "caution", "block", "unknown"] as const).map((s) => (
            <li key={s} className={`is-${s}`}>
              <GateMark status={s} size={16} />
              {{ pass: "Clear", caution: "Caution, read before use", block: "Blocked, moved to not recommended", unknown: "Needs patient data" }[s]}
            </li>
          ))}
        </ul>
        {meta && (
          <dl className="gates-def">
            {meta.gates.map((g) => (
              <div key={g.gate}>
                <dt>{GATE_LABEL[g.gate]}</dt>
                <dd>{g.question}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      <section aria-labelledby="m-data">
        <h2 id="m-data">Data in this deployment</h2>
        {error && <p className="field__error">Could not load deployment details: {error}</p>}
        {meta && (
          <>
            <dl className="stats">
              {[
                ["Labels indexed", meta.corpus.labels],
                ["Label passages", meta.corpus.chunks],
                ["Ingredients", meta.corpus.ingredients],
                ["Current shortage entries", meta.corpus.current_shortages],
                ["Orange Book products", meta.corpus.orange_book_products],
                ["Class links", meta.corpus.class_links],
              ].map(([k, v]) => (
                <div key={k as string}>
                  <dt>{k}</dt>
                  <dd>{Number(v ?? 0).toLocaleString("en-GB")}</dd>
                </div>
              ))}
            </dl>
            <p className="muted">
              Embedding model {meta.embedding_model}. Summary writer: {meta.llm_provider === "none" ? "rules based template" : meta.llm_provider}.
              Version {meta.version}.
            </p>
            {Object.keys(meta.data_versions).length > 0 && (
              <ul className="freshness">
                {Object.entries(meta.data_versions).map(([k, v]) => (
                  <li key={k}>{k.replace(/_/g, " ")}: refreshed {new Date(v).toLocaleString("en-GB")}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="m-limits">
        <h2 id="m-limits">Limits</h2>
        <ul>
          <li>Labels are US FDA labelling. Check local formulary and national guidance before use outside the US.</li>
          <li>Dose conversions use published equivalence tables and are starting points to titrate, never final doses.</li>
          <li>Methadone, fentanyl and buprenorphine conversions are always refused and need a specialist.</li>
          <li>An absent finding means the indexed label text did not mention it. It is not proof of safety.</li>
        </ul>
      </section>
    </div>
  );
}
