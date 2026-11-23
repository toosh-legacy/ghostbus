"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { API_BASE } from "@/lib/api";
import { ErrorBox, Loading } from "@/components/ui";

const VIEWS = ["feed-health", "pipeline", "models", "datasets"] as const;

export default function Admin() {
  const [token, setToken] = useState("dev-admin-token");
  const [view, setView] = useState<(typeof VIEWS)[number]>("feed-health");
  const q = useQuery({
    queryKey: ["admin", view, token],
    queryFn: async () => {
      const r = await fetch(`${API_BASE}/admin/v1/${view}`, { headers: { "X-Admin-Token": token } });
      if (!r.ok) throw new Error((await r.json()).title);
      return r.json();
    },
    refetchInterval: 10000,
  });
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Admin (internal)</h1>
      <label className="block">Admin token <input value={token} onChange={(e) => setToken(e.target.value)} type="password" className="border p-1" /></label>
      <div role="tablist" aria-label="Admin views" className="flex gap-2">
        {VIEWS.map((v) => (
          <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
            className={`rounded border px-3 py-1 ${view === v ? "bg-blue-800 text-white" : "bg-white"}`}>{v}</button>
        ))}
      </div>
      {q.isLoading && <Loading what={view} />}
      {q.error && <ErrorBox error={q.error} />}
      {q.data && <pre className="overflow-x-auto rounded bg-white p-3 text-xs">{JSON.stringify(q.data, null, 2)}</pre>}
    </div>
  );
}
