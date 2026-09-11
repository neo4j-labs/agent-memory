import { describe, expect, it } from "vitest";
import {
  createSseParserState,
  flushSseParser,
  pushSseChunk,
  type SseFrame,
} from "./sse";

/** Feed an arbitrary sequence of chunks through the parser. */
function parseAll(chunks: string[]): SseFrame[] {
  const state = createSseParserState();
  const frames: SseFrame[] = [];
  for (const chunk of chunks) frames.push(...pushSseChunk(state, chunk));
  frames.push(...flushSseParser(state));
  return frames;
}

describe("SSE parser", () => {
  it("parses well-formed events delivered in one chunk", () => {
    const frames = parseAll([
      'event: agent_start\ndata: {"agent":"supervisor"}\n\n' +
        'event: done\ndata: {"session_id":"s-1"}\n\n',
    ]);
    expect(frames).toEqual([
      { event: "agent_start", data: '{"agent":"supervisor"}' },
      { event: "done", data: '{"session_id":"s-1"}' },
    ]);
  });

  it("keeps the event name when a chunk boundary splits an event", () => {
    // This is the regression: the `event:` line arrives in one network chunk
    // and its `data:` line in the next.
    const frames = parseAll([
      "event: tool_call\n",
      'data: {"tool":"scan_transactions"}\n\n',
    ]);
    expect(frames).toEqual([
      { event: "tool_call", data: '{"tool":"scan_transactions"}' },
    ]);
  });

  it("reassembles a payload split mid-line", () => {
    const frames = parseAll([
      'event: tool_result\ndata: {"tool":"scan_trans',
      'actions","result":"4 deposits of $9,500"}\n\n',
    ]);
    expect(frames).toEqual([
      {
        event: "tool_result",
        data: '{"tool":"scan_transactions","result":"4 deposits of $9,500"}',
      },
    ]);
  });

  it("survives byte-at-a-time delivery", () => {
    const stream =
      'event: thinking\ndata: {"thought":"checking sanctions"}\n\n' +
      'event: response\ndata: {"content":"done"}\n\n';
    const frames = parseAll([...stream]);
    expect(frames.map((f) => f.event)).toEqual(["thinking", "response"]);
  });

  it("joins multi-line data fields and tolerates CRLF plus comments", () => {
    const frames = parseAll([
      ": keep-alive\r\nevent: response\r\ndata: line one\r\ndata: line two\r\n\r\n",
    ]);
    expect(frames).toEqual([
      { event: "response", data: "line one\nline two" },
    ]);
  });

  it("emits a trailing event that was never terminated by a blank line", () => {
    const frames = parseAll(['event: done\ndata: {"session_id":"s-2"}']);
    expect(frames).toEqual([{ event: "done", data: '{"session_id":"s-2"}' }]);
  });

  it("ignores id: and retry: fields", () => {
    const frames = parseAll(["id: 7\nretry: 1000\nevent: error\ndata: oops\n\n"]);
    expect(frames).toEqual([{ event: "error", data: "oops" }]);
  });
});
