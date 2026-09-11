"use client";

import { Box, Button, Flex, Text } from "@chakra-ui/react";
import {
  ALL_MEMORY_TYPES,
  MEMORY_TYPE_META,
  type MemoryTypeFilters,
} from "./graph";

interface MemoryFilterBarProps {
  filters: MemoryTypeFilters;
  onChange: (filters: MemoryTypeFilters) => void;
}

/** Toggle row for the library's three memory layers. */
export function MemoryFilterBar({ filters, onChange }: MemoryFilterBarProps) {
  const allEnabled = ALL_MEMORY_TYPES.every((type) => filters[type]);

  return (
    <Flex
      p="3"
      gap="3"
      justifyContent="center"
      flexWrap="wrap"
      alignItems="center"
      borderBottomWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
    >
      <Text fontSize="xs" fontWeight="semibold" color="fg.muted">
        Filter by memory layer:
      </Text>
      {ALL_MEMORY_TYPES.map((type) => {
        const meta = MEMORY_TYPE_META[type];
        const enabled = filters[type];
        return (
          <Button
            key={type}
            size="sm"
            variant={enabled ? "solid" : "outline"}
            colorPalette={enabled ? meta.palette : "gray"}
            onClick={() => onChange({ ...filters, [type]: !enabled })}
          >
            <Box w="3" h="3" borderRadius="full" bg={meta.color} mr="2" />
            {meta.label}
          </Button>
        );
      })}
      <Button
        size="sm"
        variant="ghost"
        onClick={() =>
          onChange({
            "short-term": !allEnabled,
            "long-term": !allEnabled,
            reasoning: !allEnabled,
          })
        }
      >
        {allEnabled ? "Deselect all" : "Select all"}
      </Button>
    </Flex>
  );
}
