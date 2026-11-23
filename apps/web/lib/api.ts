// Typed API client generated from packages/schemas/openapi.json (run `npm run gen:api` after API changes).
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const API_BASE = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";
export const client = createClient<paths>({ baseUrl: API_BASE });

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Envelope<T = any> = { data: T; data_as_of: string | null; feed_health?: FeedHealth };
export type FeedHealth = { healthy: boolean; stale: boolean; reason: string; last_fetch_utc: string | null; mode: string };

export async function get<T = any>(path: string, query?: Record<string, string | number | undefined>): Promise<Envelope<T>> {
  const u = new URL(path, API_BASE);
  Object.entries(query ?? {}).forEach(([k, v]) => v !== undefined && v !== "" && u.searchParams.set(k, String(v)));
  const r = await fetch(u, { cache: "no-store" });
  if (!r.ok) {
    const p = await r.json().catch(() => ({ title: r.statusText }));
    throw new Error(p.title ?? `HTTP ${r.status}`);
  }
  return r.json();
}

export const routeLabel = (id: string) => ({ "741": "SL1", "743": "SL3" } as Record<string, string>)[id] ?? id;

export function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(1)}%`;
}

export function minutesFromNow(iso: string, nowIso?: string | null) {
  const now = nowIso ? Date.parse(nowIso) : Date.now();
  return Math.round((Date.parse(iso) - now) / 60000);
}

export function localTime(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
}
