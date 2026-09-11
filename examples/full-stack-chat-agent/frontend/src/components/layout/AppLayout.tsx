"use client";

import { Box, Button, Flex, IconButton, Stack, Text } from "@chakra-ui/react";
import { useState } from "react";
import { LuNetwork, LuPanelLeft, LuPanelLeftClose } from "react-icons/lu";
import { LabsBadge } from "@/components/branding/LabsBadge";
import { LabsFooter } from "@/components/branding/LabsFooter";
import { MemoryGraphDialog } from "@/components/memory/MemoryGraphDialog";
import { ColorModeButton } from "@/components/ui/color-mode";
import type { Thread } from "@/lib/types";
import { Sidebar } from "./Sidebar";

interface AppLayoutProps {
  children: React.ReactNode;
  threads: Thread[];
  activeThreadId: string | null;
  onSelectThread: (id: string) => void;
  onCreateThread: () => void;
  onDeleteThread: (id: string) => void;
  memoryEnabled: boolean;
  onToggleMemory: (enabled: boolean) => void;
  /** Bumped after each completed turn so the graph dialog refetches. */
  memoryVersion?: number;
}

export function AppLayout({
  children,
  threads,
  activeThreadId,
  onSelectThread,
  onCreateThread,
  onDeleteThread,
  memoryEnabled,
  onToggleMemory,
  memoryVersion = 0,
}: AppLayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [graphViewOpen, setGraphViewOpen] = useState(false);

  return (
    <Flex h="100dvh" overflow="hidden" bg="bg">
      {/* Sidebar */}
      {sidebarOpen && (
        <Box
          w="280px"
          borderRightWidth="1px"
          borderColor="border.subtle"
          bg="bg.panel"
          flexShrink={0}
        >
          <Sidebar
            threads={threads}
            activeThreadId={activeThreadId}
            onSelectThread={onSelectThread}
            onCreateThread={onCreateThread}
            onDeleteThread={onDeleteThread}
            memoryEnabled={memoryEnabled}
            onToggleMemory={onToggleMemory}
          />
        </Box>
      )}

      {/* Main content */}
      <Stack flex="1" gap="0" overflow="hidden">
        {/* Header */}
        <Flex
          h="14"
          px="4"
          gap="3"
          alignItems="center"
          justifyContent="space-between"
          borderBottomWidth="1px"
          borderColor="border.subtle"
          bg="bg.panel"
          flexShrink={0}
        >
          <Flex alignItems="center" gap="3" minW="0">
            <IconButton
              aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
              variant="ghost"
              size="sm"
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              {sidebarOpen ? <LuPanelLeftClose /> : <LuPanelLeft />}
            </IconButton>
            <Text fontWeight="medium" fontFamily="heading" truncate>
              News Research Assistant
            </Text>
            <LabsBadge />
          </Flex>

          <Flex alignItems="center" gap="2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setGraphViewOpen(true)}
            >
              <LuNetwork />
              <Text ml="2">Memory graph</Text>
            </Button>
            <ColorModeButton />
          </Flex>
        </Flex>

        {/* Content area */}
        <Box flex="1" overflow="hidden">
          {children}
        </Box>

        <LabsFooter />
      </Stack>

      <MemoryGraphDialog
        isOpen={graphViewOpen}
        onClose={() => setGraphViewOpen(false)}
        threadId={activeThreadId || undefined}
        memoryVersion={memoryVersion}
      />
    </Flex>
  );
}
