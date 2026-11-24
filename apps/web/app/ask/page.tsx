"use client";

import Link from "next/link";
import { useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { NODE_LABELS, type CopilotAnswer } from "@/lib/copilot-stream";

type Msg = UIMessage<unknown, { progress: { node: string }; answer: CopilotAnswer }>;

const STARTERS = [
  "Which 3 pilot routes had the highest missed-trip rate on weekday PM peaks last month?",
  "How did bunching on route 66 change by month?",
  "Compare on-time performance for route 1 and SL3 in November 2025.",
  "How is excess wait time measured?",
];

function fmt(v: unknown): string {
  return typeof v === "number" ? String(+v.toFixed(4)) : String(v);
}

function AnswerDetails({ a }: { a: CopilotAnswer }) {
  return (
    <div className="space-y-3 text-sm">
      {a.via && <Badge variant="outline">{a.via.startsWith("mcp:") ? `MCP tool ${a.via.slice(4)}` : a.via === "rag" ? "methodology search" : "validated SQL"}</Badge>}
      {a.refused && a.suggestions.length > 0 && (
        <p>Try: {a.suggestions.slice(0, 3).join(" · ")}</p>
      )}
      {a.table && (
        <Table>
          <TableHeader><TableRow>{a.table.columns.map((c) => <TableHead key={c} scope="col">{c}</TableHead>)}</TableRow></TableHeader>
          <TableBody>{a.table.rows.slice(0, 50).map((r, i) => (
            <TableRow key={i}>{r.map((v, j) => <TableCell key={j}>{fmt(v)}</TableCell>)}</TableRow>))}</TableBody>
        </Table>
      )}
      {a.citations.length > 0 && (
        <ul className="list-disc pl-5">{a.citations.map((c) => <li key={c.id}><span className="font-mono">[{c.id}]</span> {c.text}</li>)}</ul>
      )}
      {a.date_range && <p>Data range: {a.date_range.start_month} to {a.date_range.end_month}</p>}
      {a.caveat && <p className="text-amber-900">{a.caveat}</p>}
      {a.definitions.map((d) => <p key={d.name}><Link className="underline" href={`/methodology#${d.name}`}>Definition: {d.name}</Link> ({d.version})</p>)}
      {a.sql && <details><summary className="cursor-pointer">Show the exact query</summary><pre className="overflow-x-auto rounded bg-slate-100 p-2 text-xs">{a.sql}</pre></details>}
    </div>
  );
}

export default function Ask() {
  const [input, setInput] = useState("");
  const [progress, setProgress] = useState<string[]>([]);
  const { messages, sendMessage, status, error } = useChat<Msg>({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
    onData: (part) => { if (part.type === "data-progress") setProgress((p) => [...p, part.data.node]); },
    onFinish: () => setProgress([]),
  });
  const busy = status === "submitted" || status === "streaming";
  const ask = (q: string) => { if (!q.trim() || busy) return; setProgress([]); sendMessage({ text: q }); setInput(""); };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Ask about reliability</h1>
      <p className="text-slate-700">Answers come only from the published reliability views and methodology. Follow-up questions keep the conversation.</p>
      <div aria-live="polite" className="space-y-3">
        {messages.map((m) => {
          const text = m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
          const ans = m.parts.find((p) => p.type === "data-answer");
          return (
            <Card key={m.id} className={m.role === "user" ? "bg-slate-50" : ""}>
              <CardContent className="space-y-2 pt-4">
                <p className={m.role === "user" ? "font-medium" : "text-lg"}>{text}</p>
                {ans && ans.type === "data-answer" && <AnswerDetails a={ans.data} />}
              </CardContent>
            </Card>
          );
        })}
        {busy && (
          <p className="text-sm text-slate-700">{progress.length ? NODE_LABELS[progress.at(-1)!] ?? progress.at(-1) : "Starting…"}</p>
        )}
        {error && <p role="alert" className="text-red-800">Could not answer: {error.message}</p>}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(input); }} className="flex gap-2">
        <label htmlFor="question" className="sr-only">Your question</label>
        <Input id="question" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask a reliability question" />
        <Button type="submit" disabled={busy}>{busy ? "Thinking…" : "Ask"}</Button>
      </form>
      {messages.length === 0 && (
        <ul className="flex flex-wrap gap-2">{STARTERS.map((s) => (
          <li key={s}><Button variant="outline" size="sm" onClick={() => ask(s)}>{s}</Button></li>))}</ul>
      )}
    </div>
  );
}
