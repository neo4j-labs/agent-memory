"use client";

/**
 * The two-column layout: conversation on the left, memory rail on the right.
 *
 * The only shared state is the "a turn just finished" signal. The chat raises
 * it; the rail listens and refreshes itself (context, then extraction, then the
 * graph). Nothing else crosses the boundary.
 */

import { Badge, Box, Flex, HStack, Heading, Stack, Text } from "@chakra-ui/react";
import { useCallback, useState } from "react";

import { ChatPanel } from "./ChatPanel";
import { MemoryRail } from "./MemoryRail";

export interface TurnSignal {
  /** The user's question — used as the extraction probe query. */
  question: string;
  /** Monotonic counter so repeating the same question still fires an update. */
  seq: number;
}

export function Workspace({ conversationId }: { conversationId: string }) {
  const [turn, setTurn] = useState<TurnSignal | null>(null);

  const onTurnComplete = useCallback((question: string) => {
    setTurn((previous) => ({ question, seq: (previous?.seq ?? 0) + 1 }));
  }, []);

  return (
    <Flex direction="column" h="100dvh" bg="bg.subtle">
      <Box as="header" px={5} py={3} bg="bg.panel" borderBottomWidth="1px">
        <HStack justify="space-between" align="baseline" wrap="wrap" gap={2}>
          <Stack gap={0}>
            <Heading size="md">Travel assistant with a memory graph</Heading>
            <Text fontSize="xs" color="fg.muted">
              Every message, entity and reasoning step below lives in the hosted Neo4j Agent
              Memory Service — not in this browser.
            </Text>
          </Stack>
          <Badge variant="surface" fontFamily="mono" fontSize="xs">
            {conversationId}
          </Badge>
        </HStack>
      </Box>

      <Flex flex="1" minH={0} direction={{ base: "column", lg: "row" }}>
        <Box flex="1" minW={0} minH={0}>
          <ChatPanel conversationId={conversationId} onTurnComplete={onTurnComplete} />
        </Box>
        <Box
          w={{ base: "full", lg: "420px" }}
          minH={0}
          borderLeftWidth={{ base: 0, lg: "1px" }}
          borderTopWidth={{ base: "1px", lg: 0 }}
          bg="bg.panel"
        >
          <MemoryRail conversationId={conversationId} turn={turn} />
        </Box>
      </Flex>
    </Flex>
  );
}
