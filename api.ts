import type { DrugSuggestion, Meta, Recommendation, ShortageRecord, SubstitutionRequest } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string | null) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(0, "The service could not be reached. Check that the API is running, then try again.");
  }
  const rid = res.headers.get("X-Request-ID");
  if (!res.ok) {
    let detail = `Request failed with status ${res.status}.`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        detail = body.detail
          .map((d: { loc?: string[]; msg: string }) => `${(d.loc ?? []).slice(1).join(" ")}: ${d.msg}`)
          .join("; ");
      }
    } catch {
      /* non JSON error */
    }
    if (res.status === 429) detail = "Too many requests in the last minute. Wait a moment and try again.";
    throw new ApiError(res.status, detail, rid);
  }
  return res.json() as Promise<T>;
}

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
  recommend: (body: SubstitutionRequest) =>
    call<Recommendation>("/api/v1/substitutions", { method: "POST", body: JSON.stringify(body) }),
};
