"use client";

import { Spinner, Text, VStack } from "@chakra-ui/react";
import type {
  ExternalCallbacks,
  NVL,
  Node as NvlNode,
  Relationship as NvlRelationship,
} from "@neo4j-nvl/base";
import type { MouseEventCallbacks } from "@neo4j-nvl/react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { GraphNode, GraphRelationship } from "@/lib/types";
import {
  EDGE_STYLE,
  edgeCaption,
  edgeKind,
  nodeCaption,
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

interface GraphCanvasProps {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  selectedNodeId: string | null;
  expandedNodeIds: ReadonlySet<string>;
  expandingNodeId: string | null;
  onNodeSelect: (nodeId: string | null) => void;
  onNodeExpand: (nodeId: string) => void;
  /**
   * Changes whenever the graph is replaced (another thread, scope or refresh):
   * the view is fitted to every node once the layout settles. Expanding a node
   * keeps the key, so the view does not jump.
   */
  fitKey: unknown;
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
  fitKey,
  fitRequest,
}: GraphCanvasProps) {
  const nvlRef = useRef<NVL | null>(null);
  const nodeIds = useRef<string[]>([]);
  const pendingFit = useRef(true);

  const fit = useCallback(() => {
    const nvl = nvlRef.current;
    if (nvl && nodeIds.current.length > 0) {
      nvl.fit(nodeIds.current, { animated: true });
    }
  }, []);

  useEffect(() => {
    nodeIds.current = nodes.map((node) => node.id);
  }, [nodes]);
  useEffect(() => {
    pendingFit.current = true;
  }, [fitKey]);
  useEffect(() => {
    if (fitRequest > 0) fit();
  }, [fitRequest, fit]);

  // A fixed initial zoom leaves a d3-force layout spilling past the panel's
  // edges; fit once the layout has settled instead.
  const nvlCallbacks = useMemo<ExternalCallbacks>(
    () => ({
      onLayoutDone: () => {
        if (!pendingFit.current) return;
        pendingFit.current = false;
        fit();
      },
    }),
    [fit],
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
        const style = EDGE_STYLE[edgeKind(rel)];
        return {
          id: rel.id,
          from: rel.from,
          to: rel.to,
          type: rel.type,
          caption: edgeCaption(rel),
          color: style.color,
          width: style.width,
        };
      }),
    [relationships],
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
    <InteractiveNvlWrapper
      ref={nvlRef}
      nodes={nvlNodes}
      rels={nvlRelationships}
      mouseEventCallbacks={mouseEventCallbacks}
      nvlCallbacks={nvlCallbacks}
      nvlOptions={{
        layout: "d3Force",
        initialZoom: 0.9,
        minZoom: 0.05,
        maxZoom: 3,
        allowDynamicMinZoom: true,
        disableTelemetry: true,
      }}
    />
  );
}
