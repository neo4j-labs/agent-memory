"use client";

/**
 * The conversation column: `useChat` + `DefaultChatTransport`, nothing exotic.
 *
 * Two details carry the demo:
 *
 *  - **The transport sends `conversationId` in the body**, and the route handler
 *    forwards only the newest user turn to the model. History comes from memory,
 *    so this component never has to replay it.
 *  - **On mount the panel hydrates from `/api/memory/context`.** Reload the tab
 *    (or open the same `/c/<id>` URL on another device) and the thread is intact,
 *    because the transcript was never client state in the first place.
 */

import { Box, Button, Flex, Input, Stack, Text } from "@chakra-ui/react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useEffect, useMemo, useRef, useState } from "react";

import type { ContextPayload } from "@/lib/types";

const STARTERS = [
  "I'm planning two weeks in Japan in April on a £3,000 budget. I'm vegetarian and I can't do long bus rides.",
  "Given my budget and diet, which three cities should I base myself in?",
  "What did I tell you about transport?",
];

function textOf(message: UIMessage): string {
  return message.parts
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

export function ChatPanel({
  conversationId,
  onTurnComplete,
}: {
  conversationId: string;
  onTurnComplete: (question: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const bottom = useRef<HTMLDivElement | null>(null);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        body: { conversationId },
      }),
    [conversationId],
  );

  const { messages, setMessages, sendMessage, status, error } = useChat({
    id: conversationId,
    transport,
  });

  // Hydrate the transcript from memory once, on mount.
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(
          `/api/memory/context?conversationId=${encodeURIComponent(conversationId)}`,
          { signal: controller.signal },
        );
        if (!response.ok) return;
        const context = (await response.json()) as ContextPayload;
        const restored: UIMessage[] = context.recentMessages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({
            id: m.id,
            role: m.role === "user" ? "user" : "assistant",
            parts: [{ type: "text", text: m.content }],
          }));
        if (restored.length > 0) setMessages(restored);
      } catch {
        // A cold conversation (or an aborted fetch) simply starts empty.
      }
    })();
    return () => controller.abort();
  }, [conversationId, setMessages]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const busy = status === "submitted" || status === "streaming";

  async function send(text: string): Promise<void> {
    const question = text.trim();
    if (!question || busy) return;
    setDraft("");
    await sendMessage({ text: question });
    // The rail refreshes once the answer is complete — by then the middleware
    // has written both sides of the turn.
    onTurnComplete(question);
  }

  return (
    <Flex direction="column" h="full" minH={0}>
      <Box flex="1" overflowY="auto" px={5} py={4}>
        <Stack gap={4}>
          {messages.length === 0 && (
            <Stack gap={3}>
              <Text color="fg.muted" fontSize="sm">
                Try these in order — the third question can only be answered from memory:
              </Text>
              {STARTERS.map((starter) => (
                <Button
                  key={starter}
                  variant="outline"
                  size="sm"
                  justifyContent="flex-start"
                  whiteSpace="normal"
                  height="auto"
                  py={2}
                  textAlign="left"
                  onClick={() => void send(starter)}
                >
                  {starter}
                </Button>
              ))}
            </Stack>
          )}

          {messages.map((message) => (
            <Box
              key={message.id}
              alignSelf={message.role === "user" ? "flex-end" : "flex-start"}
              maxW="85%"
              bg={message.role === "user" ? "colorPalette.subtle" : "bg.panel"}
              colorPalette="purple"
              borderWidth="1px"
              borderRadius="lg"
              px={4}
              py={3}
            >
              <Text fontSize="xs" color="fg.muted" mb={1}>
                {message.role === "user" ? "you" : "assistant"}
              </Text>
              <Text whiteSpace="pre-wrap">{textOf(message)}</Text>
            </Box>
          ))}

          {error && (
            <Text color="fg.error" fontSize="sm">
              {error.message}
            </Text>
          )}
          <div ref={bottom} />
        </Stack>
      </Box>

      <Box as="form" px={5} py={4} borderTopWidth="1px" bg="bg.panel" onSubmit={(event) => {
        event.preventDefault();
        void send(draft);
      }}>
        <Flex gap={2}>
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask about your trip…"
            disabled={busy}
            aria-label="Message"
          />
          <Button type="submit" loading={busy} colorPalette="purple">
            Send
          </Button>
        </Flex>
      </Box>
    </Flex>
  );
}
