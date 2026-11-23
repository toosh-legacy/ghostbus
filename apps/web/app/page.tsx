"use client";
import Link from "next/link";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get, routeLabel } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

type StopRow = { stop_id: string; name: string; distance_m?: number; routes: string[] };

export default function Home() {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [geoErr, setGeoErr] = useState<string | null>(null);
  const routes = useQuery({ queryKey: ["routes"], queryFn: () => get("/v1/routes") });
  const found = useQuery({
    queryKey: ["search", search],
    queryFn: () => get<StopRow[]>("/v1/stops/search", { q: search }),
    enabled: search.length > 0,
  });
  // Geolocation is used once, only for this query, and never stored.
  const nearby = useQuery({
    queryKey: ["nearby", coords?.lat, coords?.lon],
    queryFn: () => get<StopRow[]>("/v1/stops/nearby", { lat: coords!.lat, lon: coords!.lon, radius_m: 800 }),
    enabled: !!coords,
  });
  const locate = () => {
    setGeoErr(null);
    navigator.geolocation?.getCurrentPosition(
      (p) => setCoords({ lat: p.coords.latitude, lon: p.coords.longitude }),
      (e) => setGeoErr(e.message),
      { maximumAge: 60000, timeout: 10000 },
    );
  };
  const list = (rows: StopRow[] | undefined, label: string) => (
    <ul className="divide-y rounded border bg-white" aria-label={label}>
      {rows?.map((s) => (
        <li key={s.stop_id}>
          <Link href={`/stops/${encodeURIComponent(s.stop_id)}`} className="block p-3 hover:bg-blue-50 focus:bg-blue-50">
            <span className="font-medium">{s.name}</span>{" "}
            {s.distance_m !== undefined && <span className="text-slate-700">· {s.distance_m} m</span>}
            <span className="block text-sm text-slate-700">Routes {s.routes.map(routeLabel).join(", ")}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Is my bus actually coming?</h1>
      <section aria-labelledby="find" className="space-y-3">
        <h2 id="find" className="text-xl font-semibold">Find a stop</h2>
        <form
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(q.trim());
          }}
          className="flex gap-2"
        >
          <label htmlFor="q" className="sr-only">Stop name or route</label>
          <input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Stop name or route (e.g. Nubian, 111)"
            className="flex-1 rounded border border-slate-500 p-2" />
          <button className="rounded bg-blue-800 px-4 py-2 text-white">Search</button>
        </form>
        <button onClick={locate} className="rounded border border-blue-800 px-4 py-2 text-blue-900">
          Use my location (stays on this device)
        </button>
        {geoErr && <p role="alert">Location unavailable: {geoErr}. Search by name instead.</p>}
        {nearby.isLoading && <Loading what="nearby stops" />}
        {nearby.data && (nearby.data.data.length ? list(nearby.data.data, "Nearby stops") : <p>No pilot-route stops within 800 m.</p>)}
        {found.isLoading && <Loading what="stops" />}
        {found.error && <ErrorBox error={found.error} />}
        {found.data && (found.data.data.length ? list(found.data.data, "Search results") : <p>No matching stops.</p>)}
      </section>
      <section aria-labelledby="pilot">
        <h2 id="pilot" className="text-xl font-semibold mb-2">Pilot routes</h2>
        {routes.isLoading && <Loading what="routes" />}
        {routes.error && <ErrorBox error={routes.error} />}
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {routes.data?.data.map((r: { route_id: string; short_name: string; long_name: string }) => (
            <li key={r.route_id}>
              <Link href={`/routes/${r.route_id}`} className="block rounded border bg-white p-3 hover:bg-blue-50">
                <span className="font-bold">{routeLabel(r.route_id)}</span>
                <span className="block text-sm text-slate-700">{r.long_name}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
