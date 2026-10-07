"use client";

import { Badge, Flex, IconButton, Text } from "@chakra-ui/react";
import {
  LuHeadset,
  LuPanelLeft,
  LuPanelLeftClose,
  LuPanelRight,
  LuPanelRightClose,
} from "react-icons/lu";
import { LabsBadge } from "@/components/branding/LabsBadge";
import { ColorModeButton } from "@/components/ui/color-mode";
import type { Health } from "@/lib/types";

interface AppHeaderProps {
  health: Health | null;
  healthError: string | null;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  panelOpen: boolean;
  onTogglePanel: () => void;
}

function HealthBadges({
  health,
  healthError,
}: {
  health: Health | null;
  healthError: string | null;
}) {
  if (healthError) {
    return (
      <Badge colorPalette="red" size="sm" title={healthError}>
        backend unreachable
      </Badge>
    );
  }
  if (!health) return null;
  const { ontology } = health;
  return (
    <Flex gap="1" display={{ base: "none", md: "flex" }} alignItems="center">
      <Badge
        size="sm"
        colorPalette={health.neo4j ? "green" : "red"}
        variant="subtle"
      >
        neo4j {health.neo4j ? "connected" : "down"}
      </Badge>
      {ontology.domain_id ? (
        <Badge
          size="sm"
          variant="outline"
          title="The ontology the backend's client resolved"
        >
          {ontology.domain_id}
          {ontology.revision !== null ? ` r${ontology.revision}` : ""}
          {ontology.validation_mode ? ` · ${ontology.validation_mode}` : ""}
        </Badge>
      ) : (
        <Badge size="sm" colorPalette="yellow" variant="subtle">
          no ontology
        </Badge>
      )}
      <Badge
        size="sm"
        variant="subtle"
        colorPalette={health.agent_model === "test" ? "yellow" : "gray"}
        title={
          health.agent_model === "test"
            ? "PydanticAI TestModel: keyless, for smoke tests — not a real agent"
            : "Agent model"
        }
      >
        {health.agent_model}
      </Badge>
    </Flex>
  );
}

export function AppHeader({
  health,
  healthError,
  sidebarOpen,
  onToggleSidebar,
  panelOpen,
  onTogglePanel,
}: AppHeaderProps) {
  return (
    <Flex
      as="header"
      h="14"
      px="3"
      gap="3"
      alignItems="center"
      borderBottomWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
      flexShrink={0}
    >
      <IconButton
        aria-label={sidebarOpen ? "Hide conversations" : "Show conversations"}
        variant="ghost"
        size="sm"
        onClick={onToggleSidebar}
      >
        {sidebarOpen ? <LuPanelLeftClose /> : <LuPanelLeft />}
      </IconButton>
      <Flex alignItems="center" gap="2" minW="0">
        <LuHeadset aria-hidden />
        <Text
          as="h1"
          fontWeight="semibold"
          fontFamily="heading"
          truncate
          fontSize={{ base: "sm", md: "md" }}
        >
          Support Desk Agent
        </Text>
        <LabsBadge />
      </Flex>
      <Flex flex="1" justifyContent="flex-end" alignItems="center" gap="2">
        <HealthBadges health={health} healthError={healthError} />
        <IconButton
          aria-label={panelOpen ? "Hide memory panels" : "Show memory panels"}
          variant="ghost"
          size="sm"
          onClick={onTogglePanel}
        >
          {panelOpen ? <LuPanelRightClose /> : <LuPanelRight />}
        </IconButton>
        <ColorModeButton />
      </Flex>
    </Flex>
  );
}
