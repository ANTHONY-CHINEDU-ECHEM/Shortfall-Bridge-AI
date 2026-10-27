// Transport: the one place that knows whether API calls go to a server or to the in browser engine.
// Both return the same payloads and the same status codes, so the rest of the app is unaware.

import { ENGINE_MODE, OPENFDA_BASES, PYODIDE_URL, PY_BUNDLE_URL, type EngineMode } from "./config";
import type { BootStage, Envelope, FromWorker, ToWorker } from "./protocol";

export interface RawResponse {
  status: number;
  contentType: string;
  body: unknown;
  requestId: string | null;
}

export interface EngineState {
  mode: EngineMode;
  phase: "booting" | "ready" | "error";
  stage?: BootStage;
  detail?: string;
  base?: string;
  version?: string;
  bootMs?: number;
  fetches: number;
  lastFetch?: string;
  error?: string;
}

type Listener = (s: EngineState) => void;

const KEY_STORAGE = "sfb-openfda-key";

export function readApiKey(): string | null {
  try { return localStorage.getItem(KEY_STORAGE) || null; } catch { return null; }
}

class EngineHub {
  state: EngineState = { mode: ENGINE_MODE, phase: ENGINE_MODE === "server" ? "ready" : "booting", fetches: 0 };
  private listeners = new Set<Listener>();
  private worker: Worker | null = null;
  private nextId = 1;
  private pending = new Map<number, { resolve: (r: RawResponse) => void; reject: (e: Error) => void }>();

  subscribe(l: Listener): () => void {
    this.listeners.add(l);
    l(this.state);
    return () => this.listeners.delete(l);
  }

  private set(patch: Partial<EngineState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l(this.state));
  }

  start() {
    if (this.state.mode !== "browser" || this.worker) return;
    this.worker = new Worker(new URL("./engine.worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (ev: MessageEvent<FromWorker>) => this.onMessage(ev.data);
    this.worker.onerror = (ev) => this.set({ phase: "error", error: ev.message || "The engine worker failed to start." });
    this.send({ type: "boot", options: { pyodideUrl: PYODIDE_URL, bundleUrl: PY_BUNDLE_URL, openfdaBases: OPENFDA_BASES,
                                         apiKey: readApiKey() } });
  }

  setApiKey(key: string | null) {
    try {
      if (key) localStorage.setItem(KEY_STORAGE, key); else localStorage.removeItem(KEY_STORAGE);
    } catch { /* storage unavailable: the key still applies to this session */ }
    this.send({ type: "set-key", apiKey: key });
  }

  private send(m: ToWorker) { this.worker?.postMessage(m); }

  private onMessage(m: FromWorker) {
    switch (m.type) {
      case "progress": this.set({ stage: m.stage, detail: m.detail }); break;
      case "ready": this.set({ phase: "ready", stage: "ready", base: m.base, version: m.version, bootMs: m.elapsedMs }); break;
      case "boot-error":
        this.set({ phase: "error", error: m.message });
        this.pending.forEach((p) => p.reject(new Error(m.message)));
        this.pending.clear();
        break;
      case "fetch": this.set({ fetches: this.state.fetches + 1, lastFetch: m.kind }); break;
      case "response": {
        const p = this.pending.get(m.id);
        this.pending.delete(m.id);
        const e: Envelope = m.envelope;
        p?.resolve({ status: e.status, contentType: e.content_type, body: e.body, requestId: null });
        break;
      }
      case "response-error": {
        const p = this.pending.get(m.id);
        this.pending.delete(m.id);
        p?.reject(new Error(m.message));
        break;
      }
    }
  }

  request(method: string, url: string, body: string | null): Promise<RawResponse> {
    if (this.state.phase === "error") return Promise.reject(new Error(this.state.error ?? "Engine unavailable"));
    this.start();
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.send({ type: "request", id, method, url, body });
    });
  }
}

export const engine = new EngineHub();

export async function transport(path: string, init?: RequestInit): Promise<RawResponse> {
  const method = (init?.method ?? "GET").toUpperCase();
  if (engine.state.mode === "browser") {
    if (init?.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    return engine.request(method, path, (init?.body as string | undefined) ?? null);
  }
  const res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  const contentType = res.headers.get("Content-Type") ?? "";
  let body: unknown = null;
  try {
    body = contentType.includes("json") ? await res.json() : await res.text();
  } catch { /* empty or malformed body */ }
  return { status: res.status, contentType, body, requestId: res.headers.get("X-Request-ID") };
}
