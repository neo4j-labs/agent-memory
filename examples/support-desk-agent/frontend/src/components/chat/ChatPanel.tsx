"use client";

import {
  Badge,
  Box,
  Button,
  Flex,
  Heading,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useEffect, useRef } from "react";
import { LuPlus } from "react-icons/lu";
import { StatusAlert } from "@/components/ui/StatusAlert";
import type { ChatMessage } from "@/hooks/useChat";
import type { ThreadSummary } from "@/lib/types";
import { MessageItem } from "./MessageItem";
import { PromptInput } from "./PromptInput";

/** Starter prompts that exercise the tools against the seeded data. */
const SUGGESTIONS = [
  "What open tickets does Priya Raman have?",
  "What's the status of order SO-4417?",
  "Show me ticket TK-2210 and everything linked to it.",
  "Has anyone else reported problems with the Aurora Desk Lamp?",
];

interface ChatPanelProps {
  thread: ThreadSummary | null;
  threadId: string | null;
  messages: ChatMessage[];
  loadingHistory: boolean;
  isStreaming: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
  onNewChat: () => void;
  onOpenTrace: (traceId: string) => void;
  error: string | null;
  onDismissError: () => void;
  warning: { title: string; description: string } | null;
}

export function ChatPanel({
  thread,
  threadId,
  messages,
  loadingHistory,
  isStreaming,
  onSend,
  onStop,
  onNewChat,
  onOpenTrace,
  error,
  onDismissError,
  warning,
}: ChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the newest token in view while streaming.
  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  const banners =
    warning || error ? (
      <Stack gap="2" px="4" pt="3">
        {warning ? (
          <StatusAlert
            status="warning"
            title={warning.title}
            description={warning.description}
          />
        ) : null}
        {error ? (
          <StatusAlert
            status="error"
            title="Something went wrong"
            description={error}
            onDismiss={onDismissError}
          />
        ) : null}
      </Stack>
    ) : null;

  if (!threadId) {
    return (
      <Flex direction="column" h="full" flex="1" minW="0">
        {banners}
        <Flex flex="1" alignItems="center" justifyContent="center" p="6">
          <Stack gap="4" alignItems="center" textAlign="center" maxW="md">
            <Heading size="md">No conversation selected</Heading>
            <Text color="fg.muted">
              Pick a seeded conversation on the left to see what the agent
              remembers about it, or start a new chat.
            </Text>
            <Button colorPalette="brand" onClick={onNewChat}>
              <LuPlus />
              New chat
            </Button>
          </Stack>
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex direction="column" h="full" flex="1" minW="0" overflow="hidden">
      <Flex
        px="4"
        py="2"
        gap="2"
        alignItems="center"
        borderBottomWidth="1px"
        borderColor="border.subtle"
        flexShrink={0}
      >
        <Text fontWeight="medium" truncate flex="1">
          {thread?.title || "Conversation"}
        </Text>
        {thread?.seeded ? (
          <Badge colorPalette="gray" variant="outline" size="sm">
            seeded
          </Badge>
        ) : null}
        <Text fontSize="xs" color="fg.muted" fontFamily="mono" truncate>
          {threadId}
        </Text>
      </Flex>

      {banners}

      <Box
        ref={scrollRef}
        flex="1"
        overflowY="auto"
        p="4"
        role="log"
        aria-live="polite"
        aria-busy={isStreaming}
      >
        {loadingHistory ? (
          <Flex h="full" alignItems="center" justifyContent="center" gap="2">
            <Spinner size="sm" />
            <Text color="fg.muted">Loading conversation…</Text>
          </Flex>
        ) : messages.length === 0 ? (
          <Flex h="full" alignItems="center" justifyContent="center">
            <Stack gap="4" maxW="lg" textAlign="center" alignItems="center">
              <Heading size="md">Ask the support-desk agent</Heading>
              <Text color="fg.muted" fontSize="sm">
                The agent reads the support graph — customers, orders, products
                and tickets typed by the <code>support-desk</code> ontology —
                and records every turn as a reasoning trace. Watch the Memory,
                Graph and Reasoning tabs as it works.
              </Text>
              <Flex gap="2" flexWrap="wrap" justifyContent="center">
                {SUGGESTIONS.map((suggestion) => (
                  <Button
                    key={suggestion}
                    size="xs"
                    variant="outline"
                    onClick={() => onSend(suggestion)}
                    disabled={isStreaming}
                  >
                    {suggestion}
                  </Button>
                ))}
              </Flex>
            </Stack>
          </Flex>
        ) : (
          <Stack gap="5" maxW="3xl" mx="auto">
            {messages.map((message) => (
              <MessageItem
                key={message.key}
                message={message}
                onOpenTrace={onOpenTrace}
              />
            ))}
          </Stack>
        )}
      </Box>

      <Box
        p="3"
        borderTopWidth="1px"
        borderColor="border.subtle"
        bg="bg.panel"
        flexShrink={0}
      >
        <Box maxW="3xl" mx="auto">
          <PromptInput
            onSend={onSend}
            onStop={onStop}
            isStreaming={isStreaming}
            disabled={loadingHistory}
          />
        </Box>
      </Box>
    </Flex>
  );
}
