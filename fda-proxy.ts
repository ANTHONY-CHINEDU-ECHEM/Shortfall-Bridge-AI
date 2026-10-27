// Netlify Edge Function: same origin proxy to the openFDA query API for the in browser demo.
//
// Why: visitors share one edge cache (labels change weekly, shortages daily), so popular queries
// cost openFDA nothing; an optional OPENFDA_API_KEY raises limits without ever reaching the
// browser; and the demo keeps working even where a network blocks cross origin calls. Only the
// three endpoints the engine uses are forwarded, and only GET.

import type { Config, Context } from "@netlify/edge-functions";

const ALLOWED = /^\/drug\/(label|shortages|drugsfda)\.json$/;
const UPSTREAM = "https://api.fda.gov";

declare const Netlify: { env: { get(name: string): string | undefined } };

function json(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...extra },
  });
}

export default async function handler(request: Request, _context: Context): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json(405, { error: { code: "METHOD_NOT_ALLOWED", message: "Only GET is proxied" } }, { Allow: "GET" });
  }
  const url = new URL(request.url);
  const path = url.pathname.replace(/^\/api\/fda/, "");
  if (!ALLOWED.test(path)) return json(404, { error: { code: "NOT_FOUND", message: "Endpoint not proxied" } });
  if (url.search.length > 2000) return json(414, { error: { code: "URI_TOO_LONG", message: "Query too long" } });

  const upstream = new URL(UPSTREAM + path);
  url.searchParams.forEach((v, k) => upstream.searchParams.set(k, v));
  const serverKey = Netlify.env.get("OPENFDA_API_KEY");
  if (serverKey && !upstream.searchParams.has("api_key")) upstream.searchParams.set("api_key", serverKey);

  let res: Response;
  try {
    res = await fetch(upstream, { headers: { Accept: "application/json", "User-Agent": "ShortfallBridgeAI-demo/2.0" } });
  } catch {
    return json(502, { error: { code: "UPSTREAM_UNREACHABLE", message: "openFDA could not be reached" } });
  }
  const body = await res.text();
  const headers: Record<string, string> = { "Content-Type": "application/json; charset=utf-8" };
  if (res.status === 200 || res.status === 404) {
    // 404 is openFDA's "no matches", which is just as cacheable as a hit.
    const ttl = path.includes("shortages") ? 3600 : 86400;
    headers["Cache-Control"] = "public, max-age=300";
    headers["Netlify-CDN-Cache-Control"] = `public, s-maxage=${ttl}, stale-while-revalidate=${ttl}`;
    headers["Netlify-Vary"] = "query";
  } else {
    headers["Cache-Control"] = "no-store";
  }
  return new Response(request.method === "HEAD" ? null : body, { status: res.status, headers });
}

export const config: Config = { path: "/api/fda/*", cache: "manual" };
