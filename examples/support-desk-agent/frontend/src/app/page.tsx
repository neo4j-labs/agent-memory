"use client";

import {
  Box,
  CloseButton,
  Drawer,
  Flex,
  Portal,
  Stack,
} from "@chakra-ui/react";
import { useCallback, useState } from "react";
import { LabsFooter } from "@/components/branding/LabsFooter";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { AppHeader } from "@/components/layout/AppHeader";
import { ThreadSidebar } from "@/components/layout/ThreadSidebar";
import { SidePanel, type PanelTab } from "@/components/panels/SidePanel";
import { useApi } from "@/hooks/useApi";
import { useChat } from "@/hooks/useChat";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useThreads } from "@/hooks/useThreads";
import { API_ORIGIN, api } from "@/lib/api";
import type { MessageStoredEvent } from "@/lib/types";

/** Widths at which the side columns stop being drawers. */
const SIDEBAR_INLINE = "(min-width: 768px)";
const PANEL_INLINE = "(min-width: 1280px)";

const bump = (n: number) => n + 1;

export default function Home() {
  const sidebarInline = useMediaQuery(SIDEBAR_INLINE);
  const panelInline = useMediaQuery(PANEL_INLINE);

  // Inline columns start open; drawers start closed.
  const [sidebarShown, setSidebarShown] = useState(true);
  const [panelShown, setPanelShown] = useState(true);
  const [sidebarDrawer, setSidebarDrawer] = useState(false);
  const [panelDrawer, setPanelDrawer] = useState(false);

  const [tab, setTab] = useState<PanelTab>("memory");
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [lastStored, setLastStored] = useState<
    (MessageStoredEvent & { threadId: string }) | null
  >(null);

  // Refresh counters: bumping one makes the panels that read it refetch.
  const [memoryVersion, setMemoryVersion] = useState(0);
  const [traceVersion, setTraceVersion] = useState(0);
  const [ontologyVersion, setOntologyVersion] = useState(0);

  const loadHealth = useCallback(
    (signal: AbortSignal) => api.health({ signal }),
    [],
  );
  const health = useApi(loadHealth, ontologyVersion);

  const threads = useThreads();
  const {
    activeThreadId,
    refresh: refreshThreads,
    selectThread: selectThreadId,
    createThread,
  } = threads;

  const onMessageStored = useCallback(
    (event: MessageStoredEvent) => {
      if (activeThreadId) setLastStored({ ...event, threadId: activeThreadId });
      setMemoryVersion(bump);
    },
    [activeThreadId],
  );
  const onTraceStarted = useCallback((traceId: string) => {
    setSelectedTraceId(traceId);
    setTraceVersion(bump);
  }, []);
  const onToolResult = useCallback(() => setTraceVersion(bump), []);
  const onTurnComplete = useCallback(() => {
    setMemoryVersion(bump);
    setTraceVersion(bump);
    refreshThreads();
  }, [refreshThreads]);

  const chat = useChat(activeThreadId, {
    onMessageStored,
    onTraceStarted,
    onToolResult,
    onTurnComplete,
  });

  const selectThread = useCallback(
    (id: string) => {
      selectThreadId(id);
      setSelectedTraceId(null);
      setSidebarDrawer(false);
    },
    [selectThreadId],
  );

  const newChat = useCallback(async () => {
    const created = await createThread();
    if (created) {
      setSelectedTraceId(null);
      setSidebarDrawer(false);
    }
  }, [createThread]);

  const openTrace = useCallback(
    (traceId: string) => {
      setSelectedTraceId(traceId);
      setTab("reasoning");
      if (panelInline) setPanelShown(true);
      else setPanelDrawer(true);
    },
    [panelInline],
  );

  const onMemoryChanged = useCallback(() => setMemoryVersion(bump), []);
  const onOntologyChanged = useCallback(() => {
    setOntologyVersion(bump);
    setMemoryVersion(bump);
  }, []);

  const toggleSidebar = () =>
    sidebarInline ? setSidebarShown((v) => !v) : setSidebarDrawer((v) => !v);
  const togglePanel = () =>
    panelInline ? setPanelShown((v) => !v) : setPanelDrawer((v) => !v);

  // First-run failures, stated plainly instead of an empty-looking UI.
  const warning = health.error
    ? {
        title: "Backend not reachable",
        description: `${API_ORIGIN}/api/health failed (${health.error}). Start it with "make run-backend", or set NEXT_PUBLIC_API_URL.`,
      }
    : health.data && !health.data.neo4j
      ? {
          title: "Neo4j is not connected",
          description:
            "The backend is running but cannot reach Neo4j. Check NEO4J_URI / NEO4J_PASSWORD in backend/.env and that `make neo4j` is up.",
        }
      : health.data && !health.data.ontology.domain_id
        ? {
            title: "No ontology active",
            description:
              "Run `make seed` to import the support-desk ontology, activate revision 1 and load the seed conversations.",
          }
        : health.data?.agent_model === "test"
          ? {
              title: "Running on PydanticAI's TestModel",
              description:
                "AGENT_MODEL=test is a keyless stand-in for smoke tests: it calls tools with placeholder arguments and is not a real agent. Set AGENT_MODEL and OPENAI_API_KEY in backend/.env.",
            }
          : null;

  const sidebar = (
    <ThreadSidebar
      threads={threads.threads}
      loaded={threads.loaded}
      activeThreadId={activeThreadId}
      onSelect={selectThread}
      onNewChat={newChat}
    />
  );

  const sidePanel = (
    <SidePanel
      tab={tab}
      onTabChange={setTab}
      ready={threads.loaded}
      threadId={activeThreadId}
      memoryVersion={memoryVersion}
      traceVersion={traceVersion}
      ontologyVersion={ontologyVersion}
      lastStored={lastStored}
      selectedTraceId={selectedTraceId}
      onSelectTrace={setSelectedTraceId}
      onMemoryChanged={onMemoryChanged}
      onOntologyChanged={onOntologyChanged}
    />
  );

  const error = chat.error ?? threads.error;
  const dismissError = chat.error ? chat.clearError : threads.clearError;

  return (
    <Stack h="100dvh" gap="0" bg="bg" overflow="hidden">
      <AppHeader
        health={health.data}
        healthError={health.error}
        sidebarOpen={sidebarInline ? sidebarShown : sidebarDrawer}
        onToggleSidebar={toggleSidebar}
        panelOpen={panelInline ? panelShown : panelDrawer}
        onTogglePanel={togglePanel}
      />

      <Flex flex="1" minH="0" overflow="hidden">
        {sidebarInline && sidebarShown ? (
          <Box
            as="aside"
            w="270px"
            flexShrink={0}
            borderRightWidth="1px"
            borderColor="border.subtle"
            bg="bg.panel"
          >
            {sidebar}
          </Box>
        ) : null}

        <Flex as="main" flex="1" minW="0" direction="column">
          <ChatPanel
            thread={threads.activeThread}
            threadId={activeThreadId}
            messages={chat.messages}
            loadingHistory={chat.loadingHistory}
            isStreaming={chat.isStreaming}
            onSend={chat.sendMessage}
            onStop={chat.stop}
            onNewChat={newChat}
            onOpenTrace={openTrace}
            error={error}
            onDismissError={dismissError}
            warning={warning}
          />
        </Flex>

        {panelInline && panelShown ? (
          <Box
            as="aside"
            aria-label="Memory panels"
            w={{ base: "440px", "2xl": "520px" }}
            flexShrink={0}
            borderLeftWidth="1px"
            borderColor="border.subtle"
            bg="bg.panel"
            minH="0"
          >
            {sidePanel}
          </Box>
        ) : null}
      </Flex>

      <LabsFooter />

      {!sidebarInline ? (
        <Drawer.Root
          open={sidebarDrawer}
          onOpenChange={(details) => setSidebarDrawer(details.open)}
          placement="start"
          size="xs"
        >
          <Portal>
            <Drawer.Backdrop />
            <Drawer.Positioner>
              <Drawer.Content>
                <Drawer.Header>
                  <Drawer.Title>Conversations</Drawer.Title>
                </Drawer.Header>
                <Drawer.Body p="0">{sidebar}</Drawer.Body>
                <Drawer.CloseTrigger asChild>
                  <CloseButton size="sm" />
                </Drawer.CloseTrigger>
              </Drawer.Content>
            </Drawer.Positioner>
          </Portal>
        </Drawer.Root>
      ) : null}

      {!panelInline ? (
        <Drawer.Root
          open={panelDrawer}
          onOpenChange={(details) => setPanelDrawer(details.open)}
          placement="end"
          size={{ base: "full", md: "md" }}
          lazyMount
          unmountOnExit
        >
          <Portal>
            <Drawer.Backdrop />
            <Drawer.Positioner>
              <Drawer.Content>
                <Drawer.Header pb="0">
                  <Drawer.Title>Memory</Drawer.Title>
                </Drawer.Header>
                <Drawer.Body p="0" display="flex" flexDirection="column">
                  {sidePanel}
                </Drawer.Body>
                <Drawer.CloseTrigger asChild>
                  <CloseButton size="sm" />
                </Drawer.CloseTrigger>
              </Drawer.Content>
            </Drawer.Positioner>
          </Portal>
        </Drawer.Root>
      ) : null}
    </Stack>
  );
}
