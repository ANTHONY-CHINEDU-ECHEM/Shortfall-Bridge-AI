/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENGINE?: "server" | "browser";
  readonly VITE_PYODIDE_URL?: string;
  readonly VITE_OPENFDA_BASES?: string;
  readonly VITE_PY_BUNDLE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
