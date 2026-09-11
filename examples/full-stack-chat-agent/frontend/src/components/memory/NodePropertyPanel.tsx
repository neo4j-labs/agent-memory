"use client";

import {
  Badge,
  Box,
  Button,
  Flex,
  IconButton,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { LuExpand, LuX } from "react-icons/lu";
import type { GraphNode, GraphRelationship } from "@/lib/types";
import { formatPropertyValue, getNodeColor, getNodeLabel } from "./graph";

interface NodePropertyPanelProps {
  node: GraphNode | null;
  relationship: GraphRelationship | null;
  /** Used to name the endpoints of a selected relationship. */
  nodesById: ReadonlyMap<string, GraphNode>;
  isExpanded: boolean;
  isExpanding: boolean;
  expandDisabled: boolean;
  onExpand: (nodeId: string) => void;
  onClose: () => void;
}

function PropertyList({ properties }: { properties: Record<string, unknown> }) {
  return (
    <Stack gap="2">
      {Object.entries(properties).map(([key, value]) => (
        <Box key={key}>
          <Text fontSize="xs" fontWeight="medium">
            {key}
          </Text>
          <Text
            fontSize="xs"
            color="fg.muted"
            mt="0.5"
            wordBreak="break-word"
            whiteSpace="pre-wrap"
          >
            {formatPropertyValue(value)}
          </Text>
        </Box>
      ))}
    </Stack>
  );
}

function endpointCaption(node: GraphNode | undefined): string {
  if (!node) return "(not loaded)";
  const props = node.properties;
  const name = props.name ?? props.preference ?? props.title;
  return typeof name === "string" ? name.slice(0, 40) : node.id;
}

/**
 * Inspector for the selected node or relationship, plus the "expand
 * neighbours" action (the button form of double-clicking a node).
 */
export function NodePropertyPanel({
  node,
  relationship,
  nodesById,
  isExpanded,
  isExpanding,
  expandDisabled,
  onExpand,
  onClose,
}: NodePropertyPanelProps) {
  if (!node && !relationship) return null;

  return (
    <Box
      position="absolute"
      top="4"
      left="4"
      zIndex="10"
      w="320px"
      maxH="min(500px, 70%)"
      p="4"
      bg="bg.panel"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      boxShadow="xl"
      display="flex"
      flexDirection="column"
    >
      <Flex justifyContent="space-between" alignItems="center" mb="3">
        <Text fontSize="md" fontWeight="bold">
          {node ? "Node properties" : "Relationship properties"}
        </Text>
        <IconButton
          aria-label="Close properties"
          size="xs"
          variant="ghost"
          onClick={onClose}
        >
          <LuX />
        </IconButton>
      </Flex>

      <Box flex="1" overflowY="auto" pr="1">
        <Stack gap="3">
          {node ? (
            <>
              <Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="fg.muted"
                  mb="1"
                >
                  Type
                </Text>
                <Flex gap="2" alignItems="center">
                  <Box
                    w="6"
                    h="6"
                    borderRadius="full"
                    bg={getNodeColor(node)}
                    borderWidth="1px"
                    borderColor="border.subtle"
                  />
                  <Text fontSize="sm" fontWeight="medium">
                    {getNodeLabel(node)}
                  </Text>
                </Flex>
              </Box>

              <Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="fg.muted"
                  mb="1"
                >
                  Labels
                </Text>
                <Flex gap="1" flexWrap="wrap">
                  {node.labels.map((label) => (
                    <Badge key={label} colorPalette="brand" size="sm">
                      {label}
                    </Badge>
                  ))}
                </Flex>
              </Box>

              <Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="fg.muted"
                  mb="1"
                >
                  Properties
                </Text>
                <PropertyList properties={node.properties} />
              </Box>

              <Box pt="2" borderTopWidth="1px" borderColor="border.subtle">
                <Button
                  size="sm"
                  width="100%"
                  colorPalette={isExpanded ? "green" : "brand"}
                  variant={isExpanded ? "outline" : "solid"}
                  onClick={() => onExpand(node.id)}
                  disabled={isExpanded || expandDisabled}
                >
                  {isExpanding ? (
                    <>
                      <Spinner size="sm" />
                      <Text ml="2">Expanding...</Text>
                    </>
                  ) : (
                    <>
                      <LuExpand />
                      <Text ml="2">
                        {isExpanded ? "Already expanded" : "Expand neighbours"}
                      </Text>
                    </>
                  )}
                </Button>
                <Text fontSize="xs" color="fg.subtle" mt="1" textAlign="center">
                  Double-click a node to expand it
                </Text>
              </Box>
            </>
          ) : relationship ? (
            <>
              <Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="fg.muted"
                  mb="1"
                >
                  Type
                </Text>
                <Badge colorPalette="green" size="sm">
                  {relationship.type}
                </Badge>
              </Box>

              <Box>
                <Text
                  fontSize="xs"
                  fontWeight="semibold"
                  color="fg.muted"
                  mb="1"
                >
                  Connection
                </Text>
                <Stack gap="2">
                  {(
                    [
                      ["From", relationship.from],
                      ["To", relationship.to],
                    ] as const
                  ).map(([direction, nodeId]) => {
                    const endpoint = nodesById.get(nodeId);
                    return (
                      <Box key={direction}>
                        <Text fontSize="xs" color="fg.subtle">
                          {direction}:
                        </Text>
                        <Text fontSize="xs" fontWeight="medium" mt="0.5">
                          {endpoint?.labels.join(", ") ?? "Unknown"}
                        </Text>
                        <Text fontSize="xs" color="fg.muted" mt="0.5">
                          {endpointCaption(endpoint)}
                        </Text>
                      </Box>
                    );
                  })}
                </Stack>
              </Box>

              {Object.keys(relationship.properties).length > 0 && (
                <Box>
                  <Text
                    fontSize="xs"
                    fontWeight="semibold"
                    color="fg.muted"
                    mb="1"
                  >
                    Properties
                  </Text>
                  <PropertyList properties={relationship.properties} />
                </Box>
              )}
            </>
          ) : null}
        </Stack>
      </Box>
    </Box>
  );
}
