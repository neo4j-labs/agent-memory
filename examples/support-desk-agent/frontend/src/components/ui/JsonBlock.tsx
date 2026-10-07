"use client";

import { Box, Code, Text } from "@chakra-ui/react";
import { toJson } from "@/lib/format";

interface JsonBlockProps {
  label?: string;
  value: unknown;
  maxH?: string;
}

/** A labelled, scrollable, monospace dump of a JSON-ish value. */
export function JsonBlock({ label, value, maxH = "200px" }: JsonBlockProps) {
  return (
    <Box minW="0">
      {label ? (
        <Text fontSize="xs" fontWeight="medium" color="fg.muted" mb="1">
          {label}
        </Text>
      ) : null}
      <Code
        display="block"
        whiteSpace="pre-wrap"
        wordBreak="break-word"
        p="2"
        borderRadius="md"
        fontSize="xs"
        bg="bg.subtle"
        maxH={maxH}
        overflowY="auto"
      >
        {toJson(value) || "—"}
      </Code>
    </Box>
  );
}
