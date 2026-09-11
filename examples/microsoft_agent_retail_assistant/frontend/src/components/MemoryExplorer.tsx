"use client";

import { useCallback, useMemo, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  HStack,
  Heading,
  Input,
  SimpleGrid,
  Spinner,
  Tabs,
  Text,
  VStack,
} from "@chakra-ui/react";
import {
  getMemoryContext,
  getMemoryGraph,
  type GraphData,
  type MemoryContext,
} from "@/lib/api";
import { toVizGraph } from "@/lib/graph";
import { useAsyncData } from "@/lib/useAsyncData";
import { MemoryGraph } from "./MemoryGraph";

interface MemoryExplorerProps {
  sessionId: string;
}

type ExplorerTab = "context" | "graph";

export function MemoryExplorer({ sessionId }: MemoryExplorerProps) {
  const [activeTab, setActiveTab] = useState<ExplorerTab>("context");
  const [query, setQuery] = useState("");
  /** When set, the graph shows this entity's neighbourhood instead of the
      whole session (the backend's `center_entity` branch). */
  const [centerEntity, setCenterEntity] = useState<string | null>(null);

  return (
    <Box>
      <HStack justify="space-between" mb={6} flexWrap="wrap" gap={3}>
        <Heading size="lg">Memory Explorer</Heading>
        <Input
          placeholder="Search query (also matches reasoning traces)..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          maxW="320px"
          size="sm"
          bg="bg.panel"
        />
      </HStack>

      <Tabs.Root
        value={activeTab}
        onValueChange={(e) => setActiveTab(e.value as ExplorerTab)}
        colorPalette="teal"
        lazyMount
      >
        <Tabs.List mb={6}>
          <Tabs.Trigger value="context">Memory Context</Tabs.Trigger>
          <Tabs.Trigger value="graph">Knowledge Graph</Tabs.Trigger>
        </Tabs.List>

        {/* Panels live inside the root so the triggers get their
            `aria-controls` targets, tabpanel roles and focus management —
            and each panel owns the request that fills it. */}
        <Tabs.Content value="context">
          <ContextPanel sessionId={sessionId} query={query} />
        </Tabs.Content>

        <Tabs.Content value="graph">
          <GraphPanel
            sessionId={sessionId}
            centerEntity={centerEntity}
            onSelectNode={setCenterEntity}
            onClearCenter={() => setCenterEntity(null)}
          />
        </Tabs.Content>
      </Tabs.Root>
    </Box>
  );
}

function PanelStatus({
  isLoading,
  error,
  onReload,
  label,
}: {
  isLoading: boolean;
  error: string | null;
  onReload: () => void;
  label: string;
}) {
  return (
    <HStack justify="space-between" mb={4} flexWrap="wrap" gap={2}>
      <Text fontSize="sm" color={error ? "fg.error" : "fg.muted"}>
        {error ?? label}
      </Text>
      <Button
        size="xs"
        variant="outline"
        colorPalette="teal"
        onClick={onReload}
        loading={isLoading}
      >
        Refresh
      </Button>
    </HStack>
  );
}

function Loading() {
  return (
    <Box textAlign="center" py={10}>
      <Spinner size="lg" color="teal.solid" />
      <Text mt={4} color="fg.muted">
        Loading memory data...
      </Text>
    </Box>
  );
}

function ContextPanel({
  sessionId,
  query,
}: {
  sessionId: string;
  query: string;
}) {
  const load = useCallback(
    () => getMemoryContext(sessionId, query || undefined),
    [sessionId, query]
  );
  const { data, error, isLoading, reload } = useAsyncData(
    `context|${sessionId}|${query}`,
    load
  );

  return (
    <Box>
      <PanelStatus
        isLoading={isLoading}
        error={error}
        onReload={reload}
        label="All three memory layers, as the backend assembles them for the next prompt."
      />
      {isLoading && !data ? <Loading /> : <ContextView context={data} />}
    </Box>
  );
}

function ContextView({ context }: { context: MemoryContext | null }) {
  if (!context) {
    return (
      <Card.Root>
        <Card.Body>
          <Text color="fg.muted">No memory context available</Text>
        </Card.Body>
      </Card.Root>
    );
  }

  return (
    <SimpleGrid columns={{ base: 1, lg: 3 }} gap={6}>
      {/* Short-term Memory */}
      <Card.Root borderTopWidth="4px" borderTopColor="green.solid">
        <Card.Header>
          <HStack>
            <Badge colorPalette="green" size="lg">
              Short-term
            </Badge>
            <Text color="fg.muted" fontSize="sm">
              ({context.short_term.length} messages)
            </Text>
          </HStack>
        </Card.Header>
        <Card.Body maxH="400px" overflowY="auto">
          <VStack align="stretch" gap={2}>
            {context.short_term.length === 0 ? (
              <Text color="fg.subtle" fontSize="sm">
                No recent messages
              </Text>
            ) : (
              context.short_term.map((msg) => (
                <Box
                  key={msg.id}
                  p={2}
                  bg="bg.subtle"
                  borderRadius="md"
                  borderLeftWidth="3px"
                  borderLeftColor={
                    msg.role === "user" ? "blue.solid" : "green.solid"
                  }
                >
                  <Badge
                    size="sm"
                    colorPalette={msg.role === "user" ? "blue" : "green"}
                  >
                    {msg.role}
                  </Badge>
                  <Text fontSize="sm" mt={1} lineClamp={3}>
                    {msg.content}
                  </Text>
                </Box>
              ))
            )}
          </VStack>
        </Card.Body>
      </Card.Root>

      {/* Long-term Memory */}
      <Card.Root borderTopWidth="4px" borderTopColor="orange.solid">
        <Card.Header>
          <HStack>
            <Badge colorPalette="orange" size="lg">
              Long-term
            </Badge>
            <Text color="fg.muted" fontSize="sm">
              ({context.long_term.entities.length} entities,{" "}
              {context.long_term.preferences.length} preferences)
            </Text>
          </HStack>
        </Card.Header>
        <Card.Body maxH="400px" overflowY="auto">
          <VStack align="stretch" gap={4}>
            {/* Entities */}
            <Box>
              <Text fontWeight="medium" mb={2}>
                Entities
              </Text>
              <VStack align="stretch" gap={1}>
                {context.long_term.entities.length === 0 ? (
                  <Text color="fg.subtle" fontSize="sm">
                    No entities yet
                  </Text>
                ) : (
                  context.long_term.entities.slice(0, 10).map((entity) => (
                    <HStack
                      key={entity.id}
                      p={1}
                      bg="bg.subtle"
                      borderRadius="sm"
                    >
                      <Badge size="sm">{entity.type}</Badge>
                      <Text fontSize="sm" lineClamp={1}>
                        {entity.name}
                      </Text>
                    </HStack>
                  ))
                )}
              </VStack>
            </Box>

            {/* Preferences */}
            <Box>
              <Text fontWeight="medium" mb={2}>
                Preferences
              </Text>
              <VStack align="stretch" gap={1}>
                {context.long_term.preferences.length === 0 ? (
                  <Text color="fg.subtle" fontSize="sm">
                    No preferences yet
                  </Text>
                ) : (
                  context.long_term.preferences.map((pref) => (
                    <HStack
                      key={pref.id}
                      p={1}
                      bg="bg.subtle"
                      borderRadius="sm"
                    >
                      <Badge size="sm" colorPalette="purple">
                        {pref.category}
                      </Badge>
                      <Text fontSize="sm">{pref.preference}</Text>
                    </HStack>
                  ))
                )}
              </VStack>
            </Box>
          </VStack>
        </Card.Body>
      </Card.Root>

      {/* Reasoning Memory */}
      <Card.Root borderTopWidth="4px" borderTopColor="purple.solid">
        <Card.Header>
          <HStack>
            <Badge colorPalette="purple" size="lg">
              Reasoning
            </Badge>
            <Text color="fg.muted" fontSize="sm">
              ({context.reasoning.length} traces)
            </Text>
          </HStack>
        </Card.Header>
        <Card.Body maxH="400px" overflowY="auto">
          <VStack align="stretch" gap={2}>
            {context.reasoning.length === 0 ? (
              <Text color="fg.subtle" fontSize="sm">
                No reasoning traces yet — traces are matched by similarity, so
                type a search query above to pull the relevant ones.
              </Text>
            ) : (
              context.reasoning.map((trace) => (
                <Box
                  key={trace.id}
                  p={2}
                  bg="bg.subtle"
                  borderRadius="md"
                  borderLeftWidth="3px"
                  borderLeftColor="purple.solid"
                >
                  <Text fontSize="sm" fontWeight="medium" lineClamp={2}>
                    {trace.task}
                  </Text>
                  <HStack mt={1}>
                    <Badge
                      size="sm"
                      colorPalette={
                        trace.outcome === "success" ? "green" : "red"
                      }
                    >
                      {trace.outcome}
                    </Badge>
                    <Text fontSize="xs" color="fg.muted">
                      {trace.steps} steps
                    </Text>
                  </HStack>
                </Box>
              ))
            )}
          </VStack>
        </Card.Body>
      </Card.Root>
    </SimpleGrid>
  );
}

function GraphPanel({
  sessionId,
  centerEntity,
  onSelectNode,
  onClearCenter,
}: {
  sessionId: string;
  centerEntity: string | null;
  onSelectNode: (name: string) => void;
  onClearCenter: () => void;
}) {
  const load = useCallback(
    () =>
      getMemoryGraph(
        sessionId,
        centerEntity ?? undefined,
        centerEntity ? 2 : undefined
      ),
    [sessionId, centerEntity]
  );
  const { data, error, isLoading, reload } = useAsyncData(
    `graph|${sessionId}|${centerEntity ?? ""}`,
    load
  );

  return (
    <Box>
      <PanelStatus
        isLoading={isLoading}
        error={error}
        onReload={reload}
        label={
          centerEntity
            ? `Two-hop neighbourhood of "${centerEntity}"`
            : "Everything this session wrote to memory"
        }
      />
      {isLoading && !data ? (
        <Loading />
      ) : (
        <GraphView
          data={data}
          centerEntity={centerEntity}
          onSelectNode={onSelectNode}
          onClearCenter={onClearCenter}
        />
      )}
    </Box>
  );
}

function GraphView({
  data,
  centerEntity,
  onSelectNode,
  onClearCenter,
}: {
  data: GraphData | null;
  centerEntity: string | null;
  onSelectNode: (name: string) => void;
  onClearCenter: () => void;
}) {
  // Counts come from the graph that is actually drawn: edges pointing at nodes
  // the backend's per-type cap left out are dropped, so the stats and the
  // picture agree. Memoised because the force simulation restarts whenever it
  // is handed a new `graphData` object.
  const graph = useMemo(
    () => toVizGraph(data ?? { nodes: [], edges: [] }),
    [data]
  );

  if (!data || (data.nodes.length === 0 && data.edges.length === 0)) {
    return (
      <Card.Root>
        <Card.Body textAlign="center" py={10}>
          <Text color="fg.muted" mb={4}>
            No graph data available yet.
          </Text>
          <Text color="fg.subtle" fontSize="sm">
            Start a conversation to see entities and their relationships
            visualized here.
          </Text>
          {centerEntity && (
            <Button mt={4} size="sm" variant="outline" onClick={onClearCenter}>
              Back to the session view
            </Button>
          )}
        </Card.Body>
      </Card.Root>
    );
  }

  const nodeTypes = [...new Set(data.nodes.map((n) => n.type))];

  return (
    <VStack align="stretch" gap={6}>
      {/* Stats */}
      <SimpleGrid columns={{ base: 1, sm: 3 }} gap={4}>
        <StatCard label="Nodes" value={graph.nodes.length} tone="teal" />
        <StatCard
          label="Relationships"
          value={graph.links.length}
          tone="purple"
        />
        <StatCard label="Node types" value={nodeTypes.length} tone="blue" />
      </SimpleGrid>

      {/* Interactive graph */}
      <Box>
        {centerEntity && (
          <HStack justify="flex-end" mb={2}>
            <Button size="xs" variant="outline" onClick={onClearCenter}>
              Back to the session view
            </Button>
          </HStack>
        )}
        <MemoryGraph graph={graph} onSelectNode={onSelectNode} />
      </Box>

      {/* Node inventory, per type */}
      <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} gap={4}>
        {nodeTypes.map((type) => {
          const nodesOfType = data.nodes.filter((n) => n.type === type);
          return (
            <Card.Root key={type}>
              <Card.Header>
                <HStack>
                  <Badge>{type}</Badge>
                  <Text color="fg.muted" fontSize="sm">
                    ({nodesOfType.length})
                  </Text>
                </HStack>
              </Card.Header>
              <Card.Body maxH="200px" overflowY="auto">
                <VStack align="stretch" gap={1}>
                  {nodesOfType.slice(0, 10).map((node) => (
                    <Text key={node.id} fontSize="sm" lineClamp={1}>
                      {node.label}
                    </Text>
                  ))}
                  {nodesOfType.length > 10 && (
                    <Text fontSize="sm" color="fg.subtle">
                      +{nodesOfType.length - 10} more
                    </Text>
                  )}
                </VStack>
              </Card.Body>
            </Card.Root>
          );
        })}
      </SimpleGrid>

      {/* Info box */}
      <Card.Root bg="bg.subtle" borderColor="teal.muted">
        <Card.Body>
          <Heading size="sm" mb={2}>
            About the memory graph
          </Heading>
          <Text color="fg.muted" fontSize="sm">
            One graph, three memory layers: the messages of this conversation
            (short-term), the entities and preferences extracted from them
            (long-term), and the reasoning trace recorded for each turn
            (reasoning). Click any node to re-query the backend for its two-hop
            neighbourhood.
          </Text>
        </Card.Body>
      </Card.Root>
    </VStack>
  );
}

function StatCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <Card.Root>
      <Card.Body>
        <Text fontSize="2xl" fontWeight="bold" color={`${tone}.fg`}>
          {value}
        </Text>
        <Text color="fg.muted">{label}</Text>
      </Card.Body>
    </Card.Root>
  );
}
