import type { FeedHealth } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const GHOST_STYLE: Record<string, { cls: string; icon: string; label: string }> = {
  low: { cls: "bg-green-100 text-green-900 border-green-700", icon: "●", label: "Low ghost risk" },
  elevated: { cls: "bg-amber-100 text-amber-950 border-amber-700", icon: "▲", label: "Elevated ghost risk" },
  high: { cls: "bg-red-100 text-red-900 border-red-700", icon: "✖", label: "High ghost risk" },
  unknown: { cls: "bg-slate-200 text-slate-900 border-slate-600", icon: "?", label: "Ghost risk unknown" },
};

// Color is never the only signal: every badge has an icon, a text label and a reason.
export function GhostBadge({ state, reason, prior }: { state: string; reason?: string; prior?: number | null }) {
  const s = GHOST_STYLE[state] ?? GHOST_STYLE.unknown;
  return (
    <Badge variant="outline" className={cn("h-auto rounded border px-2 py-0.5 text-sm", s.cls)} title={reason}>
      <span aria-hidden="true">{s.icon}</span>
      <span>{s.label}</span>
      {prior !== undefined && prior !== null && <span className="opacity-80">· prior {(prior * 100).toFixed(1)}%</span>}
    </Badge>
  );
}

export function StaleBanner({ health }: { health?: FeedHealth }) {
  if (!health || health.healthy) return null;
  return (
    <div role="alert" className="mb-3 rounded border border-amber-700 bg-amber-50 p-3 text-amber-950">
      <strong>Live data is stale.</strong> {health.reason}. Showing schedule plus historical ranges
      {health.last_fetch_utc ? ` (last update ${new Date(health.last_fetch_utc).toLocaleTimeString()})` : ""}.
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <p aria-live="polite">Loading {what}…</p>;
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <p role="alert" className="text-red-800">
      Could not load: {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
