import assert from "node:assert/strict";
import test from "node:test";

import type { GraphData } from "./api.ts";
import { FALLBACK_COLOR, TYPE_COLORS, toVizGraph } from "./graph.ts";

const sample: GraphData = {
  nodes: [
    { id: "m1", label: "hello", type: "Message" },
    { id: "e1", label: "Nike", type: "Entity" },
    { id: "z1", label: "mystery", type: "SomethingNew" },
  ],
  edges: [
    { id: "r1", source: "m1", target: "e1", type: "MENTIONS" },
    { id: "r2", source: "e1", target: "missing", type: "RELATED_TO" },
  ],
};

test("nodes keep their label and get a colour per memory layer", () => {
  const { nodes } = toVizGraph(sample);
  assert.equal(nodes.length, 3);
  assert.equal(nodes[0].color, TYPE_COLORS.Message);
  assert.equal(nodes[1].color, TYPE_COLORS.Entity);
  assert.equal(nodes[2].color, FALLBACK_COLOR);
  assert.equal(nodes[1].label, "Nike");
});

test("edges pointing outside the returned node set are dropped", () => {
  const { links } = toVizGraph(sample);
  assert.deepEqual(links, [
    { source: "m1", target: "e1", type: "MENTIONS" },
  ]);
});

test("an empty payload yields an empty graph", () => {
  assert.deepEqual(toVizGraph({ nodes: [], edges: [] }), {
    nodes: [],
    links: [],
  });
});
