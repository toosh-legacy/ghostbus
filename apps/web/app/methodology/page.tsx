"use client";
import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Methodology() {
  const q = useQuery({ queryKey: ["meth"], queryFn: () => get<any>("/v1/methodology") });
  if (q.isLoading) return <Loading what="methodology" />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!.data;
  const a = d.backtests?.model_a;
  const b = d.backtests?.model_b;
  return (
    <article className="prose max-w-none space-y-6">
      <h1 className="text-2xl font-bold">Methodology</h1>
      <section aria-labelledby="metrics">
        <h2 id="metrics" className="text-xl font-semibold">Reliability metrics ({d.metric_def_version})</h2>
        <dl>
          {Object.entries(d.metric_definitions).map(([k, v]) => (
            <div key={k} id={k} className="mb-2"><dt className="font-semibold">{k.replaceAll("_", " ")}</dt><dd>{String(v)}</dd></div>
          ))}
        </dl>
        <p>Thresholds used: <code>{JSON.stringify(d.thresholds)}</code></p>
      </section>
      <section aria-labelledby="ghost">
        <h2 id="ghost" className="text-xl font-semibold">Ghost and missed-trip definitions ({d.ghost_def_version})</h2>
        <dl>
          {Object.entries(d.ghost_definitions).map(([k, v]) => (
            <div key={k} id={k} className="mb-2"><dt className="font-semibold">{k.replaceAll("_", " ")}</dt><dd>{String(v)}</dd></div>
          ))}
        </dl>
      </section>
      <section aria-labelledby="bt">
        <h2 id="bt" className="text-xl font-semibold">Latest backtests</h2>
        {a ? (
          <ul>
            <li>Arrival-delay model: median-prediction error {a.p50_mae.toFixed(0)} s vs best baseline ({a.best_baseline}) {a[`${a.best_baseline}_mae`].toFixed(0)} s ({a.improvement_vs_best_baseline_pct.toFixed(1)}% better).</li>
            <li id="coverage_of_intervals">80% range coverage on held-out weeks: {(a.coverage * 100).toFixed(1)}% (average width {Math.round(a.width_s / 60)} min).</li>
            <li>Missed-trip model ({b.chosen}): Brier {b.brier.toFixed(5)} vs rolling 28-day baseline {b.mean.rolling_28d_route_hour.brier.toFixed(5)}.</li>
          </ul>
        ) : <p>No backtest has been run yet.</p>}
      </section>
      <section aria-labelledby="src">
        <h2 id="src" className="text-xl font-semibold">Data sources and licenses</h2>
        <ul>{d.data_sources.map((s: any) => <li key={s.name}><a className="underline" href={s.url}>{s.name}</a> — {s.license}. {s.attribution}</li>)}</ul>
        <h3 className="font-semibold">Known data gaps</h3>
        <ul>{d.known_gaps.map((g: string) => <li key={g}>{g}</li>)}</ul>
        <p>Ground truth source: <strong>{d.ground_truth_source}</strong></p>
      </section>
    </article>
  );
}
