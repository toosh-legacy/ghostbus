"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE, get, routeLabel } from "@/lib/api";
import { ErrorBox, Loading, StaleBanner } from "@/components/ui";
import type { Vehicle } from "@/components/LiveMap";

const LiveMap = dynamic(() => import("@/components/LiveMap"), { ssr: false });

type Live = { shapes: { coords: [number, number][] }[]; stops: { stop_id: string; lat: number; lon: number; name: string }[]; vehicles: Vehicle[] };

export default function RoutePage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["live", id], queryFn: () => get<Live>(`/v1/routes/${id}/live`), refetchInterval: 20000 });
  const [vehicles, setVehicles] = useState<Record<string, Vehicle>>({});

  useEffect(() => {
    if (q.data) setVehicles(Object.fromEntries(q.data.data.vehicles.map((v) => [v.vehicle_id, v])));
  }, [q.data]);

  // SSE: vehicle updates within ~2 s of processing
  useEffect(() => {
    const es = new EventSource(`${API_BASE}/v1/stream?route=${encodeURIComponent(id)}`);
    es.addEventListener("vehicle", (ev) => {
      const v = JSON.parse((ev as MessageEvent).data);
      if (v.route_id !== id) return;
      setVehicles((cur) => ({ ...cur, [v.vehicle_id]: { ...v, age_s: 0, stale: false } }));
    });
    return () => es.close();
  }, [id]);

  if (q.isLoading) return <Loading what="route" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const vs = Object.values(vehicles).sort((a, b) => a.vehicle_id.localeCompare(b.vehicle_id));
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Route {routeLabel(id)} — live</h1>
      <StaleBanner health={q.data!.feed_health} />
      <LiveMap shapes={q.data!.data.shapes} stops={q.data!.data.stops} vehicles={vs} />
      <section aria-labelledby="vlist">
        <h2 id="vlist" className="text-xl font-semibold">Buses on this route ({vs.length})</h2>
        <table className="w-full bg-white text-left">
          <thead><tr><th scope="col">Bus</th><th scope="col">Trip</th><th scope="col">Last update</th></tr></thead>
          <tbody>
            {vs.map((v) => (
              <tr key={v.vehicle_id} className={v.stale ? "text-slate-500" : ""}>
                <td>{v.vehicle_id}</td><td>{v.trip_id ?? "—"}</td>
                <td>{v.age_s}s ago{v.stale ? " (stale, older than 2 min)" : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section aria-labelledby="slist">
        <h2 id="slist" className="text-xl font-semibold">Stops</h2>
        <ul className="columns-2 text-sm">
          {q.data!.data.stops.map((s) => (
            <li key={s.stop_id}><Link className="underline" href={`/stops/${encodeURIComponent(s.stop_id)}`}>{s.name}</Link></li>
          ))}
        </ul>
      </section>
    </div>
  );
}
