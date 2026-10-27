/// <reference lib="webworker" />
// Engine worker: loads Pyodide, mounts the shortfall_bridge Python package from a JSON bundle and
// answers API calls with the real engine. Synchronous XMLHttpRequest (used for lazy fetches) is
// allowed in workers, and running here keeps the UI responsive while labels are indexed.

import type { BootOptions, FromWorker, ToWorker } from "./protocol";

declare const self: DedicatedWorkerGlobalScope;

interface PyProxy {
  request: (method: string, url: string, body: string | null) => Promise<string>;
  set_api_key: (key: string | null) => void;
  set_progress_callback: (cb: (kind: string) => void) => void;
}
interface Pyodide {
  loadPackage: (names: string[]) => Promise<void>;
  runPython: (code: string) => unknown;
  FS: { mkdirTree: (p: string) => void; writeFile: (p: string, data: string) => void };
}

let runtime: PyProxy | null = null;
const queue: ToWorker[] = [];
const post = (m: FromWorker) => self.postMessage(m);

async function probe(base: string): Promise<boolean> {
  try {
    const url = `${base.replace(/\/$/, "")}/drug/shortages.json?limit=1`;
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) return false;
    const body = await res.json();
    return Array.isArray(body?.results);
  } catch {
    return false;
  }
}

async function boot(o: BootOptions) {
  const t0 = performance.now();
  post({ type: "progress", stage: "runtime", detail: "Downloading the Python runtime (WebAssembly)" });
  const mod = await import(/* @vite-ignore */ `${o.pyodideUrl}pyodide.mjs`);
  const py: Pyodide = await mod.loadPyodide({ indexURL: o.pyodideUrl });

  post({ type: "progress", stage: "packages", detail: "Loading numpy, pydantic and PyYAML" });
  await py.loadPackage(["numpy", "pydantic", "pyyaml"]);

  post({ type: "progress", stage: "engine", detail: "Mounting the Shortfall Bridge engine" });
  const bundle = await (await fetch(o.bundleUrl)).json() as { version: string; files: Record<string, string> };
  for (const [rel, text] of Object.entries(bundle.files)) {
    const full = `/home/pyodide/sfb/${rel}`;
    py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
    py.FS.writeFile(full, text);
  }
  py.runPython("import sys; sys.path.insert(0, '/home/pyodide/sfb')");

  post({ type: "progress", stage: "data-source", detail: "Connecting to openFDA" });
  let base = o.openfdaBases[o.openfdaBases.length - 1];
  for (const b of o.openfdaBases) {
    if (await probe(b)) { base = b; break; }
  }
  const absolute = base.startsWith("/") ? `${self.location.origin}${base}` : base;
  const make = py.runPython(`
from shortfall_bridge.browser.runtime import BrowserRuntime
BrowserRuntime
`) as (base_url: string, api_key: string | null) => PyProxy;
  runtime = make(absolute, o.apiKey);
  runtime.set_progress_callback((kind: string) => post({ type: "fetch", kind }));
  post({ type: "ready", base, version: bundle.version, elapsedMs: Math.round(performance.now() - t0) });
  while (queue.length) handle(queue.shift()!);
}

async function handle(msg: ToWorker) {
  if (msg.type === "boot") {
    try {
      await boot(msg.options);
    } catch (e) {
      post({ type: "boot-error", message: e instanceof Error ? e.message : String(e) });
    }
    return;
  }
  if (!runtime) {
    queue.push(msg);
    return;
  }
  if (msg.type === "set-key") {
    runtime.set_api_key(msg.apiKey);
    return;
  }
  try {
    const raw = await runtime.request(msg.method, msg.url, msg.body);
    post({ type: "response", id: msg.id, envelope: JSON.parse(raw) });
  } catch (e) {
    post({ type: "response-error", id: msg.id, message: e instanceof Error ? e.message : String(e) });
  }
}

self.onmessage = (ev: MessageEvent<ToWorker>) => { void handle(ev.data); };
