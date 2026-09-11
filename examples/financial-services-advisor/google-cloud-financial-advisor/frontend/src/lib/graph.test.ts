import { describe, expect, it } from "vitest";
import type { GraphPayload } from "./api";
import { categorizeNode, countCategories, filterGraph, nodeColor } from "./graph";

const payload: GraphPayload = {
  nodes: [
    { id: "CUST-003", label: "Global Holdings Ltd", labels: ["Customer"], properties: {} },
    { id: "TXN-1", label: "TXN-1", labels: ["Transaction"], properties: {} },
    { id: "conv-1", label: "session-1", labels: ["Conversation"], properties: {} },
    { id: "msg-1", label: "user", labels: ["Message"], properties: {} },
    {
      id: "ent-1",
      label: "Shell Corp Cayman",
      labels: ["Entity", "Organization", "Company"],
      properties: {},
    },
    { id: "trace-1", label: "trace", labels: ["ReasoningTrace"], properties: {} },
  ],
  relationships: [
    { id: "r1", from: "conv-1", to: "msg-1", type: "HAS_MESSAGE" },
    { id: "r2", from: "msg-1", to: "ent-1", type: "MENTIONS" },
    { id: "r3", from: "CUST-003", to: "TXN-1", type: "SENT" },
    { id: "r4", from: "msg-1", to: "missing-node", type: "MENTIONS" },
  ],
};

describe("categorizeNode", () => {
  it("classifies each memory layer and the domain", () => {
    expect(categorizeNode(["Customer"])).toBe("domain");
    expect(categorizeNode(["Conversation"])).toBe("shortTerm");
    expect(categorizeNode(["Preference"])).toBe("longTerm");
    expect(categorizeNode(["ReasoningStep"])).toBe("reasoning");
  });

  it("prefers the memory label when an entity label collides with a domain one", () => {
    // Library entity nodes carry :Entity plus PascalCase type labels, and
    // :Organization is also a domain label in this app.
    expect(categorizeNode(["Entity", "Organization"])).toBe("longTerm");
    expect(categorizeNode(["Organization"])).toBe("domain");
  });

  it("treats unknown labels as domain data", () => {
    expect(categorizeNode([])).toBe("domain");
    expect(categorizeNode(["Widget"])).toBe("domain");
  });
});

describe("countCategories", () => {
  it("counts nodes per layer", () => {
    expect(countCategories(payload.nodes)).toEqual({
      domain: 2,
      shortTerm: 2,
      longTerm: 1,
      reasoning: 1,
    });
  });
});

describe("filterGraph", () => {
  it("keeps only enabled layers and drops dangling relationships", () => {
    const result = filterGraph(payload, {
      domain: false,
      shortTerm: true,
      longTerm: true,
      reasoning: false,
    });
    expect(result.nodes.map((n) => n.id)).toEqual(["conv-1", "msg-1", "ent-1"]);
    // r3 is domain-only, r4 points at a node that is not in the payload.
    expect(result.relationships.map((r) => r.id)).toEqual(["r1", "r2"]);
  });

  it("returns an empty graph when everything is filtered out", () => {
    const result = filterGraph(payload, {
      domain: false,
      shortTerm: false,
      longTerm: false,
      reasoning: false,
    });
    expect(result).toEqual({ nodes: [], relationships: [] });
  });
});

describe("nodeColor", () => {
  it("uses the first known label and falls back to grey", () => {
    expect(nodeColor(["Unknown", "Customer"])).toBe("#68BDF6");
    expect(nodeColor(["Unknown"])).toBe("#95A5A6");
  });
});
