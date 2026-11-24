"use client";
import { useEffect, useRef } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Map as MlMap, StyleSpecification } from "maplibre-gl";
import type { MapboxOverlay } from "@deck.gl/mapbox";

export type Vehicle = { vehicle_id: string; lat: number; lon: number; age_s: number; stale: boolean; trip_id?: string };
type Shape = { coords: [number, number][] }; // [lat, lon] pairs from the API
type Stop = { lat: number; lon: number; name: string };

// OpenStreetMap raster tiles for local development only (light use, attribution shown).
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256,
           attribution: "© OpenStreetMap contributors", maxzoom: 18 },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const ROUTE: [number, number, number] = [30, 58, 138];
const LIVE: [number, number, number] = [185, 28, 28];
const STALE: [number, number, number, number] = [100, 116, 139, 110];

/** MapLibre GL base map with deck.gl layers: route paths, stops, and live buses (stale buses greyed out). */
export default function LiveMap({ shapes, stops, vehicles }: { shapes: Shape[]; stops: Stop[]; vehicles: Vehicle[] }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const deck = useRef<typeof import("@deck.gl/layers") | null>(null);

  const layers = (vs: Vehicle[]) => {
    const L = deck.current;
    if (!L) return [];
    return [
      new L.PathLayer<Shape>({ id: "routes", data: shapes, getPath: (s) => s.coords.map(([la, lo]) => [lo, la] as [number, number]),
        getColor: ROUTE, getWidth: 4, widthUnits: "pixels" }),
      new L.ScatterplotLayer<Stop>({ id: "stops", data: stops, getPosition: (s) => [s.lon, s.lat], getRadius: 3,
        radiusUnits: "pixels", getFillColor: [51, 65, 85], pickable: true }),
      new L.ScatterplotLayer<Vehicle>({ id: "buses", data: vs, getPosition: (v) => [v.lon, v.lat], getRadius: 8,
        radiusUnits: "pixels", getFillColor: (v) => (v.stale ? STALE : LIVE), stroked: true, getLineColor: [255, 255, 255],
        lineWidthMinPixels: 1.5, pickable: true, updateTriggers: { getFillColor: vs.map((v) => v.stale).join() } }),
    ];
  };

  useEffect(() => {
    let cancelled = false;
    Promise.all([import("maplibre-gl"), import("@deck.gl/mapbox"), import("@deck.gl/layers")]).then(([ml, mb, L]) => {
      if (cancelled || !el.current || map.current) return;
      deck.current = L;
      const m = new ml.Map({ container: el.current, style: STYLE, center: [-71.06, 42.35], zoom: 11, attributionControl: { compact: true } });
      m.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
      const all = shapes.flatMap((s) => s.coords);
      if (all.length) {
        const lons = all.map((c) => c[1]), lats = all.map((c) => c[0]);
        m.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 24, duration: 0 });
      }
      const o = new mb.MapboxOverlay({
        interleaved: false, layers: layers(vehicles),
        getTooltip: ({ object }) => (object && "vehicle_id" in object
          ? `Bus ${object.vehicle_id} · ${object.age_s}s ago${object.stale ? " (stale)" : ""}`
          : object && "name" in object ? object.name : null),
      });
      m.addControl(o);
      overlay.current = o;
      map.current = m;
    });
    return () => { cancelled = true; map.current?.remove(); map.current = null; overlay.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    overlay.current?.setProps({ layers: layers(vehicles) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicles]);

  return <div ref={el} className="h-80 w-full overflow-hidden rounded border" role="img" aria-label="Map of the route with live buses; a list follows" />;
}
