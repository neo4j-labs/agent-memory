"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Box,
  Card,
  Container,
  Flex,
  HStack,
  Heading,
  SegmentGroup,
  Tabs,
  Text,
} from "@chakra-ui/react";
import { ChatInterface } from "@/components/ChatInterface";
import { PreferencePanel } from "@/components/PreferencePanel";
import { MemoryExplorer } from "@/components/MemoryExplorer";
import { ColorModeButton } from "@/components/ui/color-mode";
import { DEMO_USERS, checkHealth, type DemoUserId } from "@/lib/api";

type AppTab = "chat" | "memory" | "preferences";

const USER_KEY = "shopping-user-id";
const SESSION_KEY_PREFIX = "shopping-session-id";

function newSessionId(userId: string): string {
  // crypto.randomUUID() is available in every browser Next 16 supports; the id
  // scopes a shopper's server-side memory, so it must not be guessable.
  return `${userId}-${crypto.randomUUID()}`;
}

export default function Home() {
  const [userId, setUserId] = useState<DemoUserId>(DEMO_USERS[0].id);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>("chat");
  const [isConnected, setIsConnected] = useState(false);
  const [isChecking, setIsChecking] = useState(true);

  // Storage and the network are only reachable after mount, so both the
  // remembered shopper and the health probe land here.
  useEffect(() => {
    const storedUser = localStorage.getItem(USER_KEY);
    if (storedUser && DEMO_USERS.some((u) => u.id === storedUser)) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration: sessionStorage is unreadable during render
      setUserId(storedUser as DemoUserId);
    }

    checkHealth()
      .then((health) => setIsConnected(health.status === "healthy"))
      .catch(() => setIsConnected(false))
      .finally(() => setIsChecking(false));
  }, []);

  // One session per shopper, remembered across reloads: switching shoppers
  // switches conversations instead of mixing two people's short-term memory.
  useEffect(() => {
    const key = `${SESSION_KEY_PREFIX}:${userId}`;
    const stored = sessionStorage.getItem(key);
    const resolved = stored ?? newSessionId(userId);
    if (!stored) sessionStorage.setItem(key, resolved);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration: sessionStorage is unreadable during render
    setSessionId(resolved);
  }, [userId]);

  const selectUser = (next: DemoUserId) => {
    localStorage.setItem(USER_KEY, next);
    setUserId(next);
  };

  return (
    <Box minH="100vh" bg="bg.subtle" color="fg" pb={14}>
      {/* Header */}
      <Box bg="bg.panel" borderBottomWidth="1px" borderColor="border.subtle" py={4}>
        <Container maxW="breakpoint-xl">
          <Flex justify="space-between" align="center" gap={4} flexWrap="wrap">
            <HStack gap={4}>
              <Heading size="lg" color="teal.fg">
                Smart Shopping Assistant
              </Heading>
              <Badge
                colorPalette={isConnected ? "green" : "red"}
                variant="subtle"
              >
                {isChecking
                  ? "Connecting..."
                  : isConnected
                    ? "Connected"
                    : "Disconnected"}
              </Badge>
            </HStack>

            <HStack gap={3}>
              <ShopperSwitcher value={userId} onChange={selectUser} />
              <ColorModeButton />
            </HStack>
          </Flex>
        </Container>
      </Box>

      {/* Main Content */}
      <Container maxW="breakpoint-xl" py={6}>
        {!isConnected && !isChecking && (
          <Card.Root mb={6} bg="bg.error" borderColor="border.error">
            <Card.Body>
              <Text color="fg.error">
                Unable to connect to the backend server. Make sure it is running
                at {process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}{" "}
                (see backend/README steps, then `uvicorn main:app --reload`).
              </Text>
            </Card.Body>
          </Card.Root>
        )}

        {/* One primitive for both levels of navigation: this root, and the
            Memory Explorer's own Tabs.Root inside the memory panel. */}
        <Tabs.Root
          value={activeTab}
          onValueChange={(e) => setActiveTab(e.value as AppTab)}
          variant="enclosed"
          colorPalette="teal"
          lazyMount
        >
          <Tabs.List mb={6}>
            <Tabs.Trigger value="chat">Chat</Tabs.Trigger>
            <Tabs.Trigger value="memory">Memory Graph</Tabs.Trigger>
            <Tabs.Trigger value="preferences">Preferences</Tabs.Trigger>
          </Tabs.List>

          <Tabs.Content value="chat">
            {sessionId && (
              <ChatInterface
                key={sessionId}
                sessionId={sessionId}
                userId={userId}
              />
            )}
          </Tabs.Content>

          <Tabs.Content value="memory">
            {sessionId && <MemoryExplorer sessionId={sessionId} />}
          </Tabs.Content>

          <Tabs.Content value="preferences">
            {sessionId && (
              <PreferencePanel sessionId={sessionId} userId={userId} />
            )}
          </Tabs.Content>
        </Tabs.Root>
      </Container>

      {/* Footer */}
      <Box
        position="fixed"
        bottom={0}
        left={0}
        right={0}
        bg="bg.panel"
        borderTopWidth="1px"
        borderColor="border.subtle"
        py={2}
      >
        <Container maxW="breakpoint-xl">
          <Flex justify="space-between" align="center" gap={2}>
            <Text fontSize="sm" color="fg.muted">
              Powered by Neo4j Agent Memory + Microsoft Agent Framework
            </Text>
            <Text fontSize="sm" color="fg.muted" truncate>
              {userId} · {sessionId ?? "resolving session..."}
            </Text>
          </Flex>
        </Container>
      </Box>
    </Box>
  );
}

/**
 * Demo shopper picker.
 *
 * The chosen id travels with every chat turn as `user_id`, where the backend
 * upserts a `(:User {identifier})` node and scopes the session to it.
 */
function ShopperSwitcher({
  value,
  onChange,
}: {
  value: DemoUserId;
  onChange: (next: DemoUserId) => void;
}) {
  return (
    <HStack gap={2}>
      <Text fontSize="sm" color="fg.muted">
        Shopper
      </Text>
      <SegmentGroup.Root
        value={value}
        onValueChange={(e) => e.value && onChange(e.value as DemoUserId)}
        size="sm"
        colorPalette="teal"
      >
        <SegmentGroup.Indicator />
        <SegmentGroup.Items
          items={DEMO_USERS.map((user) => ({
            value: user.id,
            label: user.label,
          }))}
        />
      </SegmentGroup.Root>
    </HStack>
  );
}
