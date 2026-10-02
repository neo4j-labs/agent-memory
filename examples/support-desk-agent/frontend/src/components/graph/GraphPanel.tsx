"use client";

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Flex,
  IconButton,
  SegmentGroup,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useMemo, useState } from "react";
import { LuExpand, LuFocus, LuRefreshCw } from "react-icons/lu";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { useApi } from "@/hooks/useApi";
import { api, errorMessage } from "@/lib/api";
import { preview } from "@/lib/format";
import { labelColor } from "@/lib/labels";
import type { GraphData, GraphNode } from "@/lib/types";
import { GraphCanvas } from "./GraphCanvas";
import { EDGE_STYLE, edgeKind, nodeLegendLabel, type EdgeKind } from "./graphStyle";

type Scope = "thread" | "all";

const NO_EXPANSION: ReadonlySet<string> = new Set();

interface GraphPanelProps {
  threadId: string | null;
  refreshKey: number;
}

interface Expansion {
  /** The fetched graph this expansion was built on. */
  base: GraphData | null;
  graph: GraphData | null;
  expanded: ReadonlySet<string>;
}

function mergeGraphs(a: GraphData, b: GraphData): GraphData {
  const nodeIds = new Set(a.nodes.map((n) => n.id));
  const relIds = new Set(a.relationships.map((r) => r.id));
  const nodes = [...a.nodes, ...b.nodes.filter((n) => !nodeIds.has(n.id))];
  const known = new Set(nodes.map((n) => n.id));
  const relationships = [
    ...a.relationships,
    ...b.relationships.filter(
      (r) => !relIds.has(r.id) && known.has(r.from) && known.has(r.to),
    ),
  ];
  return { nodes, relationships };
}

function GraphLegend({ graph }: { graph: GraphData }) {
  const labels = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of graph.nodes) {
      const label = nodeLegendLabel(node);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [graph]);

  const edges = useMemo(() => {
    const kinds = new Set<EdgeKind>();
    for (const rel of graph.relationships) kinds.add(edgeKind(rel));
    return [...kinds];
  }, [graph]);

  return (
    <Flex gap="3" flexWrap="wrap" fontSize="xs" aria-label="Graph legend">
      {labels.map(([label, count]) => (
        <Flex key={label} alignItems="center" gap="1">
          <Box
            w="3"
            h="3"
            borderRadius="full"
            style={{ background: labelColor(label) }}
          />
          <Text>
            {label}{" "}
            <Text as="span" color="fg.muted">
              {count}
            </Text>
          </Text>
        </Flex>
      ))}
      {edges.map((kind) => (
        <Flex key={kind} alignItems="center" gap="1">
          <Box
            w="4"
            h="0.5"
            style={{ background: EDGE_STYLE[kind].color }}
          />
          <Text color="fg.muted">{EDGE_STYLE[kind].label}</Text>
        </Flex>
      ))}
    </Flex>
  );
}

function NodeDetails({
  node,
  expanded,
  expanding,
  onExpand,
  onClose,
}: {
  node: GraphNode;
  expanded: boolean;
  expanding: boolean;
  onExpand: () => void;
  onClose: () => void;
}) {
  const entries = Object.entries(node.properties ?? {}).filter(
    ([key]) => key !== "embedding",
  );
  return (
    <Box
      p="3"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      bg="bg.panel"
    >
      <Flex alignItems="center" gap="2">
        <Box
          w="3"
          h="3"
          borderRadius="full"
          flexShrink={0}
          style={{ background: labelColor(nodeLegendLabel(node)) }}
        />
        <Text fontWeight="medium" fontSize="sm" flex="1" truncate>
          {node.caption}
        </Text>
        <CloseButton size="2xs" onClick={onClose} aria-label="Close details" />
      </Flex>
      <Flex gap="1" mt="1" flexWrap="wrap">
        <Badge size="xs" variant="outline">
          {node.kind}
        </Badge>
        {node.labels.map((label) => (
          <Badge key={label} size="xs" variant="subtle">
            {label}
          </Badge>
        ))}
      </Flex>
      {entries.length > 0 ? (
        <Stack gap="0.5" mt="2" fontSize="xs" maxH="140px" overflowY="auto">
          {entries.map(([key, value]) => (
            <Flex key={key} gap="2">
              <Text color="fg.muted" flexShrink={0} fontFamily="mono">
                {key}
              </Text>
              <Text truncate title={preview(value, 500)}>
                {preview(value, 120)}
              </Text>
            </Flex>
          ))}
        </Stack>
      ) : null}
      <Button
        mt="2"
        size="xs"
        variant="outline"
        onClick={onExpand}
        loading={expanding}
        disabled={expanded}
      >
        <LuExpand />
        {expanded ? "Neighbours shown" : "Expand neighbours"}
      </Button>
    </Box>
  );
}

/**
 * The thread's subgraph — conversation, messages, the entities they mention
 * and the typed RELATED_TO edges among those entities — or, with the scope
 * switched to "All seeded", every seeded entity.
 */
export function GraphPanel({ threadId, refreshKey }: GraphPanelProps) {
  const [scope, setScope] = useState<Scope>("thread");
  const effectiveThread = scope === "thread" ? threadId : null;

  const load = useCallback(
    (signal: AbortSignal) => api.graph.get(effectiveThread, { signal }),
    [effectiveThread],
  );
  const { data, error, loading, reload } = useApi(load, refreshKey);

  const [expansion, setExpansion] = useState<Expansion>({
    base: null,
    graph: null,
    expanded: NO_EXPANSION,
  });
  const [expandingNodeId, setExpandingNodeId] = useState<string | null>(null);
  const [expandError, setExpandError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [fitRequest, setFitRequest] = useState(0);

  // An expansion belongs to the fetch it was built on; a refetch resets it.
  const current = expansion.base === data && data !== null;
  const graph = current ? expansion.graph : data;
  const expanded = current ? expansion.expanded : NO_EXPANSION;

  const expand = useCallback(
    async (nodeId: string) => {
      if (!data || expandingNodeId) return;
      setExpandingNodeId(nodeId);
      setExpandError(null);
      try {
        const neighbours = await api.graph.neighbors(nodeId);
        setExpansion((prev) => {
          const start =
            prev.base === data && prev.graph
              ? prev
              : { base: data, graph: data, expanded: new Set<string>() };
          return {
            base: data,
            graph: mergeGraphs(start.graph ?? data, neighbours),
            expanded: new Set(start.expanded).add(nodeId),
          };
        });
      } catch (err) {
        setExpandError(`Could not expand the node: ${errorMessage(err)}`);
      } finally {
        setExpandingNodeId(null);
      }
    },
    [data, expandingNodeId],
  );

  const selectedNode =
    graph?.nodes.find((node) => node.id === selectedNodeId) ?? null;

  return (
    <Stack gap="3" h="full" minH="0">
      <Flex alignItems="center" gap="2" flexWrap="wrap">
        <SegmentGroup.Root
          size="xs"
          value={scope}
          onValueChange={(details) => {
            if (details.value) setScope(details.value as Scope);
          }}
          aria-label="Graph scope"
        >
          <SegmentGroup.Indicator />
          <SegmentGroup.Items
            items={[
              { value: "thread", label: "This conversation" },
              { value: "all", label: "All seeded" },
            ]}
          />
        </SegmentGroup.Root>
        <Flex ml="auto" alignItems="center" gap="2">
          {graph ? (
            <Text fontSize="xs" color="fg.muted">
              {graph.nodes.length} nodes · {graph.relationships.length} rels
            </Text>
          ) : null}
          <IconButton
            aria-label="Fit graph to view"
            title="Fit to view"
            size="xs"
            variant="ghost"
            onClick={() => setFitRequest((n) => n + 1)}
            disabled={!graph || graph.nodes.length === 0}
          >
            <LuFocus />
          </IconButton>
          <IconButton
            aria-label="Refresh graph"
            size="xs"
            variant="ghost"
            onClick={reload}
            disabled={loading}
          >
            {loading ? <Spinner size="xs" /> : <LuRefreshCw />}
          </IconButton>
        </Flex>
      </Flex>

      {error ? (
        <StatusAlert status="error" title="Graph unavailable" description={error} />
      ) : null}
      {expandError ? (
        <StatusAlert
          status="warning"
          title="Expand failed"
          description={expandError}
          onDismiss={() => setExpandError(null)}
        />
      ) : null}

      <Box
        position="relative"
        flex="1"
        minH="360px"
        borderWidth="1px"
        borderColor="border.subtle"
        borderRadius="md"
        overflow="hidden"
        bg="bg.subtle"
        style={{ touchAction: "none" }}
      >
        {graph && graph.nodes.length > 0 ? (
          <GraphCanvas
            nodes={graph.nodes}
            relationships={graph.relationships}
            selectedNodeId={selectedNode?.id ?? null}
            expandedNodeIds={expanded}
            expandingNodeId={expandingNodeId}
            onNodeSelect={setSelectedNodeId}
            onNodeExpand={expand}
            fitKey={data}
            fitRequest={fitRequest}
          />
        ) : (
          <Flex h="full" alignItems="center" justifyContent="center" p="6">
            {loading ? (
              <Spinner />
            ) : (
              <Text color="fg.muted" fontSize="sm" textAlign="center">
                {scope === "thread" && !threadId
                  ? "Select a conversation, or switch to “All seeded”."
                  : "Nothing in the graph for this scope yet."}
              </Text>
            )}
          </Flex>
        )}
      </Box>

      {graph && graph.nodes.length > 0 ? <GraphLegend graph={graph} /> : null}

      {selectedNode ? (
        <NodeDetails
          node={selectedNode}
          expanded={expanded.has(selectedNode.id)}
          expanding={expandingNodeId === selectedNode.id}
          onExpand={() => expand(selectedNode.id)}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : (
        <Text fontSize="xs" color="fg.muted">
          Click a node for its properties · double-click to expand its
          neighbours · drag to pan · scroll to zoom
        </Text>
      )}
    </Stack>
  );
}
