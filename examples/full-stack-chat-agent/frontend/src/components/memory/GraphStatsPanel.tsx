"use client";

import { Badge, Box, Flex, Stack, Text } from "@chakra-ui/react";
import { NODE_COLORS, UNKNOWN_NODE_COLOR } from "./graph";

interface GraphStatsPanelProps {
  nodeCount: number;
  relationshipCount: number;
  /** `[label, count]`, already sorted. */
  nodesByLabel: [string, number][];
  /** `[relationship type, count]`, already sorted. */
  relationshipsByType: [string, number][];
}

function CountRow({
  label,
  count,
  color,
}: {
  label: string;
  count: number;
  color?: string;
}) {
  return (
    <Flex justifyContent="space-between" fontSize="xs" gap="2">
      <Flex gap="1.5" alignItems="center" minW="0">
        {color ? (
          <Box w="2.5" h="2.5" borderRadius="full" flexShrink={0} bg={color} />
        ) : null}
        <Text color="fg.muted" truncate>
          {label}
        </Text>
      </Flex>
      <Badge colorPalette="gray" size="sm">
        {count}
      </Badge>
    </Flex>
  );
}

/** Floating "what is on screen" summary for the graph canvas. */
export function GraphStatsPanel({
  nodeCount,
  relationshipCount,
  nodesByLabel,
  relationshipsByType,
}: GraphStatsPanelProps) {
  return (
    <Box
      position="absolute"
      top="4"
      right="4"
      zIndex="10"
      w="260px"
      maxH="min(400px, 60%)"
      p="4"
      bg="bg.panel"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      boxShadow="lg"
      display="flex"
      flexDirection="column"
    >
      <Stack gap="1" mb="3">
        <Text fontSize="sm" fontWeight="bold">
          Scene overview
        </Text>
        <Text fontSize="xs" color="fg.muted">
          {nodeCount} nodes, {relationshipCount} relationships
        </Text>
      </Stack>
      <Box flex="1" overflowY="auto" pr="1">
        <Stack gap="3">
          <Box>
            <Text fontSize="xs" fontWeight="semibold" color="fg.muted" mb="1">
              Nodes by label
            </Text>
            <Stack gap="0.5">
              {nodesByLabel.map(([label, count]) => (
                <CountRow
                  key={label}
                  label={label}
                  count={count}
                  color={NODE_COLORS[label] ?? UNKNOWN_NODE_COLOR}
                />
              ))}
            </Stack>
          </Box>

          {relationshipsByType.length > 0 && (
            <Box>
              <Text fontSize="xs" fontWeight="semibold" color="fg.muted" mb="1">
                Relationships
              </Text>
              <Stack gap="0.5">
                {relationshipsByType.map(([type, count]) => (
                  <CountRow key={type} label={type} count={count} />
                ))}
              </Stack>
            </Box>
          )}
        </Stack>
      </Box>
    </Box>
  );
}
