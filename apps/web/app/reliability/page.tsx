"use client";
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE, get, pct, routeLabel } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

type Row = {
  route_id: string; on_time_performance: number | null; headway_adherence: number | null; excess_wait_time_s: number | null;
  bunching_rate: number | null; missed_trip_rate: number | null; missed_trips: number; scheduled_trips: number; unknown_route_days: number;
};
const BANDS = ["all", "am_peak", "midday", "pm_peak", "evening", "early_late"];

export default function Reliability() {
  const [month, setMonth] = useState<string>("");
  const [band, setBand] = useState("all");
  const [day, setDay] = useState("all");
  const q = useQuery({
    queryKey: ["rel", month, band, day],
    queryFn: () => get<Row[]>("/v1/reliability/routes", { month, timeband: band, day_type: day }) as Promise<{
      data: Row[]; month: string; months_available: string[]; data_as_of: string | null }>,
  });
  const cols: [keyof Row, string, (v: Row) => string][] = [
    ["on_time_performance", "On-time", (r) => pct(r.on_time_performance)],
    ["headway_adherence", "Headway adherence", (r) => pct(r.headway_adherence)],
    ["excess_wait_time_s", "Excess wait", (r) => (r.excess_wait_time_s === null ? "n/a" : `${Math.round(r.excess_wait_time_s)} s`)],
    ["bunching_rate", "Bunching", (r) => pct(r.bunching_rate)],
    ["missed_trip_rate", "Missed trips", (r) => `${pct(r.missed_trip_rate)} (${r.missed_trips}/${r.scheduled_trips})`],
  ];
  const defs: Record<string, string> = { on_time_performance: "on_time_performance", headway_adherence: "headway_adherence",
    excess_wait_time_s: "excess_wait_time", bunching_rate: "bunching_rate", missed_trip_rate: "missed_trip_rate" };
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Route reliability</h1>
      <p className="text-slate-700">From the MBTA&apos;s official arrival/departure records. Every column links to its definition.</p>
      <div className="flex flex-wrap gap-3">
        <label>Month{" "}
          <select value={month || q.data?.month || ""} onChange={(e) => setMonth(e.target.value)} className="border p-1">
            {(q.data?.months_available ?? []).map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <label>Time band{" "}
          <select value={band} onChange={(e) => setBand(e.target.value)} className="border p-1">
            {BANDS.map((b) => <option key={b} value={b}>{b.replace("_", " ")}</option>)}
          </select>
        </label>
        <label>Days{" "}
          <select value={day} onChange={(e) => setDay(e.target.value)} className="border p-1">
            {["all", "weekday", "weekend"].map((b) => <option key={b}>{b}</option>)}
          </select>
        </label>
        <a className="underline" href={`${API_BASE}/v1/reliability/export.csv${month ? `?month=${month}` : ""}`}>Download CSV</a>
      </div>
      {q.isLoading && <Loading what="metrics" />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && (
        <div className="overflow-x-auto">
          <table className="w-full bg-white text-left text-sm">
            <caption className="text-left p-1">Pilot routes, {q.data.month}, {band.replace("_", " ")}, {day} days</caption>
            <thead>
              <tr>
                <th scope="col">Route</th>
                {cols.map(([k, label]) => (
                  <th key={k} scope="col"><Link className="underline" href={`/methodology#${defs[k]}`}>{label}</Link></th>
                ))}
              </tr>
            </thead>
            <tbody>
              {q.data.data.map((r) => (
                <tr key={r.route_id} className="border-t">
                  <th scope="row"><Link className="underline" href={`/reliability/${r.route_id}`}>{routeLabel(r.route_id)}</Link></th>
                  {cols.map(([k, , f]) => <td key={k}>{f(r)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
