// Shareable requests: the order is encoded in the hash query, e.g.
//   #/workbench?drug=furosemide&route=intravenous&dose=40&freq=twice%20daily&crcl=42&meds=lisinopril&run=1
// Opening such a link prefills the workbench and, with run=1, runs it. Nothing identifies a patient.

import { EMPTY_ORDER, type OrderState } from "./components/OrderPanel";
import type { CreatinineUnit, HepaticImpairment, Sex } from "./types";

const KEYS: [keyof OrderState, string][] = [
  ["drug", "drug"], ["route", "route"], ["dosageForm", "form"], ["strength", "strength"], ["doseAmount", "dose"],
  ["doseUnit", "unit"], ["frequency", "freq"], ["age", "age"], ["weight", "wt"], ["height", "ht"], ["sex", "sex"],
  ["scr", "scr"], ["scrUnit", "scru"], ["crcl", "crcl"], ["egfr", "egfr"], ["hepatic", "hep"], ["question", "q"],
];

export function encodeOrder(o: OrderState, run = true): string {
  const sp = new URLSearchParams();
  for (const [k, short] of KEYS) {
    const v = o[k];
    if (typeof v === "string" && v && v !== (EMPTY_ORDER[k] as string)) sp.set(short, v);
  }
  if (o.pregnant) sp.set("preg", "1");
  if (o.breastfeeding) sp.set("bf", "1");
  if (o.dialysis) sp.set("dial", "1");
  if (!o.includeAltClass) sp.set("noalt", "1");
  if (o.allergies.length) sp.set("allergies", o.allergies.join("|"));
  if (o.meds.length) sp.set("meds", o.meds.join("|"));
  if (run) sp.set("run", "1");
  return sp.toString();
}

export function decodeOrder(query: string): { order: Partial<OrderState>; run: boolean; ref: string | null } | null {
  const sp = new URLSearchParams(query);
  const ref = sp.get("ref");
  if (!sp.get("drug") && !ref) return null;
  const order: Partial<OrderState> = {};
  for (const [k, short] of KEYS) {
    const v = sp.get(short);
    if (v != null) (order as Record<string, string>)[k] = v.slice(0, 300);
  }
  if (order.sex && !["female", "male", "unknown"].includes(order.sex)) order.sex = "unknown" as Sex;
  if (order.scrUnit && !["mg/dL", "umol/L"].includes(order.scrUnit)) order.scrUnit = "mg/dL" as CreatinineUnit;
  if (order.hepatic && !["none", "mild", "moderate", "severe"].includes(order.hepatic)) order.hepatic = "none" as HepaticImpairment;
  order.pregnant = sp.get("preg") === "1";
  order.breastfeeding = sp.get("bf") === "1";
  order.dialysis = sp.get("dial") === "1";
  order.includeAltClass = sp.get("noalt") !== "1";
  const list = (k: string) => (sp.get(k) ?? "").split("|").map((x) => x.trim()).filter(Boolean).slice(0, 20);
  order.allergies = list("allergies");
  order.meds = list("meds");
  return { order, run: sp.get("run") === "1", ref };
}

export function shareUrl(o: OrderState): string {
  const base = `${window.location.origin}${window.location.pathname}${window.location.search}`;
  return `${base}#/workbench?${encodeOrder(o)}`;
}
