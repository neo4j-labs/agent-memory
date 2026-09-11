"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Badge,
  Box,
  Button,
  Card,
  Code,
  Flex,
  HStack,
  Input,
  Spinner,
  Text,
  VStack,
} from "@chakra-ui/react";
import ReactMarkdown from "react-markdown";
import { parseToolArguments, streamChat } from "@/lib/api";

interface ToolCall {
  /** `call_id` when the backend sends one, else the tool name. */
  key: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
}

interface Message {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  /** Absent for the static welcome bubble, which must not print a
      server-rendered clock (it would mismatch on hydration). */
  timestamp?: Date;
  isStreaming?: boolean;
  toolCalls?: ToolCall[];
}

interface ChatInterfaceProps {
  sessionId: string;
  userId: string;
}

const WELCOME: Message = {
  id: "welcome",
  role: "assistant",
  content:
    "Hello! I'm your personal shopping assistant. I can help you find products, remember your preferences, and make personalized recommendations. What are you looking for today?",
};

export function ChatInterface({ sessionId, userId }: ChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // Switching shoppers switches sessions, and page.tsx keys this component on
  // the session id — React discards the old transcript for us, so there is no
  // reset effect here.

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      content: input.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput("");
    setIsLoading(true);

    const assistantMessageId = `assistant-${Date.now()}`;
    setMessages((prev) => [
      ...prev,
      {
        id: assistantMessageId,
        role: "assistant",
        content: "",
        timestamp: new Date(),
        isStreaming: true,
        toolCalls: [],
      },
    ]);

    const patch = (update: (message: Message) => Message) =>
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantMessageId ? update(m) : m))
      );

    try {
      for await (const event of streamChat(
        userMessage.content,
        sessionId,
        userId
      )) {
        switch (event.event) {
          case "token":
            patch((m) => ({ ...m, content: m.content + event.data.content }));
            break;

          case "tool_call": {
            const key = event.data.call_id ?? event.data.name;
            patch((m) => ({
              ...m,
              toolCalls: [
                ...(m.toolCalls ?? []),
                {
                  key,
                  name: event.data.name,
                  arguments: parseToolArguments(event.data.arguments),
                },
              ],
            }));
            break;
          }

          case "tool_result": {
            // Match on `call_id` when the backend supplies one; otherwise fall
            // back to the first still-pending call with the same tool name, so
            // two calls to one tool resolve in order instead of both grabbing
            // the first result.
            const { call_id: callId, name, result } = event.data;
            patch((m) => {
              const calls = m.toolCalls ?? [];
              const index = callId
                ? calls.findIndex((tc) => tc.key === callId)
                : calls.findIndex((tc) => tc.name === name && !tc.result);
              if (index === -1) return m;
              const next = [...calls];
              next[index] = { ...next[index], result };
              return { ...m, toolCalls: next };
            });
            break;
          }

          case "done":
            patch((m) => ({ ...m, isStreaming: false }));
            break;

          case "error":
            patch((m) => ({
              ...m,
              content: m.content || `Error: ${event.data.error}`,
              isStreaming: false,
            }));
            break;
        }
      }
    } catch (error) {
      patch((m) => ({
        ...m,
        content: `Error: ${error instanceof Error ? error.message : "Unknown error"}`,
        isStreaming: false,
      }));
    } finally {
      setIsLoading(false);
      patch((m) => ({ ...m, isStreaming: false }));
      inputRef.current?.focus();
    }
  };

  const examplePrompts = [
    "I'm looking for running shoes",
    "I prefer Nike brand",
    "My budget is under $150",
    "What would you recommend?",
    "What do you know about my preferences?",
  ];

  return (
    <Flex direction="column" h="calc(100vh - 220px)">
      {/* Messages */}
      <Box flex={1} overflowY="auto" pb={4}>
        <VStack gap={4} align="stretch">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
          <div ref={messagesEndRef} />
        </VStack>
      </Box>

      {/* Example prompts */}
      {messages.length <= 2 && (
        <HStack gap={2} flexWrap="wrap" mb={4}>
          {examplePrompts.map((prompt) => (
            <Button
              key={prompt}
              size="sm"
              variant="outline"
              colorPalette="teal"
              onClick={() => setInput(prompt)}
            >
              {prompt}
            </Button>
          ))}
        </HStack>
      )}

      {/* Input */}
      <Box as="form" onSubmit={handleSubmit}>
        <HStack gap={2}>
          <Input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask me about products, preferences, or recommendations..."
            size="lg"
            disabled={isLoading}
            bg="bg.panel"
          />
          <Button
            type="submit"
            colorPalette="teal"
            size="lg"
            disabled={isLoading || !input.trim()}
          >
            {isLoading ? <Spinner size="sm" /> : "Send"}
          </Button>
        </HStack>
      </Box>
    </Flex>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <Flex justify={isUser ? "flex-end" : "flex-start"}>
      <Card.Root
        maxW="80%"
        bg={isUser ? "teal.solid" : "bg.panel"}
        color={isUser ? "teal.contrast" : "fg"}
        borderColor="border.subtle"
        shadow="sm"
      >
        <Card.Body p={4}>
          {/* Tool calls */}
          {message.toolCalls && message.toolCalls.length > 0 && (
            <VStack align="stretch" gap={2} mb={3}>
              {message.toolCalls.map((tc) => (
                <ToolCallRow key={tc.key} call={tc} onTealBubble={isUser} />
              ))}
            </VStack>
          )}

          {/* Message content */}
          {message.content ? (
            <Box
              className="markdown-content"
              css={{
                "& p": { marginBottom: "0.5em" },
                "& p:last-child": { marginBottom: 0 },
                "& ul, & ol": { paddingLeft: "1.5em", marginBottom: "0.5em" },
                "& code": {
                  background: "var(--chakra-colors-bg-muted)",
                  padding: "0.1em 0.3em",
                  borderRadius: "3px",
                  fontSize: "0.9em",
                },
              }}
            >
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </Box>
          ) : message.isStreaming ? (
            <HStack>
              <Spinner size="sm" />
              <Text fontSize="sm">Thinking...</Text>
            </HStack>
          ) : null}

          {/* Timestamp */}
          {message.timestamp && (
            <Text
              fontSize="xs"
              color={isUser ? "teal.contrast" : "fg.muted"}
              opacity={isUser ? 0.8 : 1}
              mt={2}
              textAlign="right"
            >
              {message.timestamp.toLocaleTimeString()}
            </Text>
          )}
        </Card.Body>
      </Card.Root>
    </Flex>
  );
}

/**
 * One tool invocation: the spinner clears when the matching `tool_result`
 * frame arrives, and the arguments are rendered from the parsed JSON.
 */
function ToolCallRow({
  call,
  onTealBubble,
}: {
  call: ToolCall;
  onTealBubble: boolean;
}) {
  const argSummary = Object.entries(call.arguments)
    .map(([key, value]) => `${key}: ${formatArg(value)}`)
    .join(", ");

  return (
    <Box
      bg={onTealBubble ? "teal.emphasized" : "bg.muted"}
      p={2}
      borderRadius="md"
      fontSize="sm"
    >
      <HStack>
        <Badge colorPalette="purple" size="sm">
          {call.name}
        </Badge>
        {call.result ? (
          <Badge colorPalette="green" size="sm" variant="subtle">
            done
          </Badge>
        ) : (
          <Spinner size="xs" />
        )}
      </HStack>
      {argSummary && (
        <Code
          mt={1}
          display="block"
          bg="transparent"
          fontSize="xs"
          color="fg.muted"
          lineClamp={2}
        >
          {argSummary}
        </Code>
      )}
    </Box>
  );
}

function formatArg(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "-";
  return JSON.stringify(value);
}
