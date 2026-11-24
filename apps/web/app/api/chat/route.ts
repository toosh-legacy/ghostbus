import { createUIMessageStream, createUIMessageStreamResponse, type UIMessage } from "ai";
import { parseSse, type CopilotAnswer } from "@/lib/copilot-stream";

const API = process.env.GHOSTBUS_API ?? process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

function lastUserText(messages: UIMessage[]): string {
  const m = [...messages].reverse().find((x) => x.role === "user");
  return (m?.parts ?? []).map((p) => (p.type === "text" ? p.text : "")).join("").trim();
}

// The browser talks to this route with the AI SDK chat protocol; the route starts a copilot job on the GhostBus API
// (the model call runs in the API's background worker) and relays its SSE progress and answer.
export async function POST(req: Request) {
  const { messages, id } = (await req.json()) as { messages: UIMessage[]; id?: string };
  const question = lastUserText(messages).slice(0, 1000);
  const stream = createUIMessageStream({
    execute: async ({ writer }) => {
      const r = await fetch(`${API}/v1/copilot/chat`, {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ question, thread_id: id }),
      });
      if (!r.ok || !r.body) throw new Error(`copilot API returned ${r.status}`);
      for await (const ev of parseSse(r.body)) {
        const data = JSON.parse(ev.data);
        if (ev.event === "progress") {
          writer.write({ type: "data-progress", data: { node: data.node }, transient: true });
        } else if (ev.event === "answer") {
          const a = data as CopilotAnswer;
          writer.write({ type: "text-start", id: "answer" });
          writer.write({ type: "text-delta", id: "answer", delta: a.answer });
          writer.write({ type: "text-end", id: "answer" });
          writer.write({ type: "data-answer", data: a });
        } else if (ev.event === "error") {
          throw new Error(data.detail ?? "copilot failed");
        }
      }
    },
    onError: (e) => (e instanceof Error ? e.message : "copilot failed"),
  });
  return createUIMessageStreamResponse({ stream });
}
