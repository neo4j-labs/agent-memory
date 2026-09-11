"use client";

import { Spinner, Text, VStack } from "@chakra-ui/react";
import type {
  Node as NvlNode,
  Relationship as NvlRelationship,
} from "@neo4j-nvl/base";
import type { MouseEventCallbacks } from "@neo4j-nvl/react";
import dynamic from "next/dynamic";
import { useMemo } from "react";
import type { GraphNode, GraphRelationship } from "@/lib/types";
import {
  getNodeCaption,
  getNodeColor,
  getNodeSize,
  UNKNOWN_NODE_COLOR,
} from "./graph";

/**
 * NVL renders to a WebGL/canvas surface and touches `window` on import, so it
 * must be client-only. `next/dynamic` with `ssr: false` is the App Router way
 * to do that, and the `loading` state keeps the modal from flashing empty.
 */
const InteractiveNvlWrapper = dynamic(
  () => import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <VStack height="100%" justifyContent="center" gap="4">
        <Spinner size="xl" />
        <Text color="fg.muted">Loading graph visualization...</Text>
      </VStack>
    ),
  },
);

interface GraphCanvasProps {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  selectedNodeId?: string;
  selectedRelationshipId?: string;
  expandedNodeIds: ReadonlySet<string>;
  expandingNodeId: string | null;
  onNodeSelect: (nodeId: string) => void;
  onRelationshipSelect: (relationshipId: string) => void;
  onNodeExpand: (nodeId: string) => void;
}

/**
 * The NVL adapter: turns the backend's `MemoryGraph` shape into NVL's `Node` /
 * `Relationship` types and wires the mouse callbacks.
 *
 * Both types come from `@neo4j-nvl/base` rather than being re-declared here,
 * so the component cannot silently drift from the library. That is also why
 * double-click uses NVL's own `onNodeDoubleClick` callback instead of timing
 * single clicks by hand.
 */
export function GraphCanvas({
  nodes,
  relationships,
  selectedNodeId,
  selectedRelationshipId,
  expandedNodeIds,
  expandingNodeId,
  onNodeSelect,
  onRelationshipSelect,
  onNodeExpand,
}: GraphCanvasProps) {
  const nvlNodes = useMemo<NvlNode[]>(
    () =>
      nodes.map((node) => {
        const isSelected = selectedNodeId === node.id;
        const isExpanded = expandedNodeIds.has(node.id);
        const caption = getNodeCaption(node);

        let size = getNodeSize(node);
        if (isSelected) size += 5;
        if (isExpanded) size += 3;

        return {
          id: node.id,
          caption: expandingNodeId === node.id ? `${caption} ...` : caption,
          size,
          color: getNodeColor(node) || UNKNOWN_NODE_COLOR,
          selected: isSelected || isExpanded,
        };
      }),
    [nodes, selectedNodeId, expandedNodeIds, expandingNodeId],
  );

  const nvlRelationships = useMemo<NvlRelationship[]>(
    () =>
      relationships.map((rel) => ({
        id: rel.id,
        from: rel.from,
        to: rel.to,
        type: rel.type,
        caption: rel.type,
        selected: selectedRelationshipId === rel.id,
      })),
    [relationships, selectedRelationshipId],
  );

  const mouseEventCallbacks = useMemo<MouseEventCallbacks>(
    () => ({
      onNodeClick: (node) => onNodeSelect(node.id),
      onNodeDoubleClick: (node) => onNodeExpand(node.id),
      onRelationshipClick: (rel) => onRelationshipSelect(rel.id),
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [onNodeSelect, onNodeExpand, onRelationshipSelect],
  );

  return (
    <InteractiveNvlWrapper
      nodes={nvlNodes}
      rels={nvlRelationships}
      mouseEventCallbacks={mouseEventCallbacks}
      nvlOptions={{
        layout: "d3Force",
        initialZoom: 1,
        disableWebGL: false,
        minZoom: 0.1,
        maxZoom: 3,
      }}
    />
  );
}
