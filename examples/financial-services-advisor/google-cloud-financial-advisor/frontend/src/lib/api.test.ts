import { describe, expect, it } from "vitest";
import { toAgentEvent } from "./api";

describe("toAgentEvent", () => {
  it("merges the SSE event name with its JSON payload", () => {
    const event = toAgentEvent({
      event: "tool_call",
      data: '{"agent":"aml_agent","tool":"scan_transactions","args":{},"timestamp":1}',
    });
    expect(event).toEqual({
      type: "tool_call",
      agent: "aml_agent",
      tool: "scan_transactions",
      args: {},
      timestamp: 1,
    });
  });

  it("skips frames whose event name we do not model", () => {
    expect(toAgentEvent({ event: "heartbeat", data: "{}" })).toBeNull();
  });

  it("skips malformed payloads instead of throwing", () => {
    expect(toAgentEvent({ event: "done", data: "not json" })).toBeNull();
    expect(toAgentEvent({ event: "done", data: "42" })).toBeNull();
    expect(toAgentEvent({ event: "done", data: "null" })).toBeNull();
  });
});
