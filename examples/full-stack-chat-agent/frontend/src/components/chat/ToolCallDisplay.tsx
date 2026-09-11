"use client";

import { Box, Flex, Text, Badge, Code, Stack } from "@chakra-ui/react";
import { useState } from "react";
import { LuChevronDown, LuChevronRight, LuWrench } from "react-icons/lu";
import type { ToolCall } from "@/lib/types";

interface ToolCallDisplayProps {
  toolCall: ToolCall;
}

export function ToolCallDisplay({ toolCall }: ToolCallDisplayProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const statusColor =
    toolCall.status === "success"
      ? "green"
      : toolCall.status === "error"
        ? "red"
        : "yellow";

  return (
    <Box
      borderWidth="1px"
      borderColor="border.subtle"
      borderRadius="md"
      overflow="hidden"
    >
      {/* Header */}
      <Flex
        px="3"
        py="2"
        bg="bg.muted"
        alignItems="center"
        gap="2"
        cursor="pointer"
        onClick={() => setIsExpanded(!isExpanded)}
        _hover={{ bg: "bg.emphasized" }}
      >
        {isExpanded ? (
          <LuChevronDown size={14} />
        ) : (
          <LuChevronRight size={14} />
        )}
        <LuWrench size={14} />
        <Text fontSize="sm" fontWeight="medium" flex="1">
          {toolCall.name}
        </Text>
        <Badge colorPalette={statusColor} size="sm">
          {toolCall.status}
        </Badge>
        {toolCall.duration_ms !== undefined && (
          <Text fontSize="xs" color="fg.muted">
            {toolCall.duration_ms.toFixed(0)}ms
          </Text>
        )}
      </Flex>

      {/* Expandable content */}
      {isExpanded && (
        <Stack p="3" gap="3">
          {/* Arguments */}
          <Box>
            <Text fontSize="xs" fontWeight="medium" color="fg.muted" mb="1">
              Arguments
            </Text>
            <Code
              display="block"
              whiteSpace="pre-wrap"
              p="2"
              borderRadius="md"
              fontSize="xs"
              bg="bg.subtle"
            >
              {JSON.stringify(toolCall.args, null, 2)}
            </Code>
          </Box>

          {/* Result */}
          {toolCall.result !== undefined && (
            <Box>
              <Text fontSize="xs" fontWeight="medium" color="fg.muted" mb="1">
                Result
              </Text>
              <Code
                display="block"
                whiteSpace="pre-wrap"
                p="2"
                borderRadius="md"
                fontSize="xs"
                bg="bg.subtle"
                maxH="200px"
                overflowY="auto"
              >
                {typeof toolCall.result === "string"
                  ? toolCall.result
                  : JSON.stringify(toolCall.result, null, 2)}
              </Code>
            </Box>
          )}
        </Stack>
      )}
    </Box>
  );
}
