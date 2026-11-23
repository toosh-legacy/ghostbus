"use client";
import { useEffect, useRef } from "react";
import type * as L from "leaflet";
import "leaflet/dist/leaflet.css";

export type Vehicle = { vehicle_id: string; lat: number; lon: number; age_s: number; stale: boolean; trip_id?: string };

// Leaflet + OpenStreetMap tiles for local development only (light use, attribution shown).
export default function LiveMap({ shapes, stops, vehicles }: {
  shapes: { coords: [number, number][] }[]; stops: { lat: number; lon: number; name: string }[]; vehicles: Vehicle[];
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const lib = useRef<typeof L | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("leaflet").then((Lm) => {
      if (cancelled || !el.current || map.current) return;
      lib.current = Lm;
      const m = Lm.map(el.current, { zoomControl: true });
      Lm.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18, attribution: "© OpenStreetMap contributors",
      }).addTo(m);
      const all: [number, number][] = [];
      shapes.forEach((s) => { Lm.polyline(s.coords, { color: "#1e3a8a", weight: 4 }).addTo(m); all.push(...s.coords); });
      stops.forEach((s) => Lm.circleMarker([s.lat, s.lon], { radius: 3, color: "#334155" }).bindTooltip(s.name).addTo(m));
      if (all.length) m.fitBounds(Lm.latLngBounds(all));
      layer.current = Lm.layerGroup().addTo(m);
      map.current = m;
    });
    return () => { cancelled = true; map.current?.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const Lm = lib.current;
    if (!Lm || !layer.current) return;
    layer.current.clearLayers();
    vehicles.forEach((v) => {
      Lm.circleMarker([v.lat, v.lon], {
        radius: 8, color: v.stale ? "#64748b" : "#b91c1c", fillOpacity: v.stale ? 0.3 : 0.9, opacity: v.stale ? 0.4 : 1,
      }).bindTooltip(`Bus ${v.vehicle_id} · ${v.age_s}s ago${v.stale ? " (stale)" : ""}`).addTo(layer.current!);
    });
  }, [vehicles]);

  return <div ref={el} className="h-80 w-full rounded border" role="img" aria-label="Map of the route with live buses; a list follows" />;
}
