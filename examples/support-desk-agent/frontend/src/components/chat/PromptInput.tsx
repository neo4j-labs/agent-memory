"use client";

import { Flex, IconButton, Textarea } from "@chakra-ui/react";
import { useState, type KeyboardEvent } from "react";
import { LuSend, LuSquare } from "react-icons/lu";

interface PromptInputProps {
  onSend: (content: string) => void;
  /** Aborts the in-flight turn; rendered as a Stop button while streaming. */
  onStop?: () => void;
  isStreaming?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

export function PromptInput({
  onSend,
  onStop,
  isStreaming = false,
  disabled = false,
  placeholder = "Ask about a customer, an order or a ticket…",
}: PromptInputProps) {
  const [value, setValue] = useState("");

  const send = () => {
    if (!value.trim() || isStreaming || disabled) return;
    onSend(value.trim());
    setValue("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <Flex gap="2" alignItems="flex-end">
      <Textarea
        flex="1"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        aria-label="Message"
        disabled={disabled}
        rows={1}
        autoresize
        maxH="200px"
        py="3"
        borderRadius="xl"
        bg="bg.subtle"
        _focus={{ bg: "bg.panel", borderColor: "border.emphasized" }}
      />
      {isStreaming && onStop ? (
        <IconButton
          aria-label="Stop generating"
          onClick={onStop}
          colorPalette="red"
          borderRadius="full"
        >
          <LuSquare />
        </IconButton>
      ) : (
        <IconButton
          aria-label="Send message"
          onClick={send}
          disabled={!value.trim() || isStreaming || disabled}
          colorPalette="brand"
          borderRadius="full"
        >
          <LuSend />
        </IconButton>
      )}
    </Flex>
  );
}
