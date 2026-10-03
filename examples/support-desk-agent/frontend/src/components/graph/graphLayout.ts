/**
 * Node positions for the graph panel, computed with d3-force.
 *
 * NVL's own `d3Force` layout takes no options, and with its short, equally
 * strong links a message that mentions eight entities pulls them all into one
 * knot (a "hairball"). This layout tunes the springs per edge kind instead:
 *
 * - typed `RELATED_TO` and `SAME_AS` edges are short and stiff, so facts that
 *   belong together sit together;
 * - `MENTIONS` and the conversation's own structure are long and loose, so a
 *   busy message fans out around its entities instead of collapsing them;
 * - every node repels the others and keeps a margin for its caption and the
 *   labels on its edges.
 *
 * The simulation runs to rest synchronously and is deterministic: d3 seeds
 * unplaced nodes on a phyllotaxis spiral, so the same graph lays out the
 * same way every time. NVL then renders it with its `free` layout.
 */

import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";
import type { GraphNode, GraphRelationship } from "@/lib/types";
import { edgeKind, nodeSize, type EdgeKind } from "./graphStyle";

export interface NodePosition {
  id: string;
  x: number;
  y: number;
}

interface LayoutNode extends SimulationNodeDatum {
  id: string;
  radius: number;
  message: boolean;
}

/** Spring length (graph units, ~px at zoom 1) and stiffness per edge kind. */
const LINKS: Record<EdgeKind, { distance: number; strength: number }> = {
  related: { distance: 190, strength: 0.35 },
  same_as: { distance: 170, strength: 0.3 },
  mentions: { distance: 200, strength: 0.18 },
  structure: { distance: 230, strength: 0.06 },
};

/** Repulsion; messages push less, so they settle between their entities. */
const CHARGE = { entity: -2200, message: -700 };
/** Room around a node for its caption and its edges' labels. */
const COLLIDE_PADDING = 52;
/** A weak pull to the centre keeps disconnected pieces from drifting apart. */
const GRAVITY = 0.05;
/**
 * Repulsion stops at this distance, so separate pieces of the graph (the
 * "All seeded" view) do not push each other to the edges of the canvas.
 */
const CHARGE_RANGE = 800;
/** Enough ticks for the default alpha decay to bring the simulation to rest. */
const TICKS = 360;

/**
 * Lay out `nodes`. Nodes listed in `pinned` keep those positions (an expanded
 * graph keeps its original nodes where they were); the rest are placed around
 * them, starting next to a pinned neighbour when they have one.
 */
export function layoutGraph(
  nodes: GraphNode[],
  relationships: GraphRelationship[],
  pinned?: ReadonlyMap<string, NodePosition>,
): NodePosition[] {
  const byId = new Map<string, LayoutNode>();
  for (const node of nodes) {
    const anchor = pinned?.get(node.id);
    byId.set(node.id, {
      id: node.id,
      radius: nodeSize(node) / 2,
      message: node.kind === "message",
      ...(anchor ? { x: anchor.x, y: anchor.y, fx: anchor.x, fy: anchor.y } : {}),
    });
  }

  const links: (SimulationLinkDatum<LayoutNode> & { kind: EdgeKind })[] = [];
  for (const rel of relationships) {
    if (rel.from === rel.to || !byId.has(rel.from) || !byId.has(rel.to)) continue;
    links.push({ source: rel.from, target: rel.to, kind: edgeKind(rel) });
  }

  // A new node starts beside a pinned neighbour rather than on the spiral,
  // so an expansion grows out of the node that was expanded.
  if (pinned && pinned.size > 0) {
    let offset = 0;
    for (const link of links) {
      const [a, b] = [byId.get(String(link.source)), byId.get(String(link.target))];
      if (!a || !b) continue;
      const [fixed, free] = a.fx !== undefined ? [a, b] : [b, a];
      if (fixed.fx === undefined || free.fx !== undefined || free.x !== undefined) continue;
      const angle = (offset += 2.399963); // the golden angle
      free.x = (fixed.x ?? 0) + Math.cos(angle) * LINKS[link.kind].distance;
      free.y = (fixed.y ?? 0) + Math.sin(angle) * LINKS[link.kind].distance;
    }
  }

  const layoutNodes = [...byId.values()];
  const simulation = forceSimulation(layoutNodes)
    .force(
      "link",
      forceLink<LayoutNode, (typeof links)[number]>(links)
        .id((node) => node.id)
        .distance((link) => LINKS[link.kind].distance)
        .strength((link) => LINKS[link.kind].strength),
    )
    .force(
      "charge",
      forceManyBody<LayoutNode>()
        .strength((node) => (node.message ? CHARGE.message : CHARGE.entity))
        .distanceMax(CHARGE_RANGE),
    )
    .force(
      "collide",
      forceCollide<LayoutNode>()
        .radius((node) => node.radius + COLLIDE_PADDING)
        .strength(0.9)
        .iterations(2),
    )
    .force("x", forceX<LayoutNode>(0).strength(GRAVITY))
    .force("y", forceY<LayoutNode>(0).strength(GRAVITY))
    .stop();
  simulation.tick(TICKS);

  return layoutNodes.map((node) => ({
    id: node.id,
    x: node.x ?? 0,
    y: node.y ?? 0,
  }));
}
