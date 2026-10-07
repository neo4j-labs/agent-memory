"use client";

import { Tabs } from "@chakra-ui/react";
import { LuBookOpen, LuBrain, LuNetwork, LuRoute } from "react-icons/lu";
import { GraphPanel } from "@/components/graph/GraphPanel";
import { MemoryPanel } from "@/components/memory/MemoryPanel";
import { OntologyPanel } from "@/components/ontology/OntologyPanel";
import { ReasoningPanel } from "@/components/reasoning/ReasoningPanel";
import { LoadingRow } from "@/components/ui/PanelSection";
import type { MessageStoredEvent } from "@/lib/types";

export type PanelTab = "memory" | "graph" | "ontology" | "reasoning";

export const PANEL_TABS: PanelTab[] = ["memory", "graph", "ontology", "reasoning"];

interface SidePanelProps {
  tab: PanelTab;
  onTabChange: (tab: PanelTab) => void;
  /**
   * False until the thread list has loaded: the thread-scoped tabs wait for
   * it rather than fetching a thread-less view that is replaced a moment later.
   */
  ready: boolean;
  threadId: string | null;
  memoryVersion: number;
  traceVersion: number;
  ontologyVersion: number;
  lastStored: (MessageStoredEvent & { threadId: string }) | null;
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string | null) => void;
  onMemoryChanged: () => void;
  onOntologyChanged: () => void;
}

/**
 * The four right-hand tabs. Only the visible tab is mounted, so a hidden tab
 * does not poll the backend and NVL never lays out a graph in a hidden,
 * zero-sized canvas; each tab fetches fresh data when it is opened.
 */
export function SidePanel({
  tab,
  onTabChange,
  ready,
  threadId,
  memoryVersion,
  traceVersion,
  ontologyVersion,
  lastStored,
  selectedTraceId,
  onSelectTrace,
  onMemoryChanged,
  onOntologyChanged,
}: SidePanelProps) {
  return (
    <Tabs.Root
      value={tab}
      onValueChange={(details) => onTabChange(details.value as PanelTab)}
      lazyMount
      unmountOnExit
      size="sm"
      variant="line"
      display="flex"
      flexDirection="column"
      flex="1"
      h="full"
      minH="0"
      // The panel's width is user-resizable, so its contents respond to the
      // panel (a size container), not to the window.
      css={{ containerType: "inline-size", containerName: "side-panel" }}
    >
      {/* The brand palette is scoped to the tab strip so it does not cascade
          into every neutral badge inside the panels. */}
      <Tabs.List
        px="2"
        flexShrink={0}
        bg="bg.panel"
        colorPalette="brand"
        css={{
          // A narrow panel drops the tab icons rather than squeezing them.
          "@container side-panel (max-width: 430px)": {
            paddingInline: "0",
            "& [role=tab]": { paddingInline: "0.5rem" },
            "& [role=tab] svg": { display: "none" },
          },
        }}
      >
        <Tabs.Trigger value="memory">
          <LuBrain />
          Memory
        </Tabs.Trigger>
        <Tabs.Trigger value="graph">
          <LuNetwork />
          Graph
        </Tabs.Trigger>
        <Tabs.Trigger value="ontology">
          <LuBookOpen />
          Ontology
        </Tabs.Trigger>
        <Tabs.Trigger value="reasoning">
          <LuRoute />
          Reasoning
        </Tabs.Trigger>
      </Tabs.List>

      <Tabs.Content value="memory" flex="1" minH="0" overflowY="auto" p="4">
        {ready ? (
          <MemoryPanel
            threadId={threadId}
            refreshKey={memoryVersion}
            lastStored={lastStored}
            onMemoryChanged={onMemoryChanged}
          />
        ) : (
          <LoadingRow />
        )}
      </Tabs.Content>

      <Tabs.Content
        value="graph"
        flex="1"
        minH="0"
        overflowY="auto"
        p="4"
        display="flex"
        flexDirection="column"
      >
        {ready ? (
          <GraphPanel threadId={threadId} refreshKey={memoryVersion} />
        ) : (
          <LoadingRow />
        )}
      </Tabs.Content>

      <Tabs.Content value="ontology" flex="1" minH="0" overflowY="auto" p="4">
        <OntologyPanel
          refreshKey={ontologyVersion}
          onOntologyChanged={onOntologyChanged}
        />
      </Tabs.Content>

      <Tabs.Content value="reasoning" flex="1" minH="0" overflowY="auto" p="4">
        {ready ? (
          <ReasoningPanel
            threadId={threadId}
            refreshKey={traceVersion}
            selectedTraceId={selectedTraceId}
            onSelectTrace={onSelectTrace}
          />
        ) : (
          <LoadingRow />
        )}
      </Tabs.Content>
    </Tabs.Root>
  );
}
