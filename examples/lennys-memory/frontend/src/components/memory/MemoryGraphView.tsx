"use client";

import { useEffect, useState, useMemo, useCallback, useRef } from "react";
import {
  Box,
  VStack,
  HStack,
  Text,
  Button,
  Spinner,
  Badge,
  IconButton,
  Image,
  Link,
} from "@chakra-ui/react";
import { HiX, HiRefresh } from "react-icons/hi";
import { LuExpand, LuExternalLink, LuSparkles } from "react-icons/lu";
import dynamic from "next/dynamic";
import type NVL from "@neo4j-nvl/base";
import { api } from "@/lib/api";
import type { MemoryGraph, GraphNode, GraphRelationship } from "@/lib/types";

// Define types for NVL
interface NvlNode {
  id: string;
  caption?: string;
  size?: number;
  color?: string;
  selected?: boolean;
  data?: GraphNode;
}

interface NvlRelationship {
  id: string;
  from: string;
  to: string;
  caption?: string;
  selected?: boolean;
}

interface MouseEventCallbacks {
  onNodeClick?: (node: NvlNode) => void;
  onRelationshipClick?: (rel: NvlRelationship) => void;
  onPan?: boolean;
  onZoom?: boolean;
  onDrag?: boolean;
}

// Dynamically import NVL to avoid SSR issues
const InteractiveNvlWrapper = dynamic(
  () => import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <VStack height="100%" justifyContent="center" gap={4}>
        <Spinner size="xl" />
        <Text color="gray.600">Loading graph visualization...</Text>
      </VStack>
    ),
  },
);

interface MemoryGraphViewProps {
  isOpen: boolean;
  onClose: () => void;
  threadId?: string;
}

// Memory type classification
type MemoryType = "short-term" | "user-profile" | "reasoning";

interface MemoryTypeFilters {
  "short-term": boolean;
  "user-profile": boolean;
  reasoning: boolean;
}

// Node type colors
const NODE_COLORS: Record<string, string> = {
  Thread: "#4C8EDA",
  Conversation: "#4C8EDA",
  Message: "#57C7E3",
  ReasoningStep: "#8DCC93",
  ReasoningTrace: "#8DCC93",
  ToolCall: "#F79767",
  Tool: "#C990C0",
  UserPreference: "#FFC454",
  Preference: "#FFC454",
  PreferenceCategory: "#DA7194",
  Category: "#DA7194",
  Location: "#68BDF6",
  Person: "#DE9BF9",
  Organization: "#FB95AF",
  Topic: "#A4DD00",
  Entity: "#9B59B6",
  // Podcast-specific
  Episode: "#4C8EDA",
  Speaker: "#DE9BF9",
  Guest: "#DE9BF9",
};

// Memory type colors
const MEMORY_TYPE_COLORS = {
  "short-term": {
    bg: "rgba(76, 142, 218, 0.08)",
    border: "#4C8EDA",
    label: "Short-Term Memory",
    description: "Conversations & Messages",
  },
  "user-profile": {
    bg: "rgba(255, 196, 84, 0.08)",
    border: "#FFC454",
    label: "User Profile Memory",
    description: "Preferences, Categories & Entities",
  },
  reasoning: {
    bg: "rgba(141, 204, 147, 0.08)",
    border: "#8DCC93",
    label: "Reasoning Memory",
    description: "Reasoning, Tools & Actions",
  },
};

// Pure node helpers, kept at module scope so hooks can depend on them
// without re-creating them on every render.
const getNodeLabel = (node: GraphNode): string => {
  if (node.labels.includes("Thread")) return "Thread";
  if (node.labels.includes("Conversation")) return "Conversation";
  if (node.labels.includes("Message")) return "Message";
  if (node.labels.includes("ReasoningStep")) return "ReasoningStep";
  if (node.labels.includes("ReasoningTrace")) return "ReasoningTrace";
  if (node.labels.includes("ToolCall")) return "ToolCall";
  if (node.labels.includes("Tool")) return "Tool";
  if (node.labels.includes("UserPreference")) return "UserPreference";
  if (node.labels.includes("Preference")) return "Preference";
  if (node.labels.includes("PreferenceCategory")) return "PreferenceCategory";
  if (node.labels.includes("Category")) return "Category";
  if (node.labels.includes("Location")) return "Location";
  if (node.labels.includes("Person")) return "Person";
  if (node.labels.includes("Organization")) return "Organization";
  if (node.labels.includes("Topic")) return "Topic";
  if (node.labels.includes("Entity")) return "Entity";
  return node.labels[0] || "Unknown";
};

const getNodeMemoryType = (node: GraphNode): MemoryType => {
  const nodeType = getNodeLabel(node);

  if (
    nodeType === "Thread" ||
    nodeType === "Conversation" ||
    nodeType === "Message"
  ) {
    return "short-term";
  }

  if (
    nodeType === "UserPreference" ||
    nodeType === "Preference" ||
    nodeType === "PreferenceCategory" ||
    nodeType === "Category" ||
    nodeType === "Location" ||
    nodeType === "Person" ||
    nodeType === "Organization" ||
    nodeType === "Topic" ||
    nodeType === "Entity"
  ) {
    return "user-profile";
  }

  if (
    nodeType === "ReasoningStep" ||
    nodeType === "ReasoningTrace" ||
    nodeType === "ToolCall" ||
    nodeType === "Tool"
  ) {
    return "reasoning";
  }

  return "short-term";
};

const getNodeCaption = (node: GraphNode): string => {
  const nodeType = getNodeLabel(node);
  const props = node.properties;

  switch (nodeType) {
    case "Thread":
    case "Conversation": {
      const title = props.title as string | undefined;
      const sessionId = props.session_id as string | undefined;
      if (title) {
        return `${title.substring(0, 30)}${title.length > 30 ? "..." : ""}`;
      }
      if (sessionId) {
        // Format podcast session IDs nicely
        const formatted = sessionId
          .replace("lenny-podcast-", "")
          .replace(/-/g, " ");
        return (
          formatted.substring(0, 25) + (formatted.length > 25 ? "..." : "")
        );
      }
      return "Conversation";
    }
    case "Message": {
      const role = (props.role as string) || "unknown";
      const text = (props.text as string) || (props.content as string) || "";
      return `${role}: ${text.substring(0, 30)}${text.length > 30 ? "..." : ""}`;
    }
    case "ReasoningStep":
    case "ReasoningTrace": {
      const task = (props.task as string) || "";
      const reasoning = (props.reasoning_text as string) || "";
      if (task) {
        return `Task: ${task.substring(0, 25)}${task.length > 25 ? "..." : ""}`;
      }
      if (reasoning) {
        const stepNum = props.step_number || "?";
        return `Step ${stepNum}: ${reasoning.substring(0, 25)}${reasoning.length > 25 ? "..." : ""}`;
      }
      return "Reasoning";
    }
    case "ToolCall":
      return "Tool Call";
    case "Tool":
      return (props.name as string) || "Tool";
    case "UserPreference": {
      const pref = (props.preference as string) || "";
      return pref.substring(0, 35) + (pref.length > 35 ? "..." : "");
    }
    case "PreferenceCategory":
      return (props.name as string) || "Category";
    case "Location":
      return (props.name as string) || "Location";
    case "Person":
      return (props.name as string) || "Person";
    case "Organization":
      return (props.name as string) || "Organization";
    case "Topic":
      return (props.name as string) || "Topic";
    case "Entity":
      return (props.name as string) || "Entity";
    default:
      return (props.name as string) || (props.title as string) || nodeType;
  }
};

/**
 * Convert a guest name to a session_id format.
 * Handles Unicode characters and special formatting.
 */
const guestToSessionId = (guestName: string): string => {
  // Normalize unicode, convert to lowercase, replace spaces with hyphens
  const normalized = guestName
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "") // Remove diacritics
    .toLowerCase()
    .replace(/\s+/g, "-") // Replace spaces with hyphens
    .replace(/[^a-z0-9-]/g, "") // Remove non-alphanumeric except hyphens
    .replace(/-+/g, "-") // Collapse multiple hyphens
    .replace(/^-|-$/g, ""); // Trim leading/trailing hyphens
  return `lenny-podcast-${normalized}`;
};

/**
 * Extract episode session IDs from tool call results in thread messages.
 * Looks for session_id fields and episode_guest fields in tool results.
 */
const extractEpisodeSessionIds = (
  messages: Array<{ toolCalls?: Array<{ result?: unknown }> }>,
): string[] => {
  const sessionIds = new Set<string>();

  for (const message of messages) {
    if (!message.toolCalls) continue;

    for (const toolCall of message.toolCalls) {
      if (!toolCall.result) continue;

      // Recursively search for session_id and episode_guest fields in the result
      const findSessionIds = (obj: unknown): void => {
        if (!obj || typeof obj !== "object") return;

        if (Array.isArray(obj)) {
          for (const item of obj) {
            findSessionIds(item);
          }
        } else {
          const record = obj as Record<string, unknown>;
          // Check for session_id that matches podcast pattern
          if (
            typeof record.session_id === "string" &&
            record.session_id.startsWith("lenny-podcast-")
          ) {
            sessionIds.add(record.session_id);
          }
          // Also check for episode field (some tools return it differently)
          if (
            typeof record.episode === "string" &&
            record.episode.startsWith("lenny-podcast-")
          ) {
            sessionIds.add(record.episode);
          }
          // Check for episode_guest field and convert to session_id
          if (
            typeof record.episode_guest === "string" &&
            record.episode_guest.length > 0 &&
            record.episode_guest !== "Unknown"
          ) {
            sessionIds.add(guestToSessionId(record.episode_guest));
          }
          // Also check for guest field
          if (
            typeof record.guest === "string" &&
            record.guest.length > 0 &&
            record.guest !== "Unknown"
          ) {
            sessionIds.add(guestToSessionId(record.guest));
          }
          // Recurse into nested objects
          for (const value of Object.values(record)) {
            findSessionIds(value);
          }
        }
      };

      findSessionIds(toolCall.result);
    }
  }

  return Array.from(sessionIds);
};

export default function MemoryGraphView({
  isOpen,
  onClose,
  threadId,
}: MemoryGraphViewProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [graphData, setGraphData] = useState<MemoryGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedRelationship, setSelectedRelationship] =
    useState<GraphRelationship | null>(null);

  const [memoryFilters, setMemoryFilters] = useState<MemoryTypeFilters>({
    "short-term": true,
    "user-profile": true,
    reasoning: true,
  });

  // Double-click detection state
  const [lastClickedNodeId, setLastClickedNodeId] = useState<string | null>(
    null,
  );
  const [lastClickTime, setLastClickTime] = useState<number>(0);
  const DOUBLE_CLICK_DELAY = 300; // ms

  // Node expansion state
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set());
  const [expandingNode, setExpandingNode] = useState<string | null>(null);

  // NVL ref for zoom-to-fit
  const nvlRef = useRef<NVL | null>(null);
  const [hasInitialFit, setHasInitialFit] = useState(false);

  const loadGraphData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    setSelectedNode(null);
    setSelectedRelationship(null);
    setExpandedNodes(new Set());
    setExpandingNode(null);
    setHasInitialFit(false);
    try {
      // First, fetch the thread's messages to extract episode session IDs from tool calls
      let episodeSessionIds: string[] = [];
      if (threadId) {
        try {
          const threadData = await api.threads.get(threadId);
          if (threadData.messages) {
            episodeSessionIds = extractEpisodeSessionIds(threadData.messages);
          }
        } catch {
          // If fetching thread fails, continue without episode session IDs
          console.warn(
            "Could not fetch thread messages for episode extraction",
          );
        }
      }

      // Fetch graph data including episode conversations if any were found
      const data = await api.memory.getGraph(threadId, episodeSessionIds);
      setGraphData(data);

      if (data.nodes.length === 0) {
        setError(
          "No memory data available. Load some podcast transcripts to build your memory graph.",
        );
      }
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : "Failed to load memory graph";
      setError(errorMsg);
    } finally {
      setIsLoading(false);
    }
  }, [threadId]);

  // Handle node expansion (fetch neighbors)
  const handleNodeExpand = useCallback(
    async (nodeId: string) => {
      // Skip if already expanded or currently expanding
      if (expandedNodes.has(nodeId) || expandingNode) return;

      setExpandingNode(nodeId);

      try {
        const neighbors = await api.memory.getNodeNeighbors(nodeId, 1, 50);

        // Merge new nodes and relationships into existing graph
        setGraphData((prev) => {
          if (!prev) return prev;

          const existingNodeIds = new Set(prev.nodes.map((n) => n.id));
          const existingRelIds = new Set(prev.relationships.map((r) => r.id));

          const newNodes = neighbors.nodes.filter(
            (n) => !existingNodeIds.has(n.id),
          );
          const newRels = neighbors.relationships.filter(
            (r) => !existingRelIds.has(r.id),
          );

          if (newNodes.length === 0 && newRels.length === 0) {
            // No new data to add
            return prev;
          }

          return {
            nodes: [...prev.nodes, ...newNodes],
            relationships: [...prev.relationships, ...newRels],
          };
        });

        setExpandedNodes((prev) => {
          const newSet = new Set(prev);
          newSet.add(nodeId);
          return newSet;
        });
      } catch (err) {
        console.error("Failed to expand node:", err);
      } finally {
        setExpandingNode(null);
      }
    },
    [expandedNodes, expandingNode],
  );

  const formatPropertyValue = (value: unknown): string => {
    if (value === null || value === undefined) {
      return "null";
    }

    if (typeof value === "object" && value !== null) {
      const obj = value as Record<string, unknown>;
      if ("year" in obj && "month" in obj && "day" in obj) {
        try {
          const year =
            (obj.year as { low?: number })?.low ?? (obj.year as number);
          const month = String(
            (obj.month as { low?: number })?.low ?? (obj.month as number),
          ).padStart(2, "0");
          const day = String(
            (obj.day as { low?: number })?.low ?? (obj.day as number),
          ).padStart(2, "0");
          const hour = String(
            (obj.hour as { low?: number })?.low ?? (obj.hour as number) ?? 0,
          ).padStart(2, "0");
          const minute = String(
            (obj.minute as { low?: number })?.low ??
              (obj.minute as number) ??
              0,
          ).padStart(2, "0");
          const second = String(
            (obj.second as { low?: number })?.low ??
              (obj.second as number) ??
              0,
          ).padStart(2, "0");
          return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
        } catch {
          return JSON.stringify(value, null, 2);
        }
      }
      return JSON.stringify(value, null, 2);
    }

    if (typeof value === "string") {
      const dateMatch = value.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
      if (dateMatch) {
        try {
          const date = new Date(value);
          if (!isNaN(date.getTime())) {
            return date.toLocaleString("en-US", {
              year: "numeric",
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              hour12: false,
            });
          }
        } catch {
          // Not a valid date
        }
      }
    }

    return String(value);
  };

  const graphStats = useMemo(() => {
    if (!graphData) return { nodes: {}, relationships: {} };

    const nodeStats = graphData.nodes.reduce(
      (acc, node) => {
        node.labels.forEach((label) => {
          acc[label] = (acc[label] || 0) + 1;
        });
        return acc;
      },
      {} as Record<string, number>,
    );

    const relStats = graphData.relationships.reduce(
      (acc, rel) => {
        acc[rel.type] = (acc[rel.type] || 0) + 1;
        return acc;
      },
      {} as Record<string, number>,
    );

    return { nodes: nodeStats, relationships: relStats };
  }, [graphData]);

  const filteredNodes = useMemo(() => {
    if (!graphData) return [];

    return graphData.nodes.filter((node) => {
      const memoryType = getNodeMemoryType(node);
      return memoryFilters[memoryType];
    });
  }, [graphData, memoryFilters]);

  // Callback to perform initial zoom-to-fit when layout is done
  const handleLayoutDone = useCallback(() => {
    if (!hasInitialFit && nvlRef.current && filteredNodes.length > 0) {
      const nodeIds = filteredNodes.map((n) => n.id);
      nvlRef.current.fit(nodeIds);
      setHasInitialFit(true);
    }
  }, [hasInitialFit, filteredNodes]);

  const filteredRelationships = useMemo(() => {
    if (!graphData) return [];

    const visibleNodeIds = new Set(filteredNodes.map((n) => n.id));

    return graphData.relationships.filter((rel) => {
      return visibleNodeIds.has(rel.from) && visibleNodeIds.has(rel.to);
    });
  }, [graphData, filteredNodes]);

  const nvlNodes = useMemo<NvlNode[]>(() => {
    if (!filteredNodes) return [];

    return filteredNodes.map((node) => {
      const nodeType = getNodeLabel(node);
      const isSelected = selectedNode?.id === node.id;
      const isExpanded = expandedNodes.has(node.id);
      const isExpanding = expandingNode === node.id;
      const caption = getNodeCaption(node);

      let baseSize = 15;
      if (nodeType === "Thread" || nodeType === "Conversation") baseSize = 25;
      else if (nodeType === "Tool") baseSize = 22;
      else if (nodeType === "Message") baseSize = 18;
      else if (nodeType === "ReasoningStep" || nodeType === "ReasoningTrace")
        baseSize = 20;
      else if (nodeType === "ToolCall") baseSize = 14;
      else if (nodeType === "UserPreference" || nodeType === "Preference")
        baseSize = 16;
      else if (nodeType === "PreferenceCategory" || nodeType === "Category")
        baseSize = 20;
      else if (
        nodeType === "Location" ||
        nodeType === "Person" ||
        nodeType === "Organization" ||
        nodeType === "Topic" ||
        nodeType === "Entity"
      )
        baseSize = 18;

      // Visual feedback for expanded/expanding nodes
      let size = baseSize;
      if (isSelected) size += 5;
      if (isExpanded) size += 3;

      return {
        id: node.id,
        caption: isExpanding ? `${caption} ...` : caption,
        size,
        color: NODE_COLORS[nodeType] || "#CCCCCC",
        selected: isSelected || isExpanded,
        data: node,
      };
    });
  }, [filteredNodes, selectedNode, expandedNodes, expandingNode]);

  const nvlRelationships = useMemo<NvlRelationship[]>(() => {
    if (!filteredRelationships) return [];

    return filteredRelationships.map((rel) => {
      const isSelected = selectedRelationship?.id === rel.id;
      return {
        id: rel.id,
        from: rel.from,
        to: rel.to,
        caption: rel.type,
        selected: isSelected,
      };
    });
  }, [filteredRelationships, selectedRelationship]);

  const mouseEventCallbacks: MouseEventCallbacks = useMemo(
    () => ({
      onNodeClick: (node: NvlNode) => {
        const now = Date.now();
        const nodeId = node.id;
        const originalData = node.data;

        // Check for double-click
        if (
          nodeId === lastClickedNodeId &&
          now - lastClickTime < DOUBLE_CLICK_DELAY
        ) {
          // Double-click detected - expand node
          handleNodeExpand(nodeId);
          setLastClickedNodeId(null);
          setLastClickTime(0);
        } else {
          // Single click - select node
          if (originalData) {
            setSelectedNode(originalData);
            setSelectedRelationship(null);
          }
          setLastClickedNodeId(nodeId);
          setLastClickTime(now);
        }
      },
      onRelationshipClick: (rel: NvlRelationship) => {
        if (!graphData) return;

        const fullRel = graphData.relationships.find((r) => r.id === rel.id);
        if (fullRel) {
          setSelectedRelationship(fullRel);
          setSelectedNode(null);
        }
      },
      onPan: true,
      onZoom: true,
      onDrag: true,
    }),
    [graphData, lastClickedNodeId, lastClickTime, handleNodeExpand],
  );

  useEffect(() => {
    if (isOpen) {
      loadGraphData();
    }
  }, [isOpen, loadGraphData]);

  if (!isOpen) return null;

  return (
    <>
      {/* Overlay */}
      <Box
        position="fixed"
        top={0}
        left={0}
        right={0}
        bottom={0}
        bg="blackAlpha.600"
        zIndex={1000}
        onClick={onClose}
      />

      {/* Graph View Modal */}
      <Box
        position="fixed"
        top={{ base: 0, md: "50%" }}
        left={{ base: 0, md: "50%" }}
        transform={{ base: "none", md: "translate(-50%, -50%)" }}
        width={{ base: "100%", md: "90%", lg: "85%" }}
        height={{ base: "100%", md: "85%" }}
        bg="white"
        borderRadius={{ base: 0, md: "xl" }}
        boxShadow={{ base: "none", md: "2xl" }}
        zIndex={1001}
        display="flex"
        flexDirection="column"
        overflow="hidden"
      >
        {/* Header */}
        <VStack gap={0} borderBottom="1px solid" borderColor="gray.200">
          <HStack
            justifyContent="space-between"
            p={4}
            width="100%"
            bg="gray.50"
          >
            <VStack align="start" gap={0}>
              <Text fontSize="xl" fontWeight="bold">
                Memory Graph
              </Text>
              <Text fontSize="xs" color="gray.600">
                Podcast transcripts and conversation memory
              </Text>
              {graphData && (
                <HStack gap={2} mt={1}>
                  <Badge colorPalette="blue" fontSize="xs">
                    {filteredNodes.length} / {graphData.nodes.length} nodes
                  </Badge>
                  <Badge colorPalette="green" fontSize="xs">
                    {filteredRelationships.length} /{" "}
                    {graphData.relationships.length} relationships
                  </Badge>
                </HStack>
              )}
            </VStack>

            <HStack gap={2}>
              <IconButton
                aria-label="Refresh graph"
                size="sm"
                onClick={loadGraphData}
                disabled={isLoading}
              >
                <HiRefresh />
              </IconButton>
              <IconButton
                aria-label="Close graph view"
                size="sm"
                variant="ghost"
                onClick={onClose}
              >
                <HiX />
              </IconButton>
            </HStack>
          </HStack>

          {/* Memory Type Filter Toggles */}
          <HStack
            p={3}
            width="100%"
            bg="white"
            borderTop="1px solid"
            borderColor="gray.100"
            justifyContent="center"
            gap={3}
            flexWrap="wrap"
          >
            <Text fontSize="xs" fontWeight="semibold" color="gray.600">
              Filter by Memory Type:
            </Text>

            <Button
              size="sm"
              variant={memoryFilters["short-term"] ? "solid" : "outline"}
              colorPalette={memoryFilters["short-term"] ? "blue" : "gray"}
              onClick={() =>
                setMemoryFilters((prev) => ({
                  ...prev,
                  "short-term": !prev["short-term"],
                }))
              }
            >
              <Box
                w={3}
                h={3}
                borderRadius="full"
                bg={MEMORY_TYPE_COLORS["short-term"].border}
                mr={2}
              />
              Short-Term
            </Button>

            <Button
              size="sm"
              variant={memoryFilters["user-profile"] ? "solid" : "outline"}
              colorPalette={memoryFilters["user-profile"] ? "yellow" : "gray"}
              onClick={() =>
                setMemoryFilters((prev) => ({
                  ...prev,
                  "user-profile": !prev["user-profile"],
                }))
              }
            >
              <Box
                w={3}
                h={3}
                borderRadius="full"
                bg={MEMORY_TYPE_COLORS["user-profile"].border}
                mr={2}
              />
              User Profile
            </Button>

            <Button
              size="sm"
              variant={memoryFilters["reasoning"] ? "solid" : "outline"}
              colorPalette={memoryFilters["reasoning"] ? "green" : "gray"}
              onClick={() =>
                setMemoryFilters((prev) => ({
                  ...prev,
                  reasoning: !prev["reasoning"],
                }))
              }
            >
              <Box
                w={3}
                h={3}
                borderRadius="full"
                bg={MEMORY_TYPE_COLORS["reasoning"].border}
                mr={2}
              />
              Reasoning
            </Button>

            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const allEnabled = Object.values(memoryFilters).every((v) => v);
                setMemoryFilters({
                  "short-term": !allEnabled,
                  "user-profile": !allEnabled,
                  reasoning: !allEnabled,
                });
              }}
            >
              {Object.values(memoryFilters).every((v) => v)
                ? "Deselect All"
                : "Select All"}
            </Button>
          </HStack>
        </VStack>

        {/* Graph Container */}
        <Box flex={1} position="relative" bg="white">
          {isLoading ? (
            <VStack height="100%" justifyContent="center" gap={4}>
              <Spinner size="xl" />
              <Text color="gray.600">Loading memory graph...</Text>
            </VStack>
          ) : error ? (
            <VStack height="100%" justifyContent="center" gap={4} p={6}>
              <Text fontSize="lg" color="red.500" textAlign="center">
                {error}
              </Text>
              <Button colorPalette="blue" onClick={loadGraphData}>
                Try Again
              </Button>
            </VStack>
          ) : graphData && graphData.nodes.length > 0 ? (
            <Box height="100%" width="100%" position="relative">
              <Box height="100%" width="100%" style={{ touchAction: "none" }}>
                <InteractiveNvlWrapper
                  ref={nvlRef}
                  nodes={nvlNodes}
                  rels={nvlRelationships}
                  mouseEventCallbacks={mouseEventCallbacks}
                  nvlCallbacks={{
                    onLayoutDone: handleLayoutDone,
                  }}
                  nvlOptions={{
                    layout: "d3Force",
                    initialZoom: 1,
                    disableWebGL: false,
                    minZoom: 0.1,
                    maxZoom: 3,
                  }}
                />
              </Box>

              {/* Legend Panel */}
              {!selectedNode && !selectedRelationship && (
                <Box
                  position="absolute"
                  bottom={4}
                  left={4}
                  bg="white"
                  borderRadius="md"
                  boxShadow="lg"
                  p={3}
                  border="1px solid"
                  borderColor="gray.200"
                  zIndex={10}
                  pointerEvents="auto"
                  opacity={0.95}
                  maxW="320px"
                >
                  <Text fontSize="sm" fontWeight="bold" mb={2} color="gray.800">
                    Memory Types Legend
                  </Text>
                  <VStack align="stretch" gap={2.5} fontSize="xs">
                    {memoryFilters["short-term"] && (
                      <Box
                        p={2}
                        borderRadius="md"
                        bg={MEMORY_TYPE_COLORS["short-term"].bg}
                        border="1px solid"
                        borderColor={MEMORY_TYPE_COLORS["short-term"].border}
                      >
                        <Text
                          fontSize="xs"
                          fontWeight="bold"
                          color="gray.800"
                          mb={1}
                        >
                          {MEMORY_TYPE_COLORS["short-term"].label}
                        </Text>
                        <VStack align="stretch" gap={1}>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.Conversation}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Conversation</Text>
                          </HStack>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.Message}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Message</Text>
                          </HStack>
                        </VStack>
                      </Box>
                    )}

                    {memoryFilters["user-profile"] && (
                      <Box
                        p={2}
                        borderRadius="md"
                        bg={MEMORY_TYPE_COLORS["user-profile"].bg}
                        border="1px solid"
                        borderColor={MEMORY_TYPE_COLORS["user-profile"].border}
                      >
                        <Text
                          fontSize="xs"
                          fontWeight="bold"
                          color="gray.800"
                          mb={1}
                        >
                          {MEMORY_TYPE_COLORS["user-profile"].label}
                        </Text>
                        <VStack align="stretch" gap={1}>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.UserPreference}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Preference</Text>
                          </HStack>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.Entity}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Entity</Text>
                          </HStack>
                        </VStack>
                      </Box>
                    )}

                    {memoryFilters["reasoning"] && (
                      <Box
                        p={2}
                        borderRadius="md"
                        bg={MEMORY_TYPE_COLORS["reasoning"].bg}
                        border="1px solid"
                        borderColor={MEMORY_TYPE_COLORS["reasoning"].border}
                      >
                        <Text
                          fontSize="xs"
                          fontWeight="bold"
                          color="gray.800"
                          mb={1}
                        >
                          {MEMORY_TYPE_COLORS["reasoning"].label}
                        </Text>
                        <VStack align="stretch" gap={1}>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.ReasoningTrace}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Reasoning Trace</Text>
                          </HStack>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.ToolCall}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Tool Call</Text>
                          </HStack>
                          <HStack gap={2}>
                            <Box
                              w={3}
                              h={3}
                              borderRadius="full"
                              bg={NODE_COLORS.Tool}
                              flexShrink={0}
                            />
                            <Text color="gray.700">Tool</Text>
                          </HStack>
                        </VStack>
                      </Box>
                    )}
                  </VStack>
                </Box>
              )}

              {/* Property Panel for Selected Node or Relationship */}
              {(selectedNode || selectedRelationship) && (
                <Box
                  position="absolute"
                  top={4}
                  left={4}
                  bg="white"
                  borderRadius="md"
                  boxShadow="xl"
                  p={4}
                  w="320px"
                  maxH="500px"
                  border="1px solid"
                  borderColor="gray.200"
                  zIndex={10}
                  pointerEvents="auto"
                  display="flex"
                  flexDirection="column"
                  opacity={0.95}
                >
                  <HStack justifyContent="space-between" mb={3}>
                    <Text fontSize="md" fontWeight="bold" color="gray.800">
                      {selectedNode
                        ? "Node Properties"
                        : "Relationship Properties"}
                    </Text>
                    <IconButton
                      aria-label="Close properties"
                      size="xs"
                      variant="ghost"
                      onClick={() => {
                        setSelectedNode(null);
                        setSelectedRelationship(null);
                      }}
                    >
                      <HiX />
                    </IconButton>
                  </HStack>

                  <Box flex={1} overflowY="auto" pr={1}>
                    <VStack align="stretch" gap={3}>
                      {selectedNode ? (
                        <>
                          <Box>
                            <Text
                              fontSize="xs"
                              fontWeight="semibold"
                              color="gray.600"
                              mb={1}
                            >
                              Type
                            </Text>
                            <HStack gap={2} align="center">
                              <Box
                                w={6}
                                h={6}
                                borderRadius="full"
                                bg={
                                  NODE_COLORS[getNodeLabel(selectedNode)] ||
                                  "#CCCCCC"
                                }
                                border="1px solid"
                                borderColor="gray.300"
                              />
                              <Text
                                fontSize="sm"
                                color="gray.800"
                                fontWeight="medium"
                              >
                                {getNodeLabel(selectedNode)}
                              </Text>
                            </HStack>
                          </Box>

                          {/* Wikipedia Enrichment Section */}
                          {(typeof selectedNode.properties
                            .enriched_description === "string" ||
                            typeof selectedNode.properties.wikipedia_url ===
                              "string" ||
                            typeof selectedNode.properties.image_url ===
                              "string") && (
                            <Box
                              p={2.5}
                              bg="purple.50"
                              borderRadius="md"
                              border="1px solid"
                              borderColor="purple.100"
                            >
                              <HStack gap={1} mb={2}>
                                <LuSparkles
                                  size={12}
                                  color="var(--chakra-colors-purple-500)"
                                />
                                <Text
                                  fontSize="xs"
                                  fontWeight="semibold"
                                  color="purple.700"
                                >
                                  Wikipedia Enrichment
                                </Text>
                              </HStack>

                              {typeof selectedNode.properties.image_url ===
                                "string" && (
                                <Image
                                  src={selectedNode.properties.image_url}
                                  alt={String(
                                    selectedNode.properties.name || "Entity",
                                  )}
                                  w="100%"
                                  maxH="120px"
                                  objectFit="cover"
                                  borderRadius="md"
                                  mb={2}
                                />
                              )}

                              {typeof selectedNode.properties
                                .enriched_description === "string" && (
                                <Text
                                  fontSize="xs"
                                  color="gray.700"
                                  mb={2}
                                  lineHeight="tall"
                                >
                                  {selectedNode.properties.enriched_description.slice(
                                    0,
                                    300,
                                  )}
                                  {selectedNode.properties.enriched_description
                                    .length > 300 && "..."}
                                </Text>
                              )}

                              {typeof selectedNode.properties.wikipedia_url ===
                                "string" && (
                                <Link
                                  href={selectedNode.properties.wikipedia_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  fontSize="xs"
                                  color="blue.600"
                                  fontWeight="medium"
                                  display="inline-flex"
                                  alignItems="center"
                                  gap={1}
                                  _hover={{
                                    textDecoration: "underline",
                                  }}
                                >
                                  View on Wikipedia <LuExternalLink size={10} />
                                </Link>
                              )}
                            </Box>
                          )}

                          <Box>
                            <Text
                              fontSize="xs"
                              fontWeight="semibold"
                              color="gray.600"
                              mb={1}
                            >
                              Labels
                            </Text>
                            <HStack gap={1} flexWrap="wrap">
                              {selectedNode.labels.map((label) => (
                                <Badge
                                  key={label}
                                  colorPalette="purple"
                                  size="sm"
                                >
                                  {label}
                                </Badge>
                              ))}
                            </HStack>
                          </Box>

                          <Box>
                            <Text
                              fontSize="xs"
                              fontWeight="semibold"
                              color="gray.600"
                              mb={1}
                            >
                              Properties
                            </Text>
                            <VStack align="stretch" gap={2}>
                              {Object.entries(selectedNode.properties)
                                .filter(
                                  ([key]) =>
                                    ![
                                      "enriched_description",
                                      "wikipedia_url",
                                      "image_url",
                                      "enrichment_provider",
                                      "enriched_at",
                                      "enrichment_metadata",
                                      "wikidata_id",
                                    ].includes(key),
                                )
                                .map(([key, value]) => (
                                  <Box key={key}>
                                    <Text
                                      fontSize="xs"
                                      fontWeight="medium"
                                      color="gray.700"
                                    >
                                      {key}
                                    </Text>
                                    <Text
                                      fontSize="xs"
                                      color="gray.600"
                                      mt={0.5}
                                      wordBreak="break-word"
                                      whiteSpace="pre-wrap"
                                    >
                                      {formatPropertyValue(value)}
                                    </Text>
                                  </Box>
                                ))}
                            </VStack>
                          </Box>

                          {/* Expand Node Button */}
                          <Box
                            pt={2}
                            borderTop="1px solid"
                            borderColor="gray.200"
                          >
                            <Button
                              size="sm"
                              width="100%"
                              colorPalette={
                                expandedNodes.has(selectedNode.id)
                                  ? "green"
                                  : "blue"
                              }
                              variant={
                                expandedNodes.has(selectedNode.id)
                                  ? "outline"
                                  : "solid"
                              }
                              onClick={() => handleNodeExpand(selectedNode.id)}
                              disabled={
                                expandedNodes.has(selectedNode.id) ||
                                expandingNode !== null
                              }
                            >
                              {expandingNode === selectedNode.id ? (
                                <>
                                  <Spinner size="sm" mr={2} />
                                  Expanding...
                                </>
                              ) : expandedNodes.has(selectedNode.id) ? (
                                <>
                                  <LuExpand />
                                  <Text ml={2}>Already Expanded</Text>
                                </>
                              ) : (
                                <>
                                  <LuExpand />
                                  <Text ml={2}>Expand Neighbors</Text>
                                </>
                              )}
                            </Button>
                            <Text
                              fontSize="xs"
                              color="gray.500"
                              mt={1}
                              textAlign="center"
                            >
                              Double-click nodes to expand
                            </Text>
                          </Box>
                        </>
                      ) : selectedRelationship && graphData ? (
                        <>
                          <Box>
                            <Text
                              fontSize="xs"
                              fontWeight="semibold"
                              color="gray.600"
                              mb={1}
                            >
                              Type
                            </Text>
                            <Badge colorPalette="green" size="sm">
                              {selectedRelationship.type}
                            </Badge>
                          </Box>

                          <Box>
                            <Text
                              fontSize="xs"
                              fontWeight="semibold"
                              color="gray.600"
                              mb={1}
                            >
                              Connection
                            </Text>
                            <VStack align="stretch" gap={2}>
                              {(() => {
                                const fromNode = graphData.nodes.find(
                                  (n) => n.id === selectedRelationship.from,
                                );
                                const toNode = graphData.nodes.find(
                                  (n) => n.id === selectedRelationship.to,
                                );

                                return (
                                  <>
                                    <Box>
                                      <Text fontSize="xs" color="gray.500">
                                        From:
                                      </Text>
                                      <Text
                                        fontSize="xs"
                                        color="gray.700"
                                        fontWeight="medium"
                                        mt={0.5}
                                      >
                                        {fromNode?.labels.join(", ")}
                                      </Text>
                                      <Text
                                        fontSize="xs"
                                        color="gray.600"
                                        mt={0.5}
                                      >
                                        {(fromNode?.properties
                                          .name as string) ||
                                          (
                                            fromNode?.properties
                                              .preference as string
                                          )?.substring(0, 40) ||
                                          fromNode?.id}
                                      </Text>
                                    </Box>
                                    <Box>
                                      <Text fontSize="xs" color="gray.500">
                                        To:
                                      </Text>
                                      <Text
                                        fontSize="xs"
                                        color="gray.700"
                                        fontWeight="medium"
                                        mt={0.5}
                                      >
                                        {toNode?.labels.join(", ")}
                                      </Text>
                                      <Text
                                        fontSize="xs"
                                        color="gray.600"
                                        mt={0.5}
                                      >
                                        {(toNode?.properties.name as string) ||
                                          (
                                            toNode?.properties
                                              .preference as string
                                          )?.substring(0, 40) ||
                                          toNode?.id}
                                      </Text>
                                    </Box>
                                  </>
                                );
                              })()}
                            </VStack>
                          </Box>

                          {Object.keys(selectedRelationship.properties).length >
                            0 && (
                            <Box>
                              <Text
                                fontSize="xs"
                                fontWeight="semibold"
                                color="gray.600"
                                mb={1}
                              >
                                Properties
                              </Text>
                              <VStack align="stretch" gap={2}>
                                {Object.entries(
                                  selectedRelationship.properties,
                                ).map(([key, value]) => (
                                  <Box key={key}>
                                    <Text
                                      fontSize="xs"
                                      fontWeight="medium"
                                      color="gray.700"
                                    >
                                      {key}
                                    </Text>
                                    <Text
                                      fontSize="xs"
                                      color="gray.600"
                                      mt={0.5}
                                      wordBreak="break-word"
                                      whiteSpace="pre-wrap"
                                    >
                                      {formatPropertyValue(value)}
                                    </Text>
                                  </Box>
                                ))}
                              </VStack>
                            </Box>
                          )}
                        </>
                      ) : null}
                    </VStack>
                  </Box>
                </Box>
              )}

              {/* Scene Overview Panel */}
              <Box
                position="absolute"
                top={4}
                right={4}
                bg="white"
                borderRadius="md"
                boxShadow="lg"
                p={4}
                w="280px"
                maxH="400px"
                opacity={0.95}
                border="1px solid"
                borderColor="gray.200"
                zIndex={10}
                pointerEvents="auto"
                display="flex"
                flexDirection="column"
              >
                <VStack align="stretch" mb={3} gap={1}>
                  <Text fontSize="sm" fontWeight="bold" color="gray.700">
                    Scene Overview
                  </Text>
                  <Text fontSize="xs" color="gray.500">
                    {filteredNodes.length} nodes, {filteredRelationships.length}{" "}
                    rels
                  </Text>
                </VStack>

                <Box flex={1} overflowY="auto" pr={1}>
                  <VStack align="stretch" gap={3}>
                    <Box>
                      <Text
                        fontSize="xs"
                        fontWeight="semibold"
                        color="gray.600"
                        mb={1}
                      >
                        Nodes by Type
                      </Text>
                      <VStack align="stretch" gap={0.5}>
                        {Object.entries(graphStats.nodes).map(
                          ([label, count]) => {
                            const color = NODE_COLORS[label] || "#CCCCCC";
                            return (
                              <HStack
                                key={label}
                                justifyContent="space-between"
                                fontSize="xs"
                              >
                                <HStack gap={1.5}>
                                  <Box
                                    w={2.5}
                                    h={2.5}
                                    borderRadius="full"
                                    bg={color}
                                    flexShrink={0}
                                  />
                                  <Text color="gray.700">{label}</Text>
                                </HStack>
                                <Badge colorPalette="gray" size="sm">
                                  {count}
                                </Badge>
                              </HStack>
                            );
                          },
                        )}
                      </VStack>
                    </Box>

                    {Object.keys(graphStats.relationships).length > 0 && (
                      <Box>
                        <Text
                          fontSize="xs"
                          fontWeight="semibold"
                          color="gray.600"
                          mb={1}
                        >
                          Relationships
                        </Text>
                        <VStack align="stretch" gap={0.5}>
                          {Object.entries(graphStats.relationships).map(
                            ([type, count]) => (
                              <HStack
                                key={type}
                                justifyContent="space-between"
                                fontSize="xs"
                              >
                                <Text color="gray.700">{type}</Text>
                                <Badge colorPalette="gray" size="sm">
                                  {count}
                                </Badge>
                              </HStack>
                            ),
                          )}
                        </VStack>
                      </Box>
                    )}
                  </VStack>
                </Box>
              </Box>
            </Box>
          ) : null}
        </Box>

        {/* Footer */}
        {graphData && graphData.nodes.length > 0 && !isLoading && !error && (
          <HStack
            p={3}
            borderTop="1px solid"
            borderColor="gray.200"
            justifyContent="center"
            gap={4}
            flexWrap="wrap"
            bg="gray.50"
          >
            <Text fontSize="xs" color="gray.600">
              Click to select |{" "}
              <strong>Double-click to expand neighbors</strong> | Drag to pan |
              Scroll to zoom
            </Text>
          </HStack>
        )}
      </Box>
    </>
  );
}
