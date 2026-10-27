// Recent requests, kept in this browser only (a per viewer convenience). Patient values are not
// stored: only the drug, presentation and dose, so a shared workstation does not retain context.

import type { OrderState } from "./components/OrderPanel";

const KEY = "sfb-recent";
const MAX = 8;

export interface RecentItem {
  at: string;
  label: string;
  order: Pick<OrderState, "drug" | "route" | "dosageForm" | "strength" | "doseAmount" | "doseUnit" | "frequency">;
  status: string;
  top: string | null;
}

export function readRecent(): RecentItem[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw) ? raw.slice(0, MAX) : [];
  } catch {
    return [];
  }
}

export function pushRecent(o: OrderState, status: string, top: string | null): RecentItem[] {
  const item: RecentItem = {
    at: new Date().toISOString(),
    label: [o.drug, o.route, o.doseAmount && `${o.doseAmount} ${o.doseUnit}`].filter(Boolean).join(", "),
    order: { drug: o.drug, route: o.route, dosageForm: o.dosageForm, strength: o.strength, doseAmount: o.doseAmount,
             doseUnit: o.doseUnit, frequency: o.frequency },
    status, top,
  };
  const next = [item, ...readRecent().filter((r) => r.label !== item.label)].slice(0, MAX);
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* storage unavailable */ }
  return next;
}

export function clearRecent(): void {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}
