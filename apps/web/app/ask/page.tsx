"use client";
import Link from "next/link";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { API_BASE } from "@/lib/api";
import { ErrorBox } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function Ask() {
  const [q, setQ] = useState("Which 3 pilot routes had the highest missed-trip rate on weekday PM peaks last month?");
  const m = useMutation({
    mutationFn: async (question: string) => {
      const r = await fetch(`${API_BASE}/v1/copilot/ask`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }) });
      if (!r.ok) throw new Error((await r.json()).title);
      return (await r.json()).data;
    },
  });
  const a: any = m.data;
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Ask about reliability</h1>
      <form onSubmit={(e) => { e.preventDefault(); m.mutate(q); }} className="space-y-2">
        <label htmlFor="question" className="block font-medium">Your question</label>
        <textarea id="question" value={q} onChange={(e) => setQ(e.target.value)} rows={3} className="w-full rounded border border-slate-500 p-2" />
        <button className="rounded bg-blue-800 px-4 py-2 text-white" disabled={m.isPending}>{m.isPending ? "Thinking…" : "Ask"}</button>
      </form>
      {m.error && <ErrorBox error={m.error} />}
      {a && (
        <section aria-live="polite" className="rounded border bg-white p-3 space-y-3">
          <p className="text-lg">{a.answer}</p>
          {a.refused && <ul className="list-disc pl-5">{a.suggestions.map((s: string) => (
            <li key={s}><button className="underline text-left" onClick={() => { setQ(s); m.mutate(s); }}>{s}</button></li>))}</ul>}
          {a.table && (
            <table className="w-full text-sm text-left">
              <thead><tr>{a.table.columns.map((c: string) => <th key={c} scope="col">{c}</th>)}</tr></thead>
              <tbody>{a.table.rows.map((r: any[], i: number) => (
                <tr key={i} className="border-t">{r.map((v, j) => <td key={j}>{typeof v === "number" ? +v.toFixed(4) : String(v)}</td>)}</tr>))}</tbody>
            </table>
          )}
          {a.date_range && <p className="text-sm">Data range: {a.date_range.start_month} to {a.date_range.end_month}</p>}
          {a.caveat && <p className="text-sm text-amber-900">{a.caveat}</p>}
          {a.definitions?.map((d: any) => <p key={d.name} className="text-sm"><Link className="underline" href={`/methodology#${d.name}`}>Definition: {d.name}</Link> ({d.version})</p>)}
          {a.sql && <details><summary className="cursor-pointer">Show the exact query</summary><pre className="overflow-x-auto bg-slate-100 p-2 text-xs">{a.sql}</pre></details>}
        </section>
      )}
    </div>
  );
}
