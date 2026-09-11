"use client";

import {
  Box,
  Stack,
  Text,
  Badge,
  Flex,
  Heading,
  Accordion,
  Span,
  Drawer,
  Portal,
  IconButton,
  useBreakpointValue,
} from "@chakra-ui/react";
import {
  LuBrain,
  LuWrench,
  LuBot,
  LuSearch,
  LuGlobe,
  LuSettings,
  LuX,
  LuLayoutGrid,
  LuMapPin,
  LuTable,
  LuChartBar,
  LuUser,
  LuNetwork,
  LuCode,
} from "react-icons/lu";
import { LiveMemory } from "./LiveMemory";
import { useMemoryContext } from "@/hooks/useMemoryContext";

// Agent tools - mirrors the 28 tools registered in
// backend/src/agent/agent.py (keep in sync if tools are added there).
const AGENT_TOOLS = {
  podcast: [
    {
      name: "search_podcast",
      description: "Search podcast transcripts for topics",
    },
    {
      name: "search_by_speaker",
      description: "Find what a specific speaker said",
    },
    { name: "search_episode", description: "Search within a specific episode" },
    { name: "list_episodes", description: "Get list of all podcast episodes" },
    { name: "list_speakers", description: "Get list of all speakers" },
    { name: "get_stats", description: "Get podcast data statistics" },
  ],
  entities: [
    {
      name: "search_entities",
      description: "Search for people, companies, topics",
    },
    {
      name: "get_entity_context",
      description: "Get detailed entity info with Wikipedia",
    },
    {
      name: "find_related_entities",
      description: "Find co-occurring entities",
    },
    { name: "get_top_entities", description: "Get most mentioned entities" },
    {
      name: "find_duplicates",
      description: "Find entities that may need merging",
    },
    {
      name: "get_entity_provenance",
      description: "See which messages an entity was extracted from",
    },
    {
      name: "check_enrichment",
      description: "Check or request Wikipedia enrichment",
    },
  ],
  locations: [
    {
      name: "search_locations",
      description: "Search for locations in podcasts",
    },
    { name: "find_locations_near", description: "Find nearby locations" },
    {
      name: "get_episode_locations",
      description: "Get locations for an episode",
    },
    {
      name: "find_location_path",
      description: "Find path between locations in graph",
    },
    { name: "get_location_clusters", description: "Analyze location clusters" },
    {
      name: "calculate_distances",
      description: "Calculate distances between locations",
    },
  ],
  memory: [
    {
      name: "memory_graph_search",
      description: "Vector search + graph traversal visualization",
    },
    {
      name: "get_user_preferences",
      description: "Get stored user preferences",
    },
    {
      name: "find_similar_queries",
      description: "Find similar past interactions",
    },
    {
      name: "learn_from_similar_task",
      description: "Learn from past reasoning traces",
    },
    {
      name: "get_tool_patterns",
      description: "Analyze tool usage patterns",
    },
    {
      name: "get_reasoning_history",
      description: "Read reasoning traces from this session",
    },
    {
      name: "get_conversation_context",
      description: "Recall earlier messages in the conversation",
    },
    {
      name: "list_podcast_sessions",
      description: "List episodes with message counts",
    },
    {
      name: "get_episode_summary",
      description: "Summarize an episode's topics and entities",
    },
  ],
};

// Tool call cards configuration
const TOOL_CALL_CARDS = [
  {
    name: "MapCard",
    icon: LuMapPin,
    colorPalette: "teal",
    description:
      "Interactive map showing locations mentioned in podcasts. Supports markers with subtype-based colors, path visualization between locations, and auto-calculated bounds.",
    triggeredBy: [
      "search_locations",
      "find_locations_near",
      "find_location_path",
      "get_location_clusters",
    ],
  },
  {
    name: "DataCard",
    icon: LuTable,
    colorPalette: "green",
    description:
      "Tabular display for search results with auto-detected columns. Shows podcast matches, episode lists, speaker info, and entity data with horizontal scroll and expandable rows.",
    triggeredBy: [
      "search_podcast",
      "search_by_speaker",
      "list_episodes",
      "list_speakers",
      "search_entities",
    ],
  },
  {
    name: "StatsCard",
    icon: LuChartBar,
    colorPalette: "amber",
    description:
      "Key metrics displayed in a colored grid layout. Shows podcast statistics, top entities, and memory stats with numeric formatting and color-coded categories.",
    triggeredBy: ["get_stats", "get_top_entities", "memory_stats"],
  },
  {
    name: "EntityCard",
    icon: LuUser,
    colorPalette: "pink",
    description:
      "Wikipedia-style knowledge panel for entities. Shows entity image, enriched description, type/subtype badges, podcast mentions, and related entities.",
    triggeredBy: ["get_entity_context"],
  },
  {
    name: "GraphCard",
    icon: LuNetwork,
    colorPalette: "purple",
    description:
      "Neo4j NVL graph visualization showing entity relationships. Displays nodes with type-specific colors, relationship labels, and interactive navigation.",
    triggeredBy: ["find_related_entities"],
  },
  {
    name: "MemoryGraphCard",
    icon: LuBrain,
    colorPalette: "purple",
    description:
      "Combined vector search and graph traversal visualization. Shows relevant messages and connected entities from the knowledge graph with similarity scores.",
    triggeredBy: ["memory_graph_search"],
  },
  {
    name: "RawJsonCard",
    icon: LuCode,
    colorPalette: "gray",
    description:
      "Fallback JSON display for unrecognized tool results. Shows raw data in a formatted, expandable code view for debugging and inspection.",
    triggeredBy: ["Any unmatched tool"],
  },
];

interface MemoryContextPanelProps {
  isVisible: boolean;
  onClose?: () => void;
  /** Conversation to show memory for; null before the first message. */
  threadId?: string | null;
  /** Bumped by useChat when a turn completes, to trigger a refetch. */
  memoryVersion?: number;
}

export function MemoryContextPanel({
  isVisible,
  onClose,
  threadId = null,
  memoryVersion = 0,
}: MemoryContextPanelProps) {
  // Detect mobile viewport - default to false during SSR to avoid hydration mismatch
  const isMobile = useBreakpointValue({ base: true, lg: false }) ?? false;
  const {
    context,
    isLoading: isLoadingMemory,
    error: memoryError,
  } = useMemoryContext(threadId, memoryVersion);

  if (!isVisible) return null;

  // Content to render (shared between mobile and desktop)
  const renderContent = () => (
    <Stack gap="4">
      {/* Live memory + static agent reference */}
      <Accordion.Root
        collapsible
        multiple
        size="sm"
        defaultValue={["memory", "capabilities"]}
      >
        {/* Stored memory for this thread (live, from the backend) */}
        <Accordion.Item value="memory">
          <Accordion.ItemTrigger>
            <Flex flex="1" alignItems="center" gap="2">
              <LuBrain size={12} />
              <Span fontSize="xs">Stored Memory</Span>
              <Badge size="sm" ml="auto">
                {context.entities.length + context.preferences.length}
              </Badge>
            </Flex>
            <Accordion.ItemIndicator />
          </Accordion.ItemTrigger>
          <Accordion.ItemContent>
            <Box py="2">
              <LiveMemory
                context={context}
                isLoading={isLoadingMemory}
                error={memoryError}
                hasThread={Boolean(threadId)}
              />
            </Box>
          </Accordion.ItemContent>
        </Accordion.Item>

        {/* Agent Capabilities */}
        <Accordion.Item value="capabilities">
          <Accordion.ItemTrigger>
            <Flex flex="1" alignItems="center" gap="2">
              <LuSettings size={12} />
              <Span fontSize="xs">Agent Capabilities</Span>
            </Flex>
            <Accordion.ItemIndicator />
          </Accordion.ItemTrigger>
          <Accordion.ItemContent>
            <Stack gap="2" py="2">
              <Box p="2" bg="green.subtle" borderRadius="md">
                <Text fontSize="xs" fontWeight="medium" color="green.700">
                  Multi-step Reasoning
                </Text>
                <Text fontSize="xs" color="green.600">
                  Plans and executes complex queries step by step
                </Text>
              </Box>
              <Box p="2" bg="blue.subtle" borderRadius="md">
                <Text fontSize="xs" fontWeight="medium" color="blue.700">
                  Conversation Memory
                </Text>
                <Text fontSize="xs" color="blue.600">
                  Maintains context across messages in the thread
                </Text>
              </Box>
              <Box p="2" bg="purple.subtle" borderRadius="md">
                <Text fontSize="xs" fontWeight="medium" color="purple.700">
                  Preference Learning
                </Text>
                <Text fontSize="xs" color="purple.600">
                  Adapts responses based on your stored preferences
                </Text>
              </Box>
              <Box p="2" bg="orange.subtle" borderRadius="md">
                <Text fontSize="xs" fontWeight="medium" color="orange.700">
                  Knowledge Graph
                </Text>
                <Text fontSize="xs" color="orange.600">
                  Queries entities and relationships in Neo4j
                </Text>
              </Box>
            </Stack>
          </Accordion.ItemContent>
        </Accordion.Item>

        {/* Available Tools */}
        <Accordion.Item value="tools">
          <Accordion.ItemTrigger>
            <Flex flex="1" alignItems="center" gap="2">
              <LuWrench size={12} />
              <Span fontSize="xs">Available Tools</Span>
              <Badge size="sm" ml="auto">
                {Object.values(AGENT_TOOLS).flat().length}
              </Badge>
            </Flex>
            <Accordion.ItemIndicator />
          </Accordion.ItemTrigger>
          <Accordion.ItemContent>
            <Stack gap="3" py="2">
              {/* Podcast Tools */}
              <Box>
                <Flex alignItems="center" gap="1" mb="1">
                  <LuSearch size={10} />
                  <Text fontSize="xs" fontWeight="medium" color="fg.muted">
                    Podcast Search
                  </Text>
                </Flex>
                <Stack gap="0.5">
                  {AGENT_TOOLS.podcast.map((tool) => (
                    <Text key={tool.name} fontSize="xs" color="fg.muted" pl="3">
                      • {tool.description}
                    </Text>
                  ))}
                </Stack>
              </Box>

              {/* Entity Tools */}
              <Box>
                <Flex alignItems="center" gap="1" mb="1">
                  <LuUser size={10} />
                  <Text fontSize="xs" fontWeight="medium" color="fg.muted">
                    Entity Queries
                  </Text>
                </Flex>
                <Stack gap="0.5">
                  {AGENT_TOOLS.entities.map((tool) => (
                    <Text key={tool.name} fontSize="xs" color="fg.muted" pl="3">
                      • {tool.description}
                    </Text>
                  ))}
                </Stack>
              </Box>

              {/* Location Tools */}
              <Box>
                <Flex alignItems="center" gap="1" mb="1">
                  <LuGlobe size={10} />
                  <Text fontSize="xs" fontWeight="medium" color="fg.muted">
                    Location Analysis
                  </Text>
                </Flex>
                <Stack gap="0.5">
                  {AGENT_TOOLS.locations.map((tool) => (
                    <Text key={tool.name} fontSize="xs" color="fg.muted" pl="3">
                      • {tool.description}
                    </Text>
                  ))}
                </Stack>
              </Box>

              {/* Memory Tools */}
              <Box>
                <Flex alignItems="center" gap="1" mb="1">
                  <LuBrain size={10} />
                  <Text fontSize="xs" fontWeight="medium" color="fg.muted">
                    Memory & Preferences
                  </Text>
                </Flex>
                <Stack gap="0.5">
                  {AGENT_TOOLS.memory.map((tool) => (
                    <Text key={tool.name} fontSize="xs" color="fg.muted" pl="3">
                      • {tool.description}
                    </Text>
                  ))}
                </Stack>
              </Box>
            </Stack>
          </Accordion.ItemContent>
        </Accordion.Item>

        {/* Tool Call Cards */}
        <Accordion.Item value="cards">
          <Accordion.ItemTrigger>
            <Flex flex="1" alignItems="center" gap="2">
              <LuLayoutGrid size={12} />
              <Span fontSize="xs">Tool Call Cards</Span>
              <Badge size="sm" ml="auto">
                {TOOL_CALL_CARDS.length}
              </Badge>
            </Flex>
            <Accordion.ItemIndicator />
          </Accordion.ItemTrigger>
          <Accordion.ItemContent>
            <Stack gap="3" py="2">
              <Text fontSize="xs" color="fg.muted">
                Custom visualization cards for tool results
              </Text>

              {TOOL_CALL_CARDS.map((card) => {
                const IconComponent = card.icon;
                return (
                  <Box
                    key={card.name}
                    p="3"
                    bg={`${card.colorPalette}.subtle`}
                    borderRadius="md"
                    borderWidth="1px"
                    borderColor={`${card.colorPalette}.muted`}
                  >
                    <Flex alignItems="center" gap="2" mb="1">
                      <Box color={`${card.colorPalette}.fg`}>
                        <IconComponent size={14} />
                      </Box>
                      <Text
                        fontSize="xs"
                        fontWeight="semibold"
                        color={`${card.colorPalette}.fg`}
                      >
                        {card.name}
                      </Text>
                    </Flex>
                    <Text fontSize="xs" color="fg.muted" mb="2">
                      {card.description}
                    </Text>
                    <Flex gap="1" flexWrap="wrap">
                      {card.triggeredBy.map((tool) => (
                        <Badge
                          key={tool}
                          size="xs"
                          variant="subtle"
                          colorPalette={card.colorPalette}
                        >
                          {tool}
                        </Badge>
                      ))}
                    </Flex>
                  </Box>
                );
              })}
            </Stack>
          </Accordion.ItemContent>
        </Accordion.Item>
      </Accordion.Root>
    </Stack>
  );

  // Mobile: Bottom sheet drawer
  if (isMobile) {
    return (
      <Drawer.Root
        open={isVisible}
        onOpenChange={(e) => !e.open && onClose?.()}
        placement="bottom"
      >
        <Portal>
          <Drawer.Backdrop />
          <Drawer.Positioner>
            <Drawer.Content borderTopRadius="xl" maxH="70vh">
              <Drawer.Header borderBottomWidth="1px" py="3">
                <Flex
                  alignItems="center"
                  justifyContent="space-between"
                  w="full"
                >
                  <Flex alignItems="center" gap="2">
                    <LuBot size={20} />
                    <Heading size="sm">Memory &amp; Agent</Heading>
                  </Flex>
                  <IconButton
                    aria-label="Close"
                    variant="ghost"
                    size="sm"
                    onClick={() => onClose?.()}
                  >
                    <LuX />
                  </IconButton>
                </Flex>
              </Drawer.Header>
              <Drawer.Body overflowY="auto" py="4">
                {renderContent()}
              </Drawer.Body>
            </Drawer.Content>
          </Drawer.Positioner>
        </Portal>
      </Drawer.Root>
    );
  }

  // Desktop: Side panel
  return (
    <Box
      w="300px"
      borderLeftWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
      p="4"
      overflowY="auto"
      hideBelow="lg"
    >
      {/* Header */}
      <Flex alignItems="center" gap="2" mb="4">
        <LuBot size={20} />
        <Heading size="sm">Memory &amp; Agent</Heading>
      </Flex>
      {renderContent()}
    </Box>
  );
}
