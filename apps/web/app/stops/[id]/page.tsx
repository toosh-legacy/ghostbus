"use client";

import Link from "next/link";

import { useParams } from "next/navigation";

import { useQuery } from "@tanstack/react-query";

import { get, localTime, routeLabel } from "@/lib/api";

import { ErrorBox, GhostBadge, Loading, StaleBanner } from "@/components/ui";



type Arrival = {

  trip_id: string; route_id: string; direction_id: number; headsign: string; scheduled_utc: string;

  predicted: { p10_utc: string; p50_utc: string; p90_utc: string; range_text: string; confidence: number };

  source: string; model_version: string; tracking: boolean; status_text: string;

  ghost: { state: string; reason: string; def_version: string; prior_prob: number | null };

};



const SOURCE: Record<string, string> = { model: "our model", agency: "agency prediction", schedule: "schedule + history" };



export default function StopPage() {

  const { id } = useParams<{ id: string }>();

  const stopId = decodeURIComponent(id);

  const q = useQuery({

    queryKey: ["arrivals", stopId],

    queryFn: () => get<{ stop: { name: string; routes: string[] }; arrivals: Arrival[] }>(`/v1/stops/${encodeURIComponent(stopId)}/arrivals`),

    refetchInterval: 15000,

  });

  const alerts = useQuery({

    queryKey: ["alerts"],

    queryFn: () => get<{ id: string; routes: string[]; effect: string; header: string }[]>("/v1/alerts"),

    refetchInterval: 60000,

  });

  if (q.isLoading) return <Loading what="arrivals" />;

  if (q.error) return <ErrorBox error={q.error} />;

  const d = q.data!;

  return (

    <div className="space-y-4">

      <h1 className="text-2xl font-bold">{d.data.stop.name}</h1>

      <p className="text-slate-700">

        Routes {d.data.stop.routes.map((r) => <Link key={r} className="underline mr-2" href={`/routes/${r}`}>{routeLabel(r)}</Link>)}

        · as of {localTime(d.data_as_of)}

      </p>

      <StaleBanner health={d.feed_health} />

      {(() => {

        const rs = new Set(d.data.stop.routes);

        const rel = (alerts.data?.data ?? []).filter((a) => a.routes.some((r) => rs.has(r)));

        return rel.length ? (

          <section aria-labelledby="alerts" className="rounded border border-blue-800 bg-blue-50 p-3">

            <h2 id="alerts" className="font-semibold">Service alerts ({rel.length})</h2>

            <ul className="list-disc pl-5 text-sm">

              {rel.slice(0, 5).map((a) => (

                <li key={a.id}><span className="font-medium">{a.effect.replaceAll("_", " ").toLowerCase()}</span> · {a.header}</li>

              ))}

            </ul>

          </section>

        ) : null;

      })()}

      {d.data.arrivals.length === 0 && <p>No pilot-route buses scheduled in the next hour.</p>}

      <ul className="space-y-3" aria-label="Upcoming buses">

        {d.data.arrivals.map((a) => (

          <li key={a.trip_id} className="rounded border bg-white p-3">

            <div className="flex flex-wrap items-baseline justify-between gap-2">

              <span className="text-lg font-bold">

                {routeLabel(a.route_id)} <span className="font-normal">to {a.headsign}</span>

              </span>

              <span className="text-xl font-semibold">{a.predicted.range_text}</span>

            </div>

            <p className="text-sm text-slate-700">

              Scheduled {localTime(a.scheduled_utc)} · likely {localTime(a.predicted.p10_utc)}–{localTime(a.predicted.p90_utc)}

              {" "}(80% range) · source: {SOURCE[a.source] ?? a.source} · {a.status_text}

            </p>

            <div className="mt-2 flex flex-wrap items-center gap-2">

              <GhostBadge state={a.ghost.state} reason={a.ghost.reason} prior={a.ghost.prior_prob} />

              <span className="text-sm">{a.ghost.reason}</span>

              <Link href="/methodology#ghost" className="text-sm underline">({a.ghost.def_version})</Link>

            </div>

          </li>

        ))}

      </ul>

    </div>

  );

}

