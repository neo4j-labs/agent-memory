"use client";

import { Box, Flex, Stack, Text } from "@chakra-ui/react";
import {
  ALL_MEMORY_TYPES,
  MEMORY_TYPE_META,
  NODE_COLORS,
  UNKNOWN_NODE_COLOR,
  type MemoryTypeFilters,
} from "./graph";

interface GraphLegendProps {
  filters: MemoryTypeFilters;
}

/**
 * Floating legend mapping each memory layer to the node labels the library
 * actually writes for it.
 */
export function GraphLegend({ filters }: GraphLegendProps) {
  return (
    <Box
      position="absolute"
      bottom="4"
      left="4"
      zIndex="10"
      maxW="300px"
      p="3"
      bg="bg.panel"
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      boxShadow="lg"
    >
      <Text fontSize="sm" fontWeight="bold" mb="2">
        Memory layers
      </Text>
      <Stack gap="2.5" fontSize="xs">
        {ALL_MEMORY_TYPES.filter((type) => filters[type]).map((type) => {
          const meta = MEMORY_TYPE_META[type];
          return (
            <Box
              key={type}
              p="2"
              borderRadius="md"
              bg="bg.subtle"
              borderWidth="1px"
              borderLeftWidth="3px"
              borderColor="border.subtle"
              borderLeftColor={meta.color}
            >
              <Text fontSize="xs" fontWeight="bold" mb="1">
                {meta.label}
              </Text>
              <Stack gap="1">
                {meta.legend.map((label) => (
                  <Flex key={label} gap="2" alignItems="center">
                    <Box
                      w="3"
                      h="3"
                      borderRadius="full"
                      flexShrink={0}
                      bg={NODE_COLORS[label] ?? UNKNOWN_NODE_COLOR}
                    />
                    <Text color="fg.muted">{label}</Text>
                  </Flex>
                ))}
              </Stack>
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}
