"use client";

import { Flex, useBreakpointValue, IconButton } from "@chakra-ui/react";
import { useState, useCallback } from "react";
import { LuBot } from "react-icons/lu";
import { AppLayout } from "@/components/layout/AppLayout";
import { ChatContainer } from "@/components/chat/ChatContainer";
import { MemoryContextPanel } from "@/components/memory/MemoryContext";
import { useQuickStart } from "@/hooks/useQuickStart";
import { useChat } from "@/hooks/useChat";
import type { QuickStartSuggestion } from "@/lib/types";

export default function Home() {
  const {
    messages,
    threadId,
    isStreaming,
    error,
    memoryVersion,
    sendMessage,
    startNewConversation,
    clearError,
  } = useChat();

  const { suggestions, isLoading: isLoadingSuggestions } = useQuickStart(10);

  // Mobile panel state
  const [mobileConfigOpen, setMobileConfigOpen] = useState(false);
  // Default to false during SSR to avoid hydration mismatch
  const isMobile = useBreakpointValue({ base: true, lg: false }) ?? false;

  // Handle selecting a quick-start suggestion
  const handleSelectSuggestion = useCallback(
    async (suggestion: QuickStartSuggestion) => {
      await startNewConversation(suggestion.firstMessage);
    },
    [startNewConversation],
  );

  // Handle starting a new blank conversation
  const handleNewConversation = useCallback(() => {
    startNewConversation();
  }, [startNewConversation]);

  return (
    <AppLayout
      suggestions={suggestions}
      isLoadingSuggestions={isLoadingSuggestions}
      onNewConversation={handleNewConversation}
      onSelectSuggestion={handleSelectSuggestion}
      hasActiveConversation={messages.length > 0}
    >
      <Flex h="full" position="relative">
        <ChatContainer
          messages={messages}
          isStreaming={isStreaming}
          error={error}
          onSendMessage={sendMessage}
          onClearError={clearError}
        />

        {/* Desktop: Always show config panel */}
        {/* Mobile: Show bottom sheet only when mobileConfigOpen */}
        <MemoryContextPanel
          isVisible={isMobile ? mobileConfigOpen : true}
          onClose={() => setMobileConfigOpen(false)}
          threadId={threadId}
          memoryVersion={memoryVersion}
        />

        {/* Mobile FAB to open agent config */}
        {isMobile && !mobileConfigOpen && (
          <IconButton
            aria-label="View agent configuration"
            position="absolute"
            bottom="100px"
            right="4"
            borderRadius="full"
            size="lg"
            colorPalette="brand"
            boxShadow="lg"
            onClick={() => setMobileConfigOpen(true)}
          >
            <LuBot />
          </IconButton>
        )}
      </Flex>
    </AppLayout>
  );
}
