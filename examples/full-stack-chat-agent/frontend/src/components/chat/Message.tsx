"use client";

import { Box, Flex, Text, Stack } from "@chakra-ui/react";
import { LuUser, LuBot } from "react-icons/lu";
import ReactMarkdown from "react-markdown";
import { ToolCallDisplay } from "./ToolCallDisplay";
import type { Message as MessageType } from "@/lib/types";

interface MessageProps {
  message: MessageType;
}

export function Message({ message }: MessageProps) {
  const isUser = message.role === "user";

  return (
    <Flex gap="3" alignItems="flex-start">
      {/* Avatar */}
      <Flex
        w="8"
        h="8"
        borderRadius="full"
        bg={isUser ? "blue.subtle" : "green.subtle"}
        alignItems="center"
        justifyContent="center"
        flexShrink={0}
      >
        {isUser ? <LuUser size={16} /> : <LuBot size={16} />}
      </Flex>

      {/* Content */}
      <Stack gap="2" flex="1" minW="0">
        <Text fontSize="sm" fontWeight="medium" color="fg.muted">
          {isUser ? "You" : "Assistant"}
        </Text>

        {/* Message content */}
        {message.content && (
          <Box
            className="prose prose-sm"
            css={{
              "& p": { margin: 0 },
              "& p + p": { marginTop: "0.5em" },
              "& ul, & ol": { paddingLeft: "1.5em", margin: "0.5em 0" },
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
              "& pre code": {
                background: "none",
                padding: 0,
              },
            }}
          >
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </Box>
        )}

        {/* Tool calls */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <Stack gap="2">
            {message.toolCalls.map((toolCall) => (
              <ToolCallDisplay key={toolCall.id} toolCall={toolCall} />
            ))}
          </Stack>
        )}
      </Stack>
    </Flex>
  );
}
