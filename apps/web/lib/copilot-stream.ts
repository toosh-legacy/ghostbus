// Converts the GhostBus copilot SSE stream (FastAPI /v1/copilot/chat) into Vercel AI SDK UI message chunks.
// Progress events become transient `data-progress` parts; the final answer becomes a text part plus a
// `data-answer` part carrying the table, SQL, date range, definitions and citations.

export type CopilotAnswer = {
  answer: string; refused: boolean; via?: string | null;
  table: { columns: string[]; rows: unknown[][] } | null; sql: string | null;
  date_range: { start_month: string; end_month: string } | null;
  definitions: { name: string; version: string; link: string }[];
  citations: { id: string; text: string }[];
  caveat: string | null; suggestions: string[]; thread_id: string; mode: string;
};

export type SseEvent = { event: string; data: string };

/** Parse a text/event-stream body into events (handles \r\n and multi-line data). */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: data.join("\n") };
    }
  }
}

export const NODE_LABELS: Record<string, string> = {
  contextualize: "Reading the conversation", classify: "Understanding the question", plan: "Choosing a tool or query",
  call_tool: "Calling the reliability tool (MCP)", generate_sql: "Writing SQL", validate: "Checking the SQL is safe",
  execute: "Running the read-only query", check_result: "Checking the result", answer: "Writing the answer",
  retrieve: "Searching the methodology", answer_methodology: "Writing a cited answer", refuse: "Explaining the limits",
};
