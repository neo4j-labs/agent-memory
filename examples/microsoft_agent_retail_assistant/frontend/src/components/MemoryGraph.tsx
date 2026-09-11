"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Box, Center, Spinner, Text } from "@chakra-ui/react";
import type {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";
import type { VizGraph } from "@/lib/graph";

/**
 * `react-force-graph-2d` reaches for `window` at module scope, so it can only
 * be loaded in the browser — hence the dynamic import with `ssr: false`. It
 * also lands in its own chunk, which keeps it off the chat tab's critical path.
 */
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <Center h="100%">
      <Spinner size="lg" color="teal.solid" />
    </Center>
  ),
});

interface MemoryGraphProps {
  graph: VizGraph;
  /** Called when a node is clicked, to re-centre the query on its name. */
  onSelectNode?: (name: string, type: string) => void;
  height?: number;
}

/**
 * Interactive node-link view of the memory graph.
 *
 * The force simulation needs pixel dimensions, so the container is measured
 * with a `ResizeObserver` rather than guessed.
 */
export function MemoryGraph({
  graph,
  onSelectNode,
  height = 460,
}: MemoryGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  // `dynamic()` erases the component's generics, so the instance handle is the
  // library's default instantiation rather than one parameterised on VizNode.
  const graphRef = useRef<
    ForceGraphMethods<NodeObject, LinkObject> | undefined
  >(undefined);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setWidth(entry.contentRect.width);
      }
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <Box
      ref={containerRef}
      h={`${height}px`}
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      bg="bg.panel"
      overflow="hidden"
      position="relative"
    >
      {width > 0 && (
        <ForceGraph2D
          graphData={graph}
          width={width}
          height={height}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeLabel={(node) => `${node.type}: ${node.label}`}
          nodeColor={(node) => node.color}
          nodeRelSize={5}
          nodeCanvasObjectMode={() => "after"}
          nodeCanvasObject={drawNodeLabel}
          linkLabel={(link) => link.type}
          linkColor={() => "rgba(128,128,128,0.45)"}
          linkDirectionalArrowLength={3}
          linkDirectionalArrowRelPos={1}
          cooldownTicks={120}
          ref={graphRef}
          // Fit the whole graph in view once the simulation settles.
          onEngineStop={() => graphRef.current?.zoomToFit(400, 40)}
          onNodeClick={(node) => onSelectNode?.(node.label, node.type)}
        />
      )}
      <Text
        position="absolute"
        bottom={2}
        right={3}
        fontSize="xs"
        color="fg.subtle"
      >
        drag to pan · scroll to zoom · click a node to re-centre
      </Text>
    </Box>
  );
}

/**
 * Draw the node's name beneath it once the view is zoomed in enough.
 *
 * `dynamic()` erases the component's generics, so the node arrives as the
 * library's loose `NodeObject` and its extra fields have to be re-checked.
 */
function drawNodeLabel(
  node: NodeObject,
  ctx: CanvasRenderingContext2D,
  globalScale: number
): void {
  if (globalScale < 0.7 || node.x === undefined || node.y === undefined) return;
  const name: string = typeof node.label === "string" ? node.label : "";
  if (!name) return;
  const label = name.length > 28 ? `${name.slice(0, 27)}…` : name;
  ctx.font = `${11 / globalScale}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(113,113,122,0.95)";
  ctx.fillText(label, node.x, node.y + 7);
}
