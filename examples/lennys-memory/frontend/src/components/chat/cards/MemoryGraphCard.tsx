"use client";

import { useState, useMemo } from "react";
import {
  Box,
  VStack,
  Text,
  Spinner,
  Portal,
  CloseButton,
  Flex,
  HStack,
  Badge,
} from "@chakra-ui/react";
import {
  DialogRoot,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogBody,
  DialogCloseTrigger,
  DialogBackdrop,
  DialogPositioner,
} from "@chakra-ui/react";
import dynamic from "next/dynamic";
import { BaseCard } from "./BaseCard";
import { LuBrain, LuMessageSquare, LuBox } from "react-icons/lu";
import type { ToolCall } from "@/lib/types";

// Types for the memory graph search result
interface MemoryGraphNode {
  id: string;
  label: string;
  type: string;
  properties?: Record<string, unknown>;
}

interface MemoryGraphRelationship {
  id: string;
  from: string;
  to: string;
  type: string;
}

export interface MemoryGraphSearchResult {
  query: string;
  nodes: MemoryGraphNode[];
  relationships: MemoryGraphRelationship[];
  summary: {
    messages_found: number;
    entities_found: number;
    relationships_found: number;
  };
  error?: string;
  message?: string;
}

interface MemoryGraphCardProps {
  toolCall: ToolCall;
  result: MemoryGraphSearchResult;
  isExpanded?: boolean;
  onExpand?: () => void;
  onCollapse?: () => void;
  compactHeight?: number;
}

// NVL types
interface NvlNode {
  id: string;
  caption?: string;
  size?: number;
  color?: string;
}

interface NvlRelationship {
  id: string;
  from: string;
  to: string;
  caption?: string;
}

// Dynamically import NVL
const InteractiveNvlWrapper = dynamic(
  () => import("@neo4j-nvl/react").then((mod) => mod.InteractiveNvlWrapper),
  {
    ssr: false,
    loading: () => (
      <VStack height="100%" justifyContent="center" gap={4}>
        <Spinner size="md" color="brand.500" />
        <Text color="fg.muted" fontSize="sm">
          Loading graph...
        </Text>
      </VStack>
    ),
  },
);

// Custom colors for memory graph - handles both uppercase and capitalized types
// Messages are distinct indigo, entities use type-specific colors
const memoryNodeColors: Record<string, string> = {
  // Message nodes - distinct indigo color
  Message: "#6366f1",
  MESSAGE: "#6366f1",
  // Entity types - both cases for flexibility
  Person: "#DA7194",
  PERSON: "#DA7194",
  Organization: "#F79767",
  ORGANIZATION: "#F79767",
  Location: "#68BDF6",
  LOCATION: "#68BDF6",
  Topic: "#A4DD00",
  TOPIC: "#A4DD00",
  Concept: "#A4DD00",
  CONCEPT: "#A4DD00",
  Event: "#C990C0",
  EVENT: "#C990C0",
  Object: "#57C7E3",
  OBJECT: "#57C7E3",
  Episode: "#C990C0",
  EPISODE: "#C990C0",
  Preference: "#F59E0B",
  PREFERENCE: "#F59E0B",
  // Entity as fallback for untyped entities
  Entity: "#8B5CF6",
  ENTITY: "#8B5CF6",
  // Default should never be gray - use purple as fallback
  default: "#8B5CF6",
};

// Get color for a node type with fallback
function getNodeColor(type: string): string {
  return (
    memoryNodeColors[type] ||
    memoryNodeColors[type.toUpperCase()] ||
    memoryNodeColors[
      type.charAt(0).toUpperCase() + type.slice(1).toLowerCase()
    ] ||
    memoryNodeColors.default
  );
}

export function MemoryGraphCard({
  toolCall,
  result,
  isExpanded = false,
  onExpand,
  onCollapse,
  compactHeight = 280,
}: MemoryGraphCardProps) {
  const [selectedNode, setSelectedNode] = useState<MemoryGraphNode | null>(
    null,
  );

  // Convert to NVL format
  const nvlNodes: NvlNode[] = useMemo(() => {
    return result.nodes.map((node) => ({
      id: node.id,
      caption: node.label,
      size:
        node.type === "Message" || node.type === "MESSAGE"
          ? isExpanded
            ? 30
            : 22
          : isExpanded
            ? 24
            : 18,
      color: getNodeColor(node.type),
    }));
  }, [result.nodes, isExpanded]);

  const nvlRels: NvlRelationship[] = useMemo(() => {
    return result.relationships.map((rel) => ({
      id: rel.id,
      from: rel.from,
      to: rel.to,
      caption: rel.type,
    }));
  }, [result.relationships]);

  // Get unique node types for legend
  const nodeTypes = useMemo(() => {
    const types = new Set(result.nodes.map((n) => n.type));
    return Array.from(types);
  }, [result.nodes]);

  if (result.error) {
    return (
      <BaseCard
        toolCall={toolCall}
        title="Memory Search"
        icon={<LuBrain size={14} />}
        colorPalette="red"
      >
        <Text color="red.500" fontSize="sm">
          {result.error}
        </Text>
      </BaseCard>
    );
  }

  const graphContent = (height: string) => (
    <Box height={height} width="100%" bg="white" position="relative">
      {result.nodes.length === 0 ? (
        <VStack height="100%" justify="center">
          <Text fontSize="sm" color="fg.muted">
            {result.message || `No results found for "${result.query}"`}
          </Text>
        </VStack>
      ) : (
        <>
          <InteractiveNvlWrapper
            nodes={nvlNodes}
            rels={nvlRels}
            mouseEventCallbacks={{
              onNodeClick: (node: NvlNode) => {
                const original = result.nodes.find((n) => n.id === node.id);
                if (original) setSelectedNode(original);
              },
              onPan: isExpanded,
              onZoom: isExpanded,
              onDrag: isExpanded,
            }}
            nvlOptions={{
              layout: "d3Force",
              initialZoom: isExpanded ? 1 : 0.75,
              disableWebGL: false,
            }}
          />

          {/* Legend */}
          <Box
            position="absolute"
            bottom={2}
            left={2}
            bg="whiteAlpha.900"
            p={1.5}
            borderRadius="md"
            borderWidth="1px"
            borderColor="border.subtle"
            boxShadow="sm"
          >
            <Flex wrap="wrap" gap={1.5}>
              {nodeTypes.map((type) => (
                <HStack key={type} gap={1}>
                  <Box
                    w={2.5}
                    h={2.5}
                    borderRadius="full"
                    bg={getNodeColor(type)}
                  />
                  <Text fontSize="xs">{type}</Text>
                </HStack>
              ))}
            </Flex>
          </Box>

          {/* Selected node panel (expanded mode) */}
          {isExpanded && selectedNode && (
            <Box
              position="absolute"
              top={4}
              right={4}
              bg="white"
              p={3}
              borderRadius="md"
              borderWidth="1px"
              borderColor="border.subtle"
              maxW="300px"
              maxH="400px"
              overflowY="auto"
            >
              <Flex align="center" gap={2} mb={2}>
                {selectedNode.type === "Message" ? (
                  <LuMessageSquare size={16} />
                ) : (
                  <LuBox size={16} />
                )}
                <Text fontSize="sm" fontWeight="bold">
                  {selectedNode.label}
                </Text>
              </Flex>
              <Badge
                size="sm"
                colorPalette={
                  selectedNode.type === "Message" ? "purple" : "blue"
                }
              >
                {selectedNode.type}
              </Badge>
              {selectedNode.properties && (
                <Box mt={2}>
                  {selectedNode.properties.content ? (
                    <Text fontSize="xs" color="fg.muted" mt={2}>
                      {String(selectedNode.properties.content)}
                    </Text>
                  ) : null}
                  {selectedNode.properties.speaker ? (
                    <Text fontSize="xs" mt={1}>
                      <strong>Speaker:</strong>{" "}
                      {String(selectedNode.properties.speaker)}
                    </Text>
                  ) : null}
                  {selectedNode.properties.episode ? (
                    <Text fontSize="xs" mt={1}>
                      <strong>Episode:</strong>{" "}
                      {String(selectedNode.properties.episode)}
                    </Text>
                  ) : null}
                  {selectedNode.properties.similarity !== undefined ? (
                    <Text fontSize="xs" mt={1}>
                      <strong>Relevance:</strong>{" "}
                      {Number(selectedNode.properties.similarity).toFixed(3)}
                    </Text>
                  ) : null}
                  {selectedNode.properties.description ? (
                    <Text fontSize="xs" mt={1} color="fg.muted">
                      {String(selectedNode.properties.description).slice(
                        0,
                        150,
                      )}
                      {String(selectedNode.properties.description).length > 150
                        ? "..."
                        : ""}
                    </Text>
                  ) : null}
                </Box>
              )}
            </Box>
          )}
        </>
      )}
    </Box>
  );

  const { summary } = result;
  const footerText = `${summary.messages_found} messages · ${summary.entities_found} entities · ${summary.relationships_found} connections`;

  // Fullscreen dialog
  if (isExpanded) {
    return (
      <DialogRoot open={true} size="cover" onOpenChange={() => onCollapse?.()}>
        <Portal>
          <DialogBackdrop />
          <DialogPositioner>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  <Flex align="center" gap={2}>
                    <LuBrain />
                    Memory Graph: &quot;{result.query}&quot;
                  </Flex>
                </DialogTitle>
                <DialogCloseTrigger asChild>
                  <CloseButton size="sm" />
                </DialogCloseTrigger>
              </DialogHeader>
              <DialogBody p={0} height="calc(100vh - 80px)">
                {graphContent("100%")}
              </DialogBody>
            </DialogContent>
          </DialogPositioner>
        </Portal>
      </DialogRoot>
    );
  }

  // Truncate query for title
  const truncatedQuery =
    result.query.length > 20 ? `${result.query.slice(0, 20)}...` : result.query;

  return (
    <BaseCard
      toolCall={toolCall}
      title={`Memory: "${truncatedQuery}"`}
      icon={<LuBrain size={14} />}
      colorPalette="purple"
      isExpanded={isExpanded}
      onExpand={onExpand}
      onCollapse={onCollapse}
      compactHeight={compactHeight}
      footer={footerText}
    >
      {graphContent(`${compactHeight}px`)}
    </BaseCard>
  );
}
