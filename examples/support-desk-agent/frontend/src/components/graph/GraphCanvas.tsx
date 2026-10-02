"use client";

import { Box, Spinner, Text, VStack } from "@chakra-ui/react";
import type {
  ExternalCallbacks,
  NVL,
  Node as NvlNode,
  Relationship as NvlRelationship,
} from "@neo4j-nvl/base";
import type { MouseEventCallbacks } from "@neo4j-nvl/react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { GraphData, GraphNode, GraphRelationship } from "@/lib/types";
import { layoutGraph, type NodePosition } from "./graphLayout";
import {
  EDGE_STYLE,
  RELATIONSHIP_CAPTION_SIZE,
  edgeCaption,
  edgeKind,
  nodeCaption,
  nodeCaptionSize,
  nodeColor,
  nodeSize,
} from "./graphStyle";

/**
 * NVL touches `window` on import, so it is loaded client-only. `ssr: false`
 * is allowed here because this module is itself a client component.
 */
const InteractiveNvlWrapper = dynamic(
  () => import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <VStack h="full" justifyContent="center" gap="3">
        <Spinner size="lg" />
        <Text color="fg.muted" fontSize="sm">
          Loading graph renderer…
        </Text>
      </VStack>
    ),
  },
);

/** How long a resize must settle before the graph is re-fitted. */
const RESIZE_FIT_DELAY_MS = 200;

/** Let the wrapper apply new positions before fitting the view to them. */
const POSITION_FIT_DELAY_MS = 60;

interface GraphCanvasProps {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  selectedNodeId: string | null;
  expandedNodeIds: ReadonlySet<string>;
  expandingNodeId: string | null;
  onNodeSelect: (nodeId: string | null) => void;
  onNodeExpand: (nodeId: string) => void;
  /**
   * The graph as fetched, before any expansion. It is laid out on its own;
   * an expanded graph keeps these nodes where they were and places the new
   * neighbours around them.
   */
  base: GraphData;
  /** Incremented by the "Fit to view" button: fit now. */
  fitRequest: number;
}

/** Maps the contract's graph shape onto NVL's node/relationship types. */
export function GraphCanvas({
  nodes,
  relationships,
  selectedNodeId,
  expandedNodeIds,
  expandingNodeId,
  onNodeSelect,
  onNodeExpand,
  base,
  fitRequest,
}: GraphCanvasProps) {
  const nvlRef = useRef<NVL | null>(null);
  const nodeIds = useRef<string[]>([]);

  // Positions come from our own d3-force run (see graphLayout.ts); NVL renders
  // them with its "free" layout, so it does not lay the graph out again.
  const basePositions = useMemo(
    () => layoutGraph(base.nodes, base.relationships),
    [base],
  );
  const positions = useMemo<NodePosition[]>(() => {
    if (nodes === base.nodes) return basePositions;
    const pinned = new Map(basePositions.map((p) => [p.id, p]));
    return layoutGraph(nodes, relationships, pinned);
  }, [nodes, relationships, base, basePositions]);

  const fit = useCallback(() => {
    const nvl = nvlRef.current;
    if (nvl && nodeIds.current.length > 0) {
      nvl.fit(nodeIds.current, { animated: true });
    }
  }, []);
  const fitSoon = useCallback(() => {
    setTimeout(fit, POSITION_FIT_DELAY_MS);
  }, [fit]);

  useEffect(() => {
    nodeIds.current = nodes.map((node) => node.id);
  }, [nodes]);
  // New positions (another graph, or an expansion): show all of it.
  useEffect(() => {
    fitSoon();
  }, [positions, fitSoon]);
  useEffect(() => {
    if (fitRequest > 0) fit();
  }, [fitRequest, fit]);

  // Re-fit when the panel changes size (a dragged column edge, a window
  // resize), once the size has settled, so the graph uses the new space.
  const containerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let size = { width: element.clientWidth, height: element.clientHeight };
    let timer: ReturnType<typeof setTimeout> | undefined;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const moved =
        Math.abs(width - size.width) >= 2 || Math.abs(height - size.height) >= 2;
      if (!moved) return;
      size = { width, height };
      clearTimeout(timer);
      timer = setTimeout(fit, RESIZE_FIT_DELAY_MS);
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      clearTimeout(timer);
    };
  }, [fit]);

  // NVL loads asynchronously, so the first graph is fitted once it exists.
  const nvlCallbacks = useMemo<ExternalCallbacks>(
    () => ({ onInitialization: fitSoon }),
    [fitSoon],
  );

  const nvlNodes = useMemo<NvlNode[]>(
    () =>
      nodes.map((node) => {
        const selected = node.id === selectedNodeId;
        return {
          id: node.id,
          caption:
            expandingNodeId === node.id
              ? `${nodeCaption(node)} …`
              : nodeCaption(node),
          captionSize: nodeCaptionSize(node),
          color: nodeColor(node),
          size: nodeSize(node) + (selected ? 4 : 0),
          selected: selected || expandedNodeIds.has(node.id),
        };
      }),
    [nodes, selectedNodeId, expandedNodeIds, expandingNodeId],
  );

  const nvlRelationships = useMemo<NvlRelationship[]>(
    () =>
      relationships.map((rel) => {
        const kind = edgeKind(rel);
        const style = EDGE_STYLE[kind];
        // Facts are labelled; the many MENTIONS / HAS_MESSAGE edges are only
        // labelled around the selected node, so their text does not pile up.
        const labelled =
          kind === "related" ||
          kind === "same_as" ||
          rel.from === selectedNodeId ||
          rel.to === selectedNodeId;
        return {
          id: rel.id,
          from: rel.from,
          to: rel.to,
          type: rel.type,
          caption: labelled ? edgeCaption(rel) : "",
          captionSize: RELATIONSHIP_CAPTION_SIZE,
          color: style.color,
          width: style.width,
        };
      }),
    [relationships, selectedNodeId],
  );

  const mouseEventCallbacks = useMemo<MouseEventCallbacks>(
    () => ({
      onNodeClick: (node) => onNodeSelect(node.id),
      onNodeDoubleClick: (node) => onNodeExpand(node.id),
      onCanvasClick: () => onNodeSelect(null),
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [onNodeSelect, onNodeExpand],
  );

  return (
    <Box ref={containerRef} position="absolute" inset="0">
      <InteractiveNvlWrapper
        ref={nvlRef}
        nodes={nvlNodes}
        rels={nvlRelationships}
        positions={positions}
        mouseEventCallbacks={mouseEventCallbacks}
        nvlCallbacks={nvlCallbacks}
        nvlOptions={{
          layout: "free",
          initialZoom: 0.9,
          minZoom: 0.05,
          maxZoom: 3,
          allowDynamicMinZoom: true,
          disableTelemetry: true,
        }}
      />
    </Box>
  );
}
