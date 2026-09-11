"use client";

/**
 * The entity graph, drawn with the Neo4j Visualization Library.
 *
 * Presentational on purpose: it receives nodes and edges and reports
 * double-clicks. `MemoryRail` owns the accumulated graph and decides what to ask
 * the service for, which is what keeps `expandGraph`'s `loadedIds` correct.
 *
 * NVL touches `window` at import time, so it is loaded with `next/dynamic` and
 * `ssr: false`.
 */

import { Box, Spinner, Stack, Text } from "@chakra-ui/react";
import dynamic from "next/dynamic";
import { useMemo, useRef } from "react";

import type { GraphEdgePayload, GraphNodePayload } from "@/lib/types";

const InteractiveNvlWrapper = dynamic(
  () => import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <Stack h="full" align="center" justify="center" gap={3}>
        <Spinner />
        <Text fontSize="xs" color="fg.muted">
          Loading graph…
        </Text>
      </Stack>
    ),
  },
);

/** POLE+O-ish palette. Unknown types fall back to grey rather than vanishing. */
const COLORS: Record<string, string> = {
  PERSON: "#6366F1",
  ORGANIZATION: "#0EA5E9",
  LOCATION: "#10B981",
  EVENT: "#F59E0B",
  OBJECT: "#A855F7",
};

const DOUBLE_CLICK_MS = 350;

export function GraphView({
  nodes,
  edges,
  expandingId,
  onExpand,
  onSelect,
}: {
  nodes: GraphNodePayload[];
  edges: GraphEdgePayload[];
  expandingId: string | null;
  onExpand: (nodeId: string) => void;
  onSelect: (node: GraphNodePayload) => void;
}) {
  const lastClick = useRef<{ id: string; at: number } | null>(null);

  const nvlNodes = useMemo(
    () =>
      nodes.map((node) => ({
        id: node.id,
        caption: node.id === expandingId ? `${node.name} …` : node.name,
        size: 16,
        color: COLORS[node.type.toUpperCase().split(":")[0] ?? ""] ?? "#94A3B8",
      })),
    [nodes, expandingId],
  );

  const nvlEdges = useMemo(
    () =>
      edges
        .filter((edge) => edge.source && edge.target)
        .map((edge) => ({
          id: edge.id,
          from: edge.source,
          to: edge.target,
          caption: edge.type,
        })),
    [edges],
  );

  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const mouseEventCallbacks = useMemo(
    () => ({
      // NVL reports clicks, not double-clicks, so the second click inside
      // DOUBLE_CLICK_MS is treated as "expand this node".
      onNodeClick: (node: { id: string }) => {
        const now = Date.now();
        const previous = lastClick.current;
        if (previous && previous.id === node.id && now - previous.at < DOUBLE_CLICK_MS) {
          lastClick.current = null;
          onExpand(node.id);
          return;
        }
        lastClick.current = { id: node.id, at: now };
        const selected = byId.get(node.id);
        if (selected) onSelect(selected);
      },
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [byId, onExpand, onSelect],
  );

  if (nodes.length === 0) {
    return (
      <Stack h="full" align="center" justify="center" gap={2} px={6} textAlign="center">
        <Text fontSize="sm" color="fg.muted">
          No entities yet.
        </Text>
        <Text fontSize="xs" color="fg.subtle">
          Mention a place, a person or an organisation and NAMS will extract it in the background.
        </Text>
      </Stack>
    );
  }

  return (
    <Box h="full" w="full" position="relative">
      <InteractiveNvlWrapper
        nodes={nvlNodes}
        rels={nvlEdges}
        mouseEventCallbacks={mouseEventCallbacks}
        nvlOptions={{ layout: "d3Force", initialZoom: 1, minZoom: 0.2, maxZoom: 3 }}
      />
    </Box>
  );
}
