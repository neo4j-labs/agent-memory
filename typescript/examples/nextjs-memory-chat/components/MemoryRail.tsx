"use client";

/**
 * The memory rail — the argument for graph-native memory, on screen.
 *
 * It owns the accumulated graph, which is what makes `expandGraph` correct: the
 * ids already on the canvas go out as `loadedIds`, so each expansion returns only
 * the delta. It also owns the order of operations after a turn:
 *
 *     context  →  wait for extraction  →  refetch the graph
 *
 * That middle await matters. Refetching the graph straight after the answer
 * shows a canvas that is one turn behind, because NAMS extracts entities in a
 * background pipeline.
 */

import { Box, Flex, HStack, Heading, Separator, Stack, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { ContextPayload, ExtractionPayload, GraphNodePayload, GraphPayload } from "@/lib/types";
import { ExtractionBadge, type ExtractionState } from "./ExtractionBadge";
import { GraphView } from "./GraphView";
import { TraceDrawer } from "./TraceDrawer";
import type { TurnSignal } from "./Workspace";

/** Union two graph fragments, preferring what is already on screen. */
function merge(current: GraphPayload, delta: GraphPayload): GraphPayload {
  const nodes = new Map(current.nodes.map((node) => [node.id, node]));
  for (const node of delta.nodes) if (!nodes.has(node.id)) nodes.set(node.id, node);
  const edges = new Map(current.edges.map((edge) => [edge.id, edge]));
  for (const edge of delta.edges) if (!edges.has(edge.id)) edges.set(edge.id, edge);
  return { nodes: [...nodes.values()], edges: [...edges.values()] };
}

export function MemoryRail({
  conversationId,
  turn,
}: {
  conversationId: string;
  turn: TurnSignal | null;
}) {
  const [context, setContext] = useState<ContextPayload | null>(null);
  const [graph, setGraph] = useState<GraphPayload>({ nodes: [], edges: [] });
  const [extraction, setExtraction] = useState<ExtractionState>("idle");
  const [entityCount, setEntityCount] = useState<number | null>(null);
  const [expanding, setExpanding] = useState<string | null>(null);
  const [selected, setSelected] = useState<GraphNodePayload | null>(null);

  // Read inside async callbacks without making them depend on the graph.
  const loadedIds = useRef<string[]>([]);
  useEffect(() => {
    loadedIds.current = graph.nodes.map((node) => node.id);
  }, [graph]);

  const loadContext = useCallback(
    async (signal?: AbortSignal) => {
      const response = await fetch(
        `/api/memory/context?conversationId=${encodeURIComponent(conversationId)}`,
        { signal },
      );
      if (response.ok) setContext((await response.json()) as ContextPayload);
    },
    [conversationId],
  );

  const loadGraph = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch("/api/memory/graph", { signal });
    if (response.ok) setGraph((await response.json()) as GraphPayload);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    // The React Compiler rule cannot see that these loaders only setState from
    // an awaited callback, not synchronously in the effect body.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void Promise.allSettled([loadContext(controller.signal), loadGraph(controller.signal)]);
    return () => controller.abort();
  }, [loadContext, loadGraph]);

  useEffect(() => {
    if (!turn) return;
    const controller = new AbortController();
    void (async () => {
      try {
        await loadContext(controller.signal);
        setExtraction("waiting");
        const response = await fetch("/api/memory/extraction", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: turn.question, knownIds: loadedIds.current }),
          signal: controller.signal,
        });
        if (response.ok) {
          const payload = (await response.json()) as ExtractionPayload;
          setEntityCount(payload.entities.length);
          setExtraction(payload.settled ? "settled" : "quiet");
        } else {
          setExtraction("idle");
        }
        // Only now is the graph worth refetching.
        await loadGraph(controller.signal);
      } catch {
        setExtraction("idle");
      }
    })();
    return () => controller.abort();
  }, [turn, loadContext, loadGraph]);

  const expand = useCallback(
    async (nodeId: string) => {
      setExpanding(nodeId);
      try {
        const response = await fetch("/api/memory/graph", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // Everything on screen, so the service can answer with the delta only.
          body: JSON.stringify({ nodeId, loadedIds: loadedIds.current }),
        });
        if (response.ok) {
          const delta = (await response.json()) as GraphPayload;
          setGraph((current) => merge(current, delta));
        }
      } catch {
        // Leave the canvas as it was.
      } finally {
        setExpanding(null);
      }
    },
    [],
  );

  return (
    <Flex direction="column" h="full" minH={0}>
      <Stack px={5} py={4} gap={3}>
        <HStack justify="space-between">
          <Heading size="sm">Memory</Heading>
          <TraceDrawer conversationId={conversationId} />
        </HStack>

        <HStack gap={5} fontSize="xs" color="fg.muted">
          <Text>
            <Text as="span" fontWeight="bold" color="fg">
              {context?.reflections.length ?? 0}
            </Text>{" "}
            reflections
          </Text>
          <Text>
            <Text as="span" fontWeight="bold" color="fg">
              {context?.observations.length ?? 0}
            </Text>{" "}
            observations
          </Text>
          <Text>
            <Text as="span" fontWeight="bold" color="fg">
              {context?.recentMessages.length ?? 0}
            </Text>{" "}
            recent messages
          </Text>
        </HStack>
        <Text fontSize="xs" color="fg.subtle">
          These three tiers are what `agentMemoryMiddleware` prepends to the next model call.
        </Text>

        <ExtractionBadge state={extraction} entityCount={entityCount} />
      </Stack>

      <Separator />

      <Box flex="1" minH="320px">
        <GraphView
          nodes={graph.nodes}
          edges={graph.edges}
          expandingId={expanding}
          onExpand={(nodeId) => void expand(nodeId)}
          onSelect={setSelected}
        />
      </Box>

      <Separator />

      <Stack px={5} py={3} gap={1}>
        {selected ? (
          <>
            <Text fontSize="sm" fontWeight="semibold">
              {selected.name}
            </Text>
            <Text fontSize="xs" color="fg.muted">
              {selected.type} · double-click a node to expand its neighbourhood
            </Text>
          </>
        ) : (
          <Text fontSize="xs" color="fg.muted">
            {graph.nodes.length} node(s), {graph.edges.length} edge(s). Click to inspect,
            double-click to expand.
          </Text>
        )}
      </Stack>
    </Flex>
  );
}
