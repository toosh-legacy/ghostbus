import { describe, expect, it } from "vitest";
import { NODE_LABELS, parseSse } from "@/lib/copilot-stream";

function body(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({ start(c) { chunks.forEach((s) => c.enqueue(enc.encode(s))); c.close(); } });
}

describe("parseSse", () => {
  it("parses events split across chunks and CRLF line endings", async () => {
    const out = [];
    for await (const e of parseSse(body(["event: progress\r\ndata: {\"node\":", "\"classify\"}\r\n\r\nevent: ans", "wer\ndata: {\"answer\":\"ok\"}\n\n"]))) out.push(e);
    expect(out).toEqual([{ event: "progress", data: "{\"node\":\"classify\"}" }, { event: "answer", data: "{\"answer\":\"ok\"}" }]);
  });
  it("ignores keepalive comments and joins multi-line data", async () => {
    const out = [];
    for await (const e of parseSse(body([": ping\n\n", "data: a\ndata: b\n\n"]))) out.push(e);
    expect(out).toEqual([{ event: "message", data: "a\nb" }]);
  });
  it("has a label for every copilot graph node", () => {
    for (const n of ["contextualize", "classify", "plan", "call_tool", "generate_sql", "validate", "execute", "check_result",
      "answer", "retrieve", "answer_methodology", "refuse"]) expect(NODE_LABELS[n]).toBeTruthy();
  });
});
