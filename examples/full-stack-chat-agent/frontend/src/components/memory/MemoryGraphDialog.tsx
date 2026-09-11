"use client";

import {
  Badge,
  Box,
  Button,
  CloseButton,
  Dialog,
  Flex,
  IconButton,
  Portal,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { LuRefreshCw } from "react-icons/lu";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { api } from "@/lib/api";
import type { GraphNode, MemoryGraph } from "@/lib/types";
import { getNodeMemoryType, type MemoryTypeFilters } from "./graph";
import { GraphCanvas } from "./GraphCanvas";
import { GraphLegend } from "./GraphLegend";
import { GraphStatsPanel } from "./GraphStatsPanel";
import { MemoryFilterBar } from "./MemoryFilterBar";
import { NodePropertyPanel } from "./NodePropertyPanel";

interface MemoryGraphDialogProps {
  isOpen: boolean;
  onClose: () => void;
  threadId?: string;
  /** Bumped by the page after each completed turn, to trigger a refetch. */
  memoryVersion?: number;
}

const ALL_ENABLED: MemoryTypeFilters = {
  "short-term": true,
  "long-term": true,
  reasoning: true,
};

function countBy<T>(
  items: T[],
  key: (item: T) => string[],
): [string, number][] {
  const counts = new Map<string, number>();
  for (const item of items) {
    for (const label of key(item)) {
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * Full-screen explorer for the memory graph.
 *
 * Built on Chakra v3's `Dialog`, which supplies the focus trap, Escape
 * handling, scroll lock, `aria-modal` and focus restore that the previous
 * hand-rolled fixed-position overlay lacked.
 */
export function MemoryGraphDialog({
  isOpen,
  onClose,
  threadId,
  memoryVersion = 0,
}: MemoryGraphDialogProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [graphData, setGraphData] = useState<MemoryGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedRelationshipId, setSelectedRelationshipId] = useState<
    string | null
  >(null);
  const [filters, setFilters] = useState<MemoryTypeFilters>(ALL_ENABLED);
  const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [expandingNodeId, setExpandingNodeId] = useState<string | null>(null);
  // Incremented by the refresh button; the effect below owns all loading.
  const [reloadToken, setReloadToken] = useState(0);
  const reload = useCallback(() => setReloadToken((token) => token + 1), []);

  // One loader, with `threadId` and `memoryVersion` in the dependency list —
  // so the subgraph refetches when the user switches conversation while the
  // dialog is open (the old effect depended on `[isOpen]` alone and kept
  // showing the previous conversation) and again after each completed turn.
  // `cancelled` drops a late response from a superseded request.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;

    const load = async () => {
      setIsLoading(true);
      setError(null);
      setSelectedNodeId(null);
      setSelectedRelationshipId(null);
      setExpandedNodeIds(new Set());
      setExpandingNodeId(null);
      try {
        const data = await api.memory.getGraph(threadId);
        if (cancelled) return;
        setGraphData(data);
        if (data.nodes.length === 0) {
          setError(
            "No memory data for this conversation yet. Enable memory and chat to build the graph.",
          );
        }
      } catch (err) {
        if (cancelled) return;
        setGraphData(null);
        setError(
          err instanceof Error ? err.message : "Failed to load memory graph",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [isOpen, threadId, memoryVersion, reloadToken]);

  const handleNodeExpand = useCallback(
    async (nodeId: string) => {
      if (expandedNodeIds.has(nodeId) || expandingNodeId) return;
      setExpandingNodeId(nodeId);
      try {
        const neighbors = await api.memory.getNodeNeighbors(nodeId, 1, 50);
        // Merge, de-duplicating against what is already on screen.
        setGraphData((prev) => {
          if (!prev) return prev;
          const nodeIds = new Set(prev.nodes.map((n) => n.id));
          const relIds = new Set(prev.relationships.map((r) => r.id));
          const newNodes = neighbors.nodes.filter((n) => !nodeIds.has(n.id));
          const newRels = neighbors.relationships.filter(
            (r) => !relIds.has(r.id),
          );
          if (newNodes.length === 0 && newRels.length === 0) return prev;
          return {
            nodes: [...prev.nodes, ...newNodes],
            relationships: [...prev.relationships, ...newRels],
          };
        });
        setExpandedNodeIds((prev) => new Set(prev).add(nodeId));
      } catch (err) {
        setError(
          err instanceof Error
            ? `Could not expand node: ${err.message}`
            : "Could not expand node",
        );
      } finally {
        setExpandingNodeId(null);
      }
    },
    [expandedNodeIds, expandingNodeId],
  );

  const nodesById = useMemo(() => {
    const map = new Map<string, GraphNode>();
    for (const node of graphData?.nodes ?? []) map.set(node.id, node);
    return map;
  }, [graphData]);

  const filteredNodes = useMemo(
    () =>
      (graphData?.nodes ?? []).filter(
        (node) => filters[getNodeMemoryType(node)],
      ),
    [graphData, filters],
  );

  // Hide edges whose endpoints are filtered out, so nothing dangles.
  const filteredRelationships = useMemo(() => {
    const visible = new Set(filteredNodes.map((n) => n.id));
    return (graphData?.relationships ?? []).filter(
      (rel) => visible.has(rel.from) && visible.has(rel.to),
    );
  }, [graphData, filteredNodes]);

  const nodesByLabel = useMemo(
    () => countBy(graphData?.nodes ?? [], (node) => node.labels),
    [graphData],
  );
  const relationshipsByType = useMemo(
    () => countBy(graphData?.relationships ?? [], (rel) => [rel.type]),
    [graphData],
  );

  const selectedNode = selectedNodeId
    ? (nodesById.get(selectedNodeId) ?? null)
    : null;
  const selectedRelationship = selectedRelationshipId
    ? (graphData?.relationships.find((r) => r.id === selectedRelationshipId) ??
      null)
    : null;

  const clearSelection = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedRelationshipId(null);
  }, []);

  return (
    <Dialog.Root
      open={isOpen}
      onOpenChange={(details) => {
        if (!details.open) onClose();
      }}
      size="cover"
      scrollBehavior="inside"
    >
      <Portal>
        <Dialog.Backdrop />
        <Dialog.Positioner>
          <Dialog.Content
            display="flex"
            flexDirection="column"
            overflow="hidden"
          >
            <Dialog.Header
              borderBottomWidth="1px"
              borderColor="border.subtle"
              bg="bg.subtle"
            >
              <Stack gap="1" flex="1">
                <Dialog.Title>Memory graph</Dialog.Title>
                <Text fontSize="xs" color="fg.muted">
                  Short-term, long-term and reasoning memory for this
                  conversation
                </Text>
                {graphData && (
                  <Flex gap="2" mt="1" flexWrap="wrap">
                    <Badge colorPalette="brand" fontSize="xs">
                      {filteredNodes.length} / {graphData.nodes.length} nodes
                    </Badge>
                    <Badge colorPalette="green" fontSize="xs">
                      {filteredRelationships.length} /{" "}
                      {graphData.relationships.length} relationships
                    </Badge>
                  </Flex>
                )}
              </Stack>
              <Flex gap="2" alignItems="center">
                <IconButton
                  aria-label="Refresh graph"
                  size="sm"
                  variant="ghost"
                  onClick={reload}
                  disabled={isLoading}
                >
                  <LuRefreshCw />
                </IconButton>
                <Dialog.CloseTrigger asChild>
                  <CloseButton size="sm" />
                </Dialog.CloseTrigger>
              </Flex>
            </Dialog.Header>

            <MemoryFilterBar filters={filters} onChange={setFilters} />

            <Dialog.Body p="0" flex="1" position="relative" bg="bg">
              {isLoading ? (
                <Stack
                  height="100%"
                  justifyContent="center"
                  alignItems="center"
                  gap="4"
                >
                  <Spinner size="xl" />
                  <Text color="fg.muted">Loading memory graph...</Text>
                </Stack>
              ) : error ? (
                <Stack
                  height="100%"
                  justifyContent="center"
                  alignItems="center"
                  gap="4"
                  p="6"
                >
                  <Box maxW="md" w="full">
                    <StatusAlert
                      status={graphData ? "info" : "error"}
                      title={
                        graphData ? "Nothing to show yet" : "Graph unavailable"
                      }
                      description={error}
                    />
                  </Box>
                  <Button colorPalette="brand" onClick={reload}>
                    Try again
                  </Button>
                </Stack>
              ) : graphData && graphData.nodes.length > 0 ? (
                <Box height="100%" width="100%" position="relative">
                  <Box
                    height="100%"
                    width="100%"
                    style={{ touchAction: "none" }}
                  >
                    <GraphCanvas
                      nodes={filteredNodes}
                      relationships={filteredRelationships}
                      selectedNodeId={selectedNodeId ?? undefined}
                      selectedRelationshipId={
                        selectedRelationshipId ?? undefined
                      }
                      expandedNodeIds={expandedNodeIds}
                      expandingNodeId={expandingNodeId}
                      onNodeSelect={(nodeId) => {
                        setSelectedNodeId(nodeId);
                        setSelectedRelationshipId(null);
                      }}
                      onRelationshipSelect={(relId) => {
                        setSelectedRelationshipId(relId);
                        setSelectedNodeId(null);
                      }}
                      onNodeExpand={handleNodeExpand}
                    />
                  </Box>

                  {!selectedNode && !selectedRelationship && (
                    <GraphLegend filters={filters} />
                  )}

                  <NodePropertyPanel
                    node={selectedNode}
                    relationship={selectedRelationship}
                    nodesById={nodesById}
                    isExpanded={
                      selectedNode
                        ? expandedNodeIds.has(selectedNode.id)
                        : false
                    }
                    isExpanding={
                      selectedNode ? expandingNodeId === selectedNode.id : false
                    }
                    expandDisabled={expandingNodeId !== null}
                    onExpand={handleNodeExpand}
                    onClose={clearSelection}
                  />

                  <GraphStatsPanel
                    nodeCount={filteredNodes.length}
                    relationshipCount={filteredRelationships.length}
                    nodesByLabel={nodesByLabel}
                    relationshipsByType={relationshipsByType}
                  />
                </Box>
              ) : null}
            </Dialog.Body>

            {graphData &&
              graphData.nodes.length > 0 &&
              !isLoading &&
              !error && (
                <Dialog.Footer
                  justifyContent="center"
                  borderTopWidth="1px"
                  borderColor="border.subtle"
                  bg="bg.subtle"
                  py="3"
                >
                  <Text fontSize="xs" color="fg.muted" textAlign="center">
                    Click a node or relationship for details · double-click a
                    node to expand its neighbours · drag to pan · scroll to zoom
                  </Text>
                </Dialog.Footer>
              )}
          </Dialog.Content>
        </Dialog.Positioner>
      </Portal>
    </Dialog.Root>
  );
}

export default MemoryGraphDialog;
