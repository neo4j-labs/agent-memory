/**
 * Tests for the SSE wire contract.
 *
 * Run with `npm test` (node:test plus Node's TypeScript type stripping — no
 * test framework dependency). The parser is the part of this client most
 * likely to break silently: a dropped `event:` name used to make every tool
 * card spin forever.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { parseToolArguments, streamChat, type ChatStreamEvent } from "./api.ts";

/** Build a response body shaped exactly like sse-starlette's output. */
function sseStream(frames: Array<[string, unknown]>): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  const text =
    frames
      .map(([event, data]) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`)
      .join("") + ": ping\r\n\r\n";
  // Split mid-frame so the parser has to survive chunk boundaries.
  const mid = Math.floor(text.length / 3);
  const chunks = [text.slice(0, mid), text.slice(mid)];
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

function stubFetch(body: ReadableStream<Uint8Array>): void {
  globalThis.fetch = (async () =>
    new Response(body, { status: 200 })) as typeof fetch;
}

async function collect(): Promise<ChatStreamEvent[]> {
  const events: ChatStreamEvent[] = [];
  for await (const event of streamChat("hi", "session-1", "ada")) {
    events.push(event);
  }
  return events;
}

test("streamChat keeps the event name attached to its payload", async () => {
  stubFetch(
    sseStream([
      ["token", { content: "Hel" }],
      ["token", { content: "lo" }],
      [
        "tool_call",
        { call_id: "c1", name: "search_products", arguments: '{"query":"shoes"}' },
      ],
      ["tool_result", { call_id: "c1", name: "search_products", result: "2 hits" }],
      ["done", { session_id: "session-1" }],
    ])
  );

  const events = await collect();

  assert.deepEqual(
    events.map((e) => e.event),
    ["token", "token", "tool_call", "tool_result", "done"]
  );
  assert.equal(
    events
      .filter((e) => e.event === "token")
      .map((e) => e.data.content)
      .join(""),
    "Hello"
  );
});

test("a tool result can be matched back to its tool call", async () => {
  stubFetch(
    sseStream([
      ["tool_call", { call_id: "c1", name: "check_inventory", arguments: "{}" }],
      ["tool_result", { call_id: "c1", name: "check_inventory", result: "in stock" }],
    ])
  );

  const events = await collect();
  const call = events.find((e) => e.event === "tool_call");
  const result = events.find((e) => e.event === "tool_result");
  assert.ok(call && result);
  assert.equal(call.data.call_id, result.data.call_id);
  assert.equal(call.data.name, result.data.name);
});

test("unknown and malformed frames are skipped, not thrown", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          "event: heartbeat\r\ndata: {}\r\n\r\n" +
            "event: token\r\ndata: not json\r\n\r\n" +
            "event: token\r\ndata: {\"content\":\"ok\"}\r\n\r\n"
        )
      );
      controller.close();
    },
  });
  stubFetch(body);

  const events = await collect();
  assert.deepEqual(events, [{ event: "token", data: { content: "ok" } }]);
});

test("parseToolArguments handles JSON strings, objects and junk", () => {
  assert.deepEqual(parseToolArguments('{"query":"shoes"}'), { query: "shoes" });
  assert.deepEqual(parseToolArguments({ query: "shoes" }), { query: "shoes" });
  assert.deepEqual(parseToolArguments(undefined), {});
  assert.deepEqual(parseToolArguments("oops"), { value: "oops" });
});
