"use client";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { API_BASE, get, pct, routeLabel } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function RouteReliability() {
  const { routeId } = useParams<{ routeId: string }>();
  const q = useQuery({ queryKey: ["relr", routeId], queryFn: () => get<any>(`/v1/reliability/routes/${routeId}`) });
  if (q.isLoading) return <Loading what="route reliability" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!.data;
  const max = Math.max(...d.trend.map((t: any) => t.missed_trip_rate ?? 0), 0.0001);
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Route {routeLabel(routeId)} reliability</h1>
      <section aria-labelledby="trend">
        <h2 id="trend" className="text-xl font-semibold">Monthly trend</h2>
        <table className="w-full bg-white text-sm text-left">
          <thead><tr><th scope="col">Month</th><th scope="col">On-time</th><th scope="col">Headway adherence</th>
            <th scope="col">Bunching</th><th scope="col">Missed-trip rate</th></tr></thead>
          <tbody>
            {d.trend.map((t: any) => (
              <tr key={t.month} className="border-t">
                <th scope="row">{t.month}</th><td>{pct(t.on_time_performance)}</td><td>{pct(t.headway_adherence)}</td>
                <td>{pct(t.bunching_rate)}</td>
                <td>
                  <span className="inline-block h-3 bg-red-700 align-middle mr-2" style={{ width: `${(100 * (t.missed_trip_rate ?? 0)) / max}px` }} aria-hidden="true" />
                  {pct(t.missed_trip_rate)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section aria-labelledby="worst">
        <h2 id="worst" className="text-xl font-semibold">Worst timepoints ({d.worst_timepoints_month}, by 90th-percentile delay)</h2>
        <table className="w-full bg-white text-sm text-left">
          <thead><tr><th scope="col">Timepoint</th><th scope="col">Direction</th><th scope="col">Median delay</th><th scope="col">90th pct</th></tr></thead>
          <tbody>
            {d.worst_timepoints.map((w: any) => (
              <tr key={`${w.direction_id}-${w.time_point_id}`} className="border-t">
                <th scope="row">{w.time_point_id}</th><td>{w.direction_id === 0 ? "Outbound" : "Inbound"}</td>
                <td>{Math.round(w.median_delay_s / 60)} min</td><td>{Math.round(w.p90_delay_s / 60)} min</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <a className="underline" href={`${API_BASE}/v1/reliability/export.csv`}>Download all metrics as CSV</a>
    </div>
  );
}
