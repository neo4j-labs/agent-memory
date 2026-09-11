"use client";

import { Box, Flex, Stack, Text } from "@chakra-ui/react";
import { useEffect, useRef } from "react";
import { StatusAlert } from "@/components/ui/StatusAlert";
import type { Message } from "@/lib/types";
import { MessageList } from "./MessageList";
import { PromptInput } from "./PromptInput";

interface ChatContainerProps {
  messages: Message[];
  isStreaming: boolean;
  onSendMessage: (content: string) => void;
  onStop: () => void;
  threadId: string | null;
  /** Rendered above the message list; null hides the alert. */
  error?: string | null;
  onDismissError?: () => void;
  /** Persistent warning, e.g. "the backend is running without memory". */
  warning?: string | null;
}

export function ChatContainer({
  messages,
  isStreaming,
  onSendMessage,
  onStop,
  threadId,
  error,
  onDismissError,
  warning,
}: ChatContainerProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const banners = (
    <Stack gap="2" px="4" pt="3">
      {warning ? (
        <StatusAlert
          status="warning"
          title="Running without memory"
          description={warning}
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
  );

  if (!threadId) {
    return (
      <Flex direction="column" h="full" flex="1" overflow="hidden">
        {(warning || error) && banners}
        <Flex flex="1" alignItems="center" justifyContent="center">
          <Text fontSize="lg" color="fg.muted" textAlign="center" px="4">
            Select a conversation or create a new one
          </Text>
        </Flex>
      </Flex>
    );
  }

  return (
    <Flex direction="column" h="full" flex="1" overflow="hidden">
      {(warning || error) && banners}

      {/* Messages area */}
      <Box ref={scrollRef} flex="1" overflowY="auto" p="4">
        {messages.length === 0 ? (
          <Flex h="full" alignItems="center" justifyContent="center">
            <Stack textAlign="center" gap="4" maxW="md">
              <Text fontSize="lg" fontWeight="medium" fontFamily="heading">
                Start a conversation
              </Text>
              <Text color="fg.muted">
                Ask about news articles, topics, or search for specific
                information in the news graph. Anything you tell the agent about
                yourself is written to long-term memory — watch the memory panel
                on the right.
              </Text>
            </Stack>
          </Flex>
        ) : (
          <MessageList messages={messages} />
        )}
      </Box>

      {/* Input area */}
      <Box
        p="4"
        borderTopWidth="1px"
        borderColor="border.subtle"
        bg="bg.panel"
        flexShrink={0}
      >
        <PromptInput
          onSend={onSendMessage}
          onStop={onStop}
          isLoading={isStreaming}
          placeholder="Ask about news..."
        />
      </Box>
    </Flex>
  );
}
