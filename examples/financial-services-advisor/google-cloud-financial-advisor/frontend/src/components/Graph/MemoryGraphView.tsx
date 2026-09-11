import { useCallback, useMemo, useRef, useState, type ComponentRef } from "react";
import {
  Badge,
  Box,
  Button,
  Flex,
  HStack,
  Heading,
  Code,
  Spinner,
  Switch,
  Text,
  VStack,
} from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { InteractiveNvlWrapper } from "@neo4j-nvl/react";
import type { Node, Relationship } from "@neo4j-nvl/base";
import { LuNetwork, LuRefreshCw, LuWrench, LuX } from "react-icons/lu";
import {
  getEntityAuditTrail,
  getGraphNeighbors,
  getMemoryGraph,
  type GraphPayload,
} from "../../lib/api";
import {
  categorizeNode,
  countCategories,
  filterGraph,
  nodeColor,
  NODE_COLORS,
  type GraphCategory,
} from "../../lib/graph";
import { useChatSession } from "../../lib/session";

const CATEGORY_LABELS: Array<{
  key: GraphCategory;
  label: string;
  hint: string;
  colorPalette: string;
}> = [
  {
    key: "domain",
    label: "Domain",
    hint: "Customers, transactions, alerts, sanctions — this app's own data",
    colorPalette: "blue",
  },
  {
    key: "shortTerm",
    label: "Short-term",
    hint: "(:Conversation)-[:HAS_MESSAGE]->(:Message)",
    colorPalette: "green",
  },
  {
    key: "longTerm",
    label: "Long-term",
    hint: "(:Entity), (:Preference), (:Fact) extracted from conversations",
    colorPalette: "purple",
  },
  {
    key: "reasoning",
    label: "Reasoning",
    hint: "(:ReasoningTrace)-[:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)",
    colorPalette: "orange",
  },
];

const EMPTY_GRAPH: GraphPayload = { nodes: [], relationships: [] };

export default function MemoryGraphView() {
  const { sessionId } = useChatSession();
  const [scopeToSession, setScopeToSession] = useState(false);
  const [enabled, setEnabled] = useState<Record<GraphCategory, boolean>>({
    domain: true,
    shortTerm: true,
    longTerm: true,
    reasoning: true,
  });
  // Nodes pulled in by double-click expansion, merged on top of the query data.
  const [expansion, setExpansion] = useState<GraphPayload>(EMPTY_GRAPH);
  // The entity whose reasoning audit trail is open in the side panel.
  const [auditEntity, setAuditEntity] = useState<string | null>(null);
  const nvlRef = useRef<ComponentRef<typeof InteractiveNvlWrapper>>(null);

  const scopedSession = scopeToSession ? (sessionId ?? undefined) : undefined;

  const {
    data,
    error,
    isPending,
    isFetching,
    refetch: refetchGraph,
  } = useQuery({
    queryKey: ["memory-graph", scopedSession ?? "all"],
    queryFn: () => getMemoryGraph({ sessionId: scopedSession, limit: 500 }),
  });

  // "Which reasoning steps touched this entity, and via which tool?"
  const auditQuery = useQuery({
    queryKey: ["entity-audit", auditEntity],
    queryFn: () => getEntityAuditTrail(auditEntity!),
    enabled: !!auditEntity,
    staleTime: Infinity,
  });

  const refresh = useCallback(() => {
    setExpansion(EMPTY_GRAPH);
    setAuditEntity(null);
    void refetchGraph();
  }, [refetchGraph]);

  const merged = useMemo<GraphPayload>(() => {
    const base = data ?? EMPTY_GRAPH;
    if (expansion.nodes.length === 0 && expansion.relationships.length === 0) {
      return base;
    }
    const ids = new Set(base.nodes.map((n) => n.id));
    const relIds = new Set(base.relationships.map((r) => r.id));
    return {
      nodes: [...base.nodes, ...expansion.nodes.filter((n) => !ids.has(n.id))],
      relationships: [
        ...base.relationships,
        ...expansion.relationships.filter((r) => !relIds.has(r.id)),
      ],
    };
  }, [data, expansion]);

  const counts = useMemo(() => countCategories(merged.nodes), [merged.nodes]);
  const visible = useMemo(() => filterGraph(merged, enabled), [merged, enabled]);

  const expandNode = useCallback(async (nodeId: string) => {
    try {
      const payload = await getGraphNeighbors(nodeId, { depth: 1, limit: 20 });
      setExpansion((prev) => ({
        nodes: [
          ...prev.nodes,
          ...payload.nodes.map((n) => ({
            id: n.id,
            label: n.label ?? n.id,
            labels: [n.type || "Unknown"],
            properties: { ...n },
          })),
        ],
        relationships: [
          ...prev.relationships,
          ...payload.edges.map((e, i) => ({
            id: `expanded-${nodeId}-${i}`,
            from: e.from || nodeId,
            to: e.to,
            type: e.relationship || "RELATED",
          })),
        ],
      }));
    } catch {
      // Expansion is a convenience; keep the current view on failure.
    }
  }, []);

  const nvlNodes: Node[] = useMemo(
    () =>
      visible.nodes.map((n) => ({
        id: n.id,
        caption: n.label ?? n.id,
        color: nodeColor(n.labels ?? []),
        size: n.labels?.includes("Customer") ? 30 : 20,
      })),
    [visible.nodes],
  );

  const nvlRels: Relationship[] = useMemo(
    () =>
      visible.relationships.map((r) => ({
        id: r.id,
        from: r.from,
        to: r.to,
        caption: r.type,
      })),
    [visible.relationships],
  );

  const legendLabels = useMemo(() => {
    const present = new Set<string>();
    for (const node of visible.nodes) {
      for (const label of node.labels ?? []) {
        if (NODE_COLORS[label]) present.add(label);
      }
    }
    return [...present].sort();
  }, [visible.nodes]);

  const memoryNodeCount =
    counts.shortTerm + counts.longTerm + counts.reasoning;

  return (
    <Box h="calc(100vh - 48px)">
      <Flex direction="column" h="full" gap={2}>
        <Flex justify="space-between" align="center" px={2} wrap="wrap" gap={2}>
          <HStack>
            <LuNetwork size={20} />
            <Heading size="md">Context Graph</Heading>
            <Badge colorPalette="brand">{visible.nodes.length} nodes</Badge>
            <Badge colorPalette="gray">
              {visible.relationships.length} relationships
            </Badge>
            {isFetching && !isPending && <Spinner size="xs" />}
          </HStack>
          <HStack gap={3}>
            <Switch.Root
              size="sm"
              checked={scopeToSession}
              onCheckedChange={(e) => {
                setExpansion(EMPTY_GRAPH);
                setScopeToSession(e.checked);
              }}
              disabled={!sessionId}
            >
              <Switch.HiddenInput />
              <Switch.Control />
              <Switch.Label>
                {sessionId
                  ? "This conversation only"
                  : "This conversation only (start a chat first)"}
              </Switch.Label>
            </Switch.Root>
            <Text fontSize="xs" color="fg.muted">
              Click a memory entity for its audit trail · double-click to expand
            </Text>
            <Button size="xs" variant="outline" onClick={refresh}>
              <LuRefreshCw size={14} /> Refresh
            </Button>
          </HStack>
        </Flex>

        {/* Memory-layer filters */}
        <Flex px={2} gap={4} wrap="wrap" align="center">
          {CATEGORY_LABELS.map((category) => (
            <Switch.Root
              key={category.key}
              size="sm"
              colorPalette={category.colorPalette}
              checked={enabled[category.key]}
              onCheckedChange={(e) =>
                setEnabled((prev) => ({ ...prev, [category.key]: e.checked }))
              }
              title={category.hint}
            >
              <Switch.HiddenInput />
              <Switch.Control />
              <Switch.Label>
                {category.label}{" "}
                <Text as="span" color="fg.muted">
                  ({counts[category.key]})
                </Text>
              </Switch.Label>
            </Switch.Root>
          ))}
        </Flex>

        {legendLabels.length > 0 && (
          <Flex px={2} gap={2} wrap="wrap">
            {legendLabels.map((label) => (
              <Badge
                key={label}
                size="sm"
                style={{ borderLeft: `3px solid ${NODE_COLORS[label]}` }}
                pl={2}
              >
                {label}
              </Badge>
            ))}
          </Flex>
        )}

        {isPending ? (
          <Flex flex={1} align="center" justify="center">
            <Spinner size="lg" />
            <Text ml={3}>Loading graph…</Text>
          </Flex>
        ) : error ? (
          <Flex flex={1} align="center" justify="center" direction="column">
            <Text color="red.500">
              {error instanceof Error ? error.message : "Failed to load graph"}
            </Text>
            <Text fontSize="sm" color="fg.muted" mt={2}>
              Is the backend running, and has sample data been loaded
              (`make load-data`)?
            </Text>
          </Flex>
        ) : visible.nodes.length === 0 ? (
          <Flex flex={1} align="center" justify="center" direction="column">
            <LuNetwork size={48} />
            <Text mt={4} color="fg.muted">
              No nodes for this scope. Load sample data with `make load-data`,
              or turn a filter back on.
            </Text>
          </Flex>
        ) : (
          <Flex flex={1} gap={2} minH={0}>
            <Box
              flex={1}
              border="1px solid"
              borderColor="border.subtle"
              borderRadius="md"
              overflow="hidden"
            >
              <InteractiveNvlWrapper
                ref={nvlRef}
                nodes={nvlNodes}
                rels={nvlRels}
                nvlOptions={{
                  layout: "forceDirected",
                  relationshipThreshold: 0.55,
                }}
                mouseEventCallbacks={{
                  // Single click on a long-term memory node opens its audit
                  // trail; double click expands neighbours.
                  onNodeClick: (node: Node) => {
                    const hit = visible.nodes.find((n) => n.id === node.id);
                    if (hit && categorizeNode(hit.labels ?? []) === "longTerm") {
                      setAuditEntity(hit.label ?? hit.id);
                    }
                  },
                  onNodeDoubleClick: (node: Node) => {
                    if (node.id) void expandNode(node.id);
                  },
                  onZoom: true,
                  onPan: true,
                  onDrag: true,
                }}
              />
            </Box>

            {auditEntity && (
              <Box
                w="320px"
                border="1px solid"
                borderColor="border.subtle"
                borderRadius="md"
                p={3}
                overflowY="auto"
                bg="bg.panel"
              >
                <Flex justify="space-between" align="start" mb={2}>
                  <Box>
                    <Text fontSize="sm" fontWeight="semibold">
                      {auditEntity}
                    </Text>
                    <Text fontSize="xs" color="fg.muted">
                      Reasoning steps that touched this entity
                    </Text>
                  </Box>
                  <Box
                    as="button"
                    aria-label="Close audit trail"
                    color="fg.muted"
                    onClick={() => setAuditEntity(null)}
                  >
                    <LuX size={14} />
                  </Box>
                </Flex>

                {auditQuery.isPending ? (
                  <Spinner size="sm" />
                ) : auditQuery.data && auditQuery.data.total > 0 ? (
                  <VStack align="stretch" gap={3}>
                    {auditQuery.data.steps.map((step, i) => (
                      <Box key={step.step_id ?? i}>
                        <Text fontSize="xs" color="fg.muted" lineClamp={2}>
                          {step.task ?? "(no task recorded)"}
                        </Text>
                        <Text fontSize="sm">
                          {step.action ?? step.thought ?? "step"}
                        </Text>
                        {step.tools.length > 0 && (
                          <Flex gap={1} mt={1} wrap="wrap" align="center">
                            <LuWrench size={10} />
                            {step.tools.map((tool) => (
                              <Code key={tool} size="sm" variant="subtle">
                                {tool}
                              </Code>
                            ))}
                          </Flex>
                        )}
                      </Box>
                    ))}
                  </VStack>
                ) : (
                  <Text fontSize="xs" color="fg.muted">
                    No <Code size="sm">TOUCHED</Code> edges for this entity yet.
                    They are written when the agents record a tool call with{" "}
                    <Code size="sm">touched_entities=</Code>.
                  </Text>
                )}
              </Box>
            )}
          </Flex>
        )}

        {!isPending && !error && memoryNodeCount === 0 && (
          <Text px={2} pb={1} fontSize="xs" color="fg.muted">
            No memory nodes in this payload yet — run a chat turn in the AI
            Financial Advisor, then refresh. Short-term, long-term and reasoning
            nodes are written by neo4j-agent-memory as the agents work.
          </Text>
        )}
      </Flex>
    </Box>
  );
}
