"use client";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type TrendRow = { month: string; on_time_performance: number | null; headway_adherence: number | null;
  bunching_rate: number | null; missed_trip_rate: number | null };

const SERIES: { key: keyof TrendRow; label: string; color: string; dash?: string }[] = [
  { key: "on_time_performance", label: "On-time", color: "#1e3a8a" },
  { key: "headway_adherence", label: "Headway adherence", color: "#0f766e", dash: "6 3" },
  { key: "bunching_rate", label: "Bunching", color: "#b45309", dash: "2 3" },
  { key: "missed_trip_rate", label: "Missed trips", color: "#b91c1c" },
];
const pctTick = (v: number) => `${Math.round(v * 100)}%`;
const pctTip = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "n/a");

/** Monthly reliability trend for one route (Recharts). Lines differ by dash pattern as well as colour. */
export function TrendChart({ data }: { data: TrendRow[] }) {
  return (
    <figure aria-label="Monthly reliability trend; the same numbers are in the table below">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="month" tick={{ fontSize: 12 }} />
          <YAxis tickFormatter={pctTick} domain={[0, 1]} tick={{ fontSize: 12 }} width={44} />
          <Tooltip formatter={pctTip} />
          <Legend />
          {SERIES.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color} strokeDasharray={s.dash}
                  strokeWidth={2} dot={{ r: 3 }} connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

/** Missed-trip rate by route for the selected month and filters. */
export function MissedTripBars({ data, label }: { data: { route: string; missed_trip_rate: number | null }[]; label: string }) {
  return (
    <figure aria-label={`Missed-trip rate by route, ${label}; the same numbers are in the table below`}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#e2e8f0" vertical={false} />
          <XAxis dataKey="route" tick={{ fontSize: 12 }} />
          <YAxis tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`} tick={{ fontSize: 12 }} width={52} />
          <Tooltip formatter={pctTip} />
          <Bar dataKey="missed_trip_rate" name="Missed-trip rate" fill="#b91c1c" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </figure>
  );
}
