// Build and runtime configuration for where the engine runs.
//
// VITE_ENGINE=server   (default) the React app calls the FastAPI service at /api.
// VITE_ENGINE=browser  the full Python engine runs in a Web Worker through Pyodide and reads live
//                      openFDA data. This is the Netlify demo: no server, no ingestion.
// ?engine=browser|server in the URL overrides the build default for one session.

export type EngineMode = "server" | "browser";

const env = import.meta.env;

function fromQuery(): EngineMode | null {
  try {
    const q = new URLSearchParams(window.location.search).get("engine");
    return q === "browser" || q === "server" ? q : null;
  } catch {
    return null;
  }
}

export const ENGINE_MODE: EngineMode = fromQuery() ?? (env.VITE_ENGINE === "browser" ? "browser" : "server");

// Pyodide 0.28.3 (Python 3.13). Pinned: the engine is tested against this exact runtime.
export const PYODIDE_URL: string = env.VITE_PYODIDE_URL || "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/";

// Candidate openFDA bases, tried in order. The first is the site's own edge proxy on Netlify
// (shared cache and an optional server side key); the second is openFDA directly (CORS enabled).
export const OPENFDA_BASES: string[] = (env.VITE_OPENFDA_BASES || "/api/fda,https://api.fda.gov")
  .split(",")
  .map((s: string) => s.trim())
  .filter(Boolean);

export const PY_BUNDLE_URL: string = env.VITE_PY_BUNDLE_URL || `${import.meta.env.BASE_URL}py/shortfall_bridge.json`;
