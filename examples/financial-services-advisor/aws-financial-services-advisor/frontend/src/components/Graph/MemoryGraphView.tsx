import { Badge, Box, Flex, HStack, Heading, IconButton, Spinner, Text } from '@chakra-ui/react'
import type NVL from '@neo4j-nvl/base'
import type { Node, Relationship } from '@neo4j-nvl/base'
import { InteractiveNvlWrapper } from '@neo4j-nvl/react'
import { useQuery } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { LuMaximize, LuNetwork, LuRefreshCw } from 'react-icons/lu'
import { graphApi, normalizeNeighborNode, type GraphData } from '../../lib/api'
import { getNodeColor, nodeColors } from '../../theme'

const EMPTY_GRAPH: GraphData = { nodes: [], relationships: [] }

/**
 * The compliance graph, rendered with the Neo4j Visualization Library.
 *
 * The base scene comes from `GET /api/graph/memory`; double-clicking a node
 * fetches `GET /api/graph/neighbors/{id}` and merges the result into a local
 * `expanded` overlay, so the cached base scene stays untouched.
 */
export default function MemoryGraphView() {
  const [expanded, setExpanded] = useState<GraphData>(EMPTY_GRAPH)
  const [expandNote, setExpandNote] = useState<string | null>(null)
  // The interactive wrapper forwards the NVL instance itself.
  const nvlRef = useRef<NVL | null>(null)

  const {
    data: base,
    isPending,
    error,
    refetch,
  } = useQuery({
    queryKey: ['graph', 'memory'],
    queryFn: () => graphApi.getMemoryGraph(500),
  })

  const reload = useCallback(() => {
    setExpanded(EMPTY_GRAPH)
    setExpandNote(null)
    void refetch()
  }, [refetch])

  const graphData: GraphData | null = base
    ? {
        nodes: [...base.nodes, ...expanded.nodes],
        relationships: [...base.relationships, ...expanded.relationships],
      }
    : null

  const fetchNeighbors = useCallback(
    async (nodeId: string) => {
      try {
        const data = await graphApi.getNeighbors(nodeId, 1, 20)
        const neighbours = data.nodes.filter((n) => n.id && !n.isRoot)
        const edges = (data.edges ?? []).filter((e) => e.to)
        const knownIds = new Set((base?.nodes ?? []).map((n) => n.id))

        setExpanded((prev) => {
          const existingIds = new Set([...knownIds, ...prev.nodes.map((n) => n.id)])
          const existingRelIds = new Set(prev.relationships.map((r) => r.id))
          return {
            nodes: [
              ...prev.nodes,
              ...neighbours.filter((n) => !existingIds.has(n.id)).map(normalizeNeighborNode),
            ],
            relationships: [
              ...prev.relationships,
              ...edges
                .map((e, i) => ({
                  id: `expanded-${nodeId}-${i}`,
                  from: e.from || nodeId,
                  to: e.to,
                  type: e.relationship || 'RELATED',
                }))
                .filter((r) => !existingRelIds.has(r.id)),
            ],
          }
        })

        setExpandNote(
          neighbours.length > 0
            ? `Expanded ${nodeId}: ${neighbours.length} neighbour(s)`
            : `No neighbours found for ${nodeId}`,
        )
      } catch (err) {
        setExpandNote(
          `Could not expand ${nodeId}: ${err instanceof Error ? err.message : 'request failed'}`,
        )
      }
    },
    [base],
  )

  if (isPending) {
    return (
      <Flex h="calc(100dvh - 48px)" align="center" justify="center">
        <Spinner size="lg" colorPalette="brand" />
        <Text ml={3}>Loading graph...</Text>
      </Flex>
    )
  }

  if (error) {
    return (
      <Flex h="calc(100dvh - 48px)" align="center" justify="center" direction="column" gap={2}>
        <Text color="fg.error">
          {error instanceof Error ? error.message : 'Failed to load graph'}
        </Text>
        <Text fontSize="sm" color="fg.muted">
          Is the backend running, and has sample data been loaded (<code>make load-data</code>)?
        </Text>
      </Flex>
    )
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <Flex h="calc(100dvh - 48px)" align="center" justify="center" direction="column" gap={4}>
        <Box color="fg.subtle">
          <LuNetwork size={48} />
        </Box>
        <Text color="fg.muted">
          No graph data. Run <code>make load-data</code> first.
        </Text>
      </Flex>
    )
  }

  const nvlNodes: Node[] = graphData.nodes.map((n) => ({
    id: n.id,
    caption: n.label || n.id,
    color: getNodeColor(n.labels ?? []),
    size: n.labels?.includes('Customer') ? 30 : 20,
  }))

  const nvlRels: Relationship[] = graphData.relationships
    .filter((r) => r.from && r.to)
    .map((r) => ({
      id: r.id,
      from: r.from,
      to: r.to,
      caption: r.type,
    }))

  return (
    <Box h="calc(100dvh - 48px)">
      <Flex direction="column" h="full">
        <Flex justify="space-between" align="center" mb={2} px={2} gap={2} flexWrap="wrap">
          <HStack>
            <LuNetwork size={20} />
            <Heading size="md" fontFamily="heading">
              Context Graph
            </Heading>
            <Badge colorPalette="brand">{graphData.nodes.length} nodes</Badge>
            <Badge colorPalette="gray">{graphData.relationships.length} relationships</Badge>
          </HStack>
          <HStack>
            <Text fontSize="xs" color="fg.muted">
              {expandNote ?? 'Double-click a node to expand its neighbours'}
            </Text>
            <IconButton
              aria-label="Fit graph to screen"
              title="Fit to screen"
              size="sm"
              variant="ghost"
              onClick={() => nvlRef.current?.fit(nvlNodes.map((n) => n.id))}
            >
              <LuMaximize />
            </IconButton>
            <IconButton
              aria-label="Reload graph"
              title="Reload"
              size="sm"
              variant="ghost"
              onClick={reload}
            >
              <LuRefreshCw />
            </IconButton>
          </HStack>
        </Flex>

        <Flex mb={2} px={2} gap={2} flexWrap="wrap">
          {Object.entries(nodeColors).map(([label, color]) => (
            <Badge key={label} size="sm" style={{ borderLeft: `3px solid ${color}` }} pl={2}>
              {label}
            </Badge>
          ))}
        </Flex>

        <Box flex={1} borderWidth="1px" borderColor="border" borderRadius="md" overflow="hidden">
          <InteractiveNvlWrapper
            ref={nvlRef}
            nodes={nvlNodes}
            rels={nvlRels}
            nvlOptions={{
              // 'forceDirected' - not 'force-directed'; see the Layout union.
              layout: 'forceDirected',
              relationshipThreshold: 0.55,
            }}
            // Mouse callbacks live on `mouseEventCallbacks`; keys passed to
            // `nvlCallbacks` (lifecycle callbacks) are silently ignored.
            mouseEventCallbacks={{
              onNodeDoubleClick: (node: Node) => {
                if (node.id) void fetchNeighbors(node.id)
              },
            }}
          />
        </Box>
      </Flex>
    </Box>
  )
}
