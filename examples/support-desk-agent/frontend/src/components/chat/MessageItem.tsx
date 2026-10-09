"use client";

import { Badge, Box, Button, Flex, Spinner, Stack, Text } from "@chakra-ui/react";
import { memo } from "react";
import { LuBot, LuRoute, LuUser } from "react-icons/lu";
import ReactMarkdown from "react-markdown";
import { EntityBadge } from "@/components/ui/EntityBadge";
import type { ChatMessage } from "@/hooks/useChat";
import { shortId } from "@/lib/format";
import { primaryLabel } from "@/lib/labels";
import { ToolCallCard } from "./ToolCallCard";

interface MessageItemProps {
  message: ChatMessage;
  onOpenTrace: (traceId: string) => void;
}

const markdownCss = {
  "& p": { margin: 0 },
  "& p + p": { marginTop: "0.5em" },
  "& ul, & ol": { paddingLeft: "1.5em", margin: "0.5em 0" },
  "& li + li": { marginTop: "0.2em" },
  "& code": {
    backgroundColor: "var(--chakra-colors-bg-muted)",
    padding: "0.1em 0.3em",
    borderRadius: "0.25em",
    fontSize: "0.9em",
  },
  "& pre": {
    backgroundColor: "var(--chakra-colors-bg-muted)",
    padding: "0.75em",
    borderRadius: "0.5em",
    overflow: "auto",
  },
  "& pre code": { background: "none", padding: 0 },
  "& table": { borderCollapse: "collapse", margin: "0.5em 0" },
  "& th, & td": {
    border: "1px solid var(--chakra-colors-border)",
    padding: "0.25em 0.5em",
  },
  "& a": { color: "var(--chakra-colors-brand-fg)", textDecoration: "underline" },
};

function MessageItemImpl({ message, onOpenTrace }: MessageItemProps) {
  const isUser = message.role === "user";
  const waiting =
    message.streaming && !message.content && message.toolCalls.length === 0;

  return (
    <Flex gap="3" alignItems="flex-start" as="article">
      <Flex
        w="8"
        h="8"
        borderRadius="full"
        bg={isUser ? "blue.subtle" : "brand.subtle"}
        color={isUser ? "blue.fg" : "brand.fg"}
        alignItems="center"
        justifyContent="center"
        flexShrink={0}
        aria-hidden
      >
        {isUser ? <LuUser size={16} /> : <LuBot size={16} />}
      </Flex>

      <Stack gap="2" flex="1" minW="0">
        <Flex alignItems="center" gap="2" flexWrap="wrap">
          <Text fontSize="sm" fontWeight="medium" color="fg.muted">
            {isUser ? "You" : "Support assistant"}
          </Text>
          {!isUser && message.traceId ? (
            <Button
              size="2xs"
              variant="subtle"
              colorPalette="green"
              onClick={() => onOpenTrace(message.traceId as string)}
              aria-label={`Open reasoning trace ${message.traceId} in the Reasoning tab`}
            >
              <LuRoute />
              trace {shortId(message.traceId)}
            </Button>
          ) : null}
          {message.streaming ? <Spinner size="xs" color="fg.muted" /> : null}
        </Flex>

        {message.toolCalls.length > 0 ? (
          <Stack gap="2">
            {message.toolCalls.map((call) => (
              <ToolCallCard key={call.id} call={call} />
            ))}
          </Stack>
        ) : null}

        {waiting ? (
          <Text fontSize="sm" color="fg.muted">
            Thinking…
          </Text>
        ) : null}

        {message.content ? (
          <Box fontSize="sm" lineHeight="tall" css={markdownCss}>
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </Box>
        ) : null}

        {isUser && message.entities ? (
          <Flex gap="1" flexWrap="wrap" alignItems="center">
            <Badge size="sm" variant="outline" colorPalette="gray">
              stored · {message.entities.length} extracted
            </Badge>
            {message.entities.map((entity) => (
              <EntityBadge
                key={`${entity.type}:${entity.name}`}
                name={entity.name}
                label={primaryLabel(entity.labels, entity.type)}
                title={`${entity.name} — ${entity.type}${entity.subtype ? `:${entity.subtype}` : ""} (${entity.labels.join(", ")})`}
              />
            ))}
          </Flex>
        ) : null}
      </Stack>
    </Flex>
  );
}

export const MessageItem = memo(MessageItemImpl);
