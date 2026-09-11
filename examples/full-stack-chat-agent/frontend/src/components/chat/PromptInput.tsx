"use client";

import { Box, Flex, IconButton, Textarea } from "@chakra-ui/react";
import { useState, type KeyboardEvent } from "react";
import { LuSend, LuSquare } from "react-icons/lu";

interface PromptInputProps {
  onSend: (content: string) => void;
  /** Aborts the in-flight turn. Rendered as a Stop button while streaming. */
  onStop?: () => void;
  isLoading?: boolean;
  placeholder?: string;
}

export function PromptInput({
  onSend,
  onStop,
  isLoading = false,
  placeholder = "Type a message...",
}: PromptInputProps) {
  const [value, setValue] = useState("");

  const handleSend = () => {
    if (value.trim() && !isLoading) {
      onSend(value.trim());
      setValue("");
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <Flex gap="2" alignItems="flex-end">
      <Box flex="1" position="relative">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={isLoading}
          rows={1}
          resize="none"
          minH="44px"
          maxH="200px"
          py="3"
          pr="12"
          borderRadius="xl"
          bg="bg.subtle"
          _focus={{
            bg: "bg.panel",
            borderColor: "border.emphasized",
          }}
          css={{
            overflow: "hidden",
            resize: "none",
            "&::-webkit-scrollbar": {
              display: "none",
            },
          }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = "auto";
            target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
          }}
        />
      </Box>
      {isLoading && onStop ? (
        <IconButton
          aria-label="Stop generating"
          onClick={onStop}
          colorPalette="red"
          borderRadius="full"
          size="md"
        >
          <LuSquare />
        </IconButton>
      ) : (
        <IconButton
          aria-label="Send message"
          onClick={handleSend}
          disabled={!value.trim() || isLoading}
          colorPalette="brand"
          borderRadius="full"
          size="md"
        >
          <LuSend />
        </IconButton>
      )}
    </Flex>
  );
}
