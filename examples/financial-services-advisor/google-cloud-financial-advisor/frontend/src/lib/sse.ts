/**
 * Minimal Server-Sent Events parser for `fetch` + ReadableStream streaming.
 *
 * The backend streams agent events over a POST endpoint, so `EventSource`
 * (GET only) cannot be used. A hand-rolled parser has to survive three things
 * the network does to an SSE stream:
 *
 * 1. A chunk can end in the middle of a *line* — so the trailing partial line
 *    is carried over in `state.buffer`.
 * 2. A chunk can end in the middle of an *event* (e.g. after `event: tool_call`
 *    but before its `data:` line) — so the in-progress event name and data
 *    lines live in the parser state, not in a per-chunk local.
 * 3. Lines may be terminated with `\r\n`, and a single event may carry several
 *    `data:` lines that have to be joined with a newline (SSE spec).
 *
 * `: keep-alive` comment lines and unknown fields (`id:`, `retry:`) are ignored.
 */

export interface SseFrame {
  /** The `event:` name, or `"message"` when the stream omits it. */
  event: string;
  /** The joined `data:` payload (may be an empty string). */
  data: string;
}

export interface SseParserState {
  buffer: string;
  event: string | null;
  data: string[];
}

export function createSseParserState(): SseParserState {
  return { buffer: "", event: null, data: [] };
}

function stripFieldValue(line: string, field: string): string {
  const value = line.slice(field.length + 1);
  return value.startsWith(" ") ? value.slice(1) : value;
}

function takePending(state: SseParserState): SseFrame | null {
  if (state.event === null && state.data.length === 0) return null;
  const frame: SseFrame = {
    event: state.event ?? "message",
    data: state.data.join("\n"),
  };
  state.event = null;
  state.data = [];
  return frame;
}

function consumeLine(state: SseParserState, rawLine: string): SseFrame | null {
  // Tolerate CRLF line endings.
  const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;

  if (line === "") return takePending(state);
  if (line.startsWith(":")) return null; // comment / keep-alive
  if (line.startsWith("event:")) {
    state.event = stripFieldValue(line, "event").trim();
    return null;
  }
  if (line.startsWith("data:")) {
    state.data.push(stripFieldValue(line, "data"));
    return null;
  }
  return null; // id:, retry:, or anything else we do not need
}

/**
 * Feed a decoded chunk into the parser and return every event it completed.
 */
export function pushSseChunk(
  state: SseParserState,
  chunk: string,
): SseFrame[] {
  state.buffer += chunk;
  const frames: SseFrame[] = [];
  const lines = state.buffer.split("\n");
  // The final element is an incomplete line (or "" when the chunk ended on a
  // newline) — keep it for the next chunk.
  state.buffer = lines.pop() ?? "";
  for (const line of lines) {
    const frame = consumeLine(state, line);
    if (frame) frames.push(frame);
  }
  return frames;
}

/**
 * Flush the parser at end of stream: the last event may not be followed by the
 * blank line that normally terminates it.
 */
export function flushSseParser(state: SseParserState): SseFrame[] {
  const frames: SseFrame[] = [];
  if (state.buffer !== "") {
    const frame = consumeLine(state, state.buffer);
    state.buffer = "";
    if (frame) frames.push(frame);
  }
  const pending = takePending(state);
  if (pending) frames.push(pending);
  return frames;
}
