"use client";

import { Flex } from "@chakra-ui/react";
import { useCallback, useState } from "react";
import { ChatContainer } from "@/components/chat/ChatContainer";
import { AppLayout } from "@/components/layout/AppLayout";
import { MemoryContextPanel } from "@/components/memory/MemoryContext";
import { useChat } from "@/hooks/useChat";
import { useHealth } from "@/hooks/useHealth";
import { useThreads } from "@/hooks/useThreads";

export default function Home() {
  // Incremented whenever a turn finishes, so the memory panel and the graph
  // dialog refetch and the user can watch memory being written.
  const [memoryVersion, setMemoryVersion] = useState(0);
  const onTurnComplete = useCallback(
    () => setMemoryVersion((version) => version + 1),
    [],
  );

  const { health, unreachable } = useHealth();

  const {
    threads,
    activeThreadId,
    createThread,
    deleteThread,
    selectThread,
    error: threadsError,
    clearError: clearThreadsError,
  } = useThreads();

  const {
    messages,
    isStreaming,
    memoryEnabled,
    setMemoryEnabled,
    sendMessage,
    stop,
    error: chatError,
    clearError: clearChatError,
  } = useChat(activeThreadId, { onTurnComplete });

  // Surface the two most common first-run failures explicitly: the backend is
  // not running at all, or it is running with the memory graph disconnected.
  const warning = unreachable
    ? `The backend at ${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api"} is not reachable (${unreachable}). Start it with "make chat-agent-backend".`
    : health && !health.memory_connected
      ? (health.memory_error ??
        "The backend started without a memory connection, so nothing will be stored. Check NEO4J_PASSWORD in backend/.env.")
      : null;

  const error = chatError ?? threadsError;
  const dismissError = chatError ? clearChatError : clearThreadsError;

  return (
    <AppLayout
      threads={threads}
      activeThreadId={activeThreadId}
      onSelectThread={selectThread}
      onCreateThread={createThread}
      onDeleteThread={deleteThread}
      memoryEnabled={memoryEnabled}
      onToggleMemory={setMemoryEnabled}
      memoryVersion={memoryVersion}
    >
      <Flex h="full" overflow="hidden">
        <ChatContainer
          messages={messages}
          isStreaming={isStreaming}
          onSendMessage={sendMessage}
          onStop={stop}
          threadId={activeThreadId}
          error={error}
          onDismissError={dismissError}
          warning={warning}
        />
        <MemoryContextPanel
          threadId={activeThreadId}
          isVisible={memoryEnabled}
          memoryVersion={memoryVersion}
        />
      </Flex>
    </AppLayout>
  );
}
