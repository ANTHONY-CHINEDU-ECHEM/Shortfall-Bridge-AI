// Messages between the UI thread and the engine worker.

export interface BootOptions {
  pyodideUrl: string;
  bundleUrl: string;
  openfdaBases: string[];
  apiKey: string | null;
}

export type ToWorker =
  | { type: "boot"; options: BootOptions }
  | { type: "request"; id: number; method: string; url: string; body: string | null }
  | { type: "set-key"; apiKey: string | null };

export interface Envelope {
  status: number;
  content_type: string;
  body: unknown;
}

export type BootStage = "runtime" | "packages" | "engine" | "data-source" | "ready";

export type FromWorker =
  | { type: "progress"; stage: BootStage; detail?: string }
  | { type: "ready"; base: string; version: string; elapsedMs: number }
  | { type: "boot-error"; message: string }
  | { type: "fetch"; kind: string }
  | { type: "response"; id: number; envelope: Envelope }
  | { type: "response-error"; id: number; message: string };
