import { transport } from "./engine/transport";
import type {
  AuditVerification, BatchResponse, DoseConversion, DrugSuggestion, EquivalenceTable, Insights, Meta, PatientContext,
  Recommendation, RenalEstimate, ReviewInput, ReviewRecord, ReviewSummary, ShortageRecord, SubstitutionRequest,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string | null) {
    super(message);
  }
}

function describe(status: number, body: unknown): string {
  let detail = `Request failed with status ${status}.`;
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") detail = d;
  else if (Array.isArray(d)) {
    detail = d.map((x: { loc?: string[]; msg: string }) => `${(x.loc ?? []).slice(1).join(" ")}: ${x.msg}`).join("; ");
  }
  if (status === 429) detail = "Too many requests in the last minute. Wait a moment and try again.";
  if (status === 401) detail = "This deployment requires an API key.";
  return detail;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res;
  try {
    res = await transport(path, init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    const msg = e instanceof Error && e.message ? e.message : "";
    throw new ApiError(0, msg && !/fetch/i.test(msg)
      ? `The engine could not answer: ${msg}`
      : "The service could not be reached. Check that the API is running, then try again.");
  }
  if (res.status < 200 || res.status >= 300) throw new ApiError(res.status, describe(res.status, res.body), res.requestId);
  return res.body as T;
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  meta: () => call<Meta>("/api/v1/meta"),
  searchDrugs: (q: string, signal?: AbortSignal) =>
    call<DrugSuggestion[]>(`/api/v1/drugs/search?q=${encodeURIComponent(q)}&limit=8`, { signal }),
  shortages: (params: { status?: string; q?: string; limit: number; offset: number }) => {
    const sp = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
    if (params.status) sp.set("status", params.status);
    if (params.q) sp.set("q", params.q);
    return call<{ items: ShortageRecord[]; total: number }>(`/api/v1/shortages?${sp}`);
  },
  insights: () => call<Insights>("/api/v1/shortages/insights"),
  recommend: (body: SubstitutionRequest) => call<Recommendation>("/api/v1/substitutions", post(body)),
  batch: (requests: SubstitutionRequest[], labels?: string[]) =>
    call<BatchResponse>("/api/v1/substitutions/batch", post({ requests, labels })),
  renal: (p: PatientContext) => call<RenalEstimate>("/api/v1/renal/estimate", post(p)),
  equivalenceTables: () => call<EquivalenceTable[]>("/api/v1/equivalence/tables"),
  convert: (b: { from_drug: string; to_drug: string; dose: { amount: number; unit: string; frequency?: string | null };
                 from_route?: string | null; to_route?: string | null }) =>
    call<DoseConversion>("/api/v1/equivalence/convert", post(b)),
  note: (requestId: string, format: "markdown" | "text" = "markdown") =>
    call<string>(`/api/v1/audit/${encodeURIComponent(requestId)}/note?format=${format}`),
  audit: (requestId: string) =>
    call<{ request: SubstitutionRequest; response: Recommendation; record_hash?: string }>(
      `/api/v1/audit/${encodeURIComponent(requestId)}`),
  auditVerify: () => call<AuditVerification>("/api/v1/audit/verify"),
  review: (b: ReviewInput) => call<ReviewRecord>("/api/v1/reviews", post(b)),
  reviews: (requestId: string) => call<ReviewRecord[]>(`/api/v1/reviews?request_id=${encodeURIComponent(requestId)}`),
  reviewSummary: () => call<ReviewSummary>("/api/v1/reviews/summary"),
};
