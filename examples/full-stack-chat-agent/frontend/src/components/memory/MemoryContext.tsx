"use client";

import {
  Badge,
  Box,
  Flex,
  Heading,
  IconButton,
  Spinner,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  LuBrain,
  LuBuilding,
  LuHeart,
  LuMapPin,
  LuMessageSquare,
  LuRefreshCw,
  LuUser,
} from "react-icons/lu";
import { StatusAlert } from "@/components/ui/StatusAlert";
import { api } from "@/lib/api";
import type { MemoryContext as MemoryContextType } from "@/lib/types";

interface MemoryContextPanelProps {
  threadId: string | null;
  isVisible: boolean;
  /**
   * Incremented by the page when a chat turn completes. Including it in the
   * fetch effect's deps is what makes the panel show memory being written —
   * previously the effect keyed on `[threadId, isVisible]` only, so the panel
   * froze on whatever it loaded when the thread was opened.
   */
  memoryVersion?: number;
}

const entityTypeIcons: Record<string, React.ReactNode> = {
  PERSON: <LuUser size={12} />,
  ORGANIZATION: <LuBuilding size={12} />,
  LOCATION: <LuMapPin size={12} />,
};

function SectionHeading({
  icon,
  children,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Flex alignItems="center" gap="2">
      {icon}
      <Text fontSize="sm" fontWeight="medium">
        {children}
      </Text>
    </Flex>
  );
}

export function MemoryContextPanel({
  threadId,
  isVisible,
  memoryVersion = 0,
}: MemoryContextPanelProps) {
  const [context, setContext] = useState<MemoryContextType | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped by the manual refresh button; `memoryVersion` covers chat turns.
  const [manualVersion, setManualVersion] = useState(0);
  const [justUpdated, setJustUpdated] = useState(false);

  const refresh = useCallback(() => setManualVersion((v) => v + 1), []);

  useEffect(() => {
    if (!isVisible) return;
    let cancelled = false;

    const fetchContext = async () => {
      setIsLoading(true);
      try {
        const data = await api.memory.getContext(threadId || undefined);
        if (cancelled) return;
        setContext(data);
        setError(null);
        // Only pulse on a refresh, not on the initial load.
        if (memoryVersion + manualVersion > 0) {
          setJustUpdated(true);
          window.setTimeout(() => setJustUpdated(false), 1200);
        }
      } catch (err) {
        // An error branch, not a bare `catch {}`: a dead backend used to look
        // identical to an empty memory graph.
        if (cancelled) return;
        setError(
          err instanceof Error ? err.message : "Could not load memory context",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };

    fetchContext();

    return () => {
      cancelled = true;
    };
  }, [threadId, isVisible, memoryVersion, manualVersion]);

  if (!isVisible) return null;

  const isEmpty =
    context !== null &&
    context.preferences.length === 0 &&
    context.entities.length === 0 &&
    (context.recent_messages?.length ?? 0) === 0;

  return (
    <Box
      w="300px"
      flexShrink={0}
      borderLeftWidth="1px"
      borderColor="border.subtle"
      bg="bg.panel"
      p="4"
      overflowY="auto"
    >
      <Stack gap="6">
        <Flex alignItems="center" gap="2">
          <LuBrain size={20} />
          <Heading size="sm" flex="1">
            Memory context
          </Heading>
          {justUpdated && (
            <Badge colorPalette="green" size="sm" variant="subtle">
              updated
            </Badge>
          )}
          <IconButton
            aria-label="Refresh memory context"
            size="xs"
            variant="ghost"
            onClick={refresh}
            disabled={isLoading}
          >
            {isLoading ? <Spinner size="xs" /> : <LuRefreshCw />}
          </IconButton>
        </Flex>

        {error ? (
          <StatusAlert
            status="error"
            title="Memory context unavailable"
            description={error}
          />
        ) : isLoading && !context ? (
          <Text fontSize="sm" color="fg.muted">
            Loading...
          </Text>
        ) : !context ? (
          <Text fontSize="sm" color="fg.muted">
            No memory context available
          </Text>
        ) : (
          <>
            {context.preferences.length > 0 && (
              <Stack gap="2">
                <SectionHeading icon={<LuHeart size={14} />}>
                  Preferences
                </SectionHeading>
                <Stack gap="1">
                  {context.preferences.slice(0, 5).map((pref) => (
                    <Box
                      key={pref.id}
                      p="2"
                      bg="bg.muted"
                      borderRadius="md"
                      fontSize="xs"
                    >
                      <Badge size="sm" mb="1" colorPalette="brand">
                        {pref.category}
                      </Badge>
                      <Text>{pref.preference}</Text>
                    </Box>
                  ))}
                </Stack>
              </Stack>
            )}

            {context.entities.length > 0 && (
              <Stack gap="2">
                <SectionHeading>Known entities</SectionHeading>
                <Flex flexWrap="wrap" gap="1">
                  {context.entities.slice(0, 12).map((entity) => (
                    <Badge
                      key={entity.id}
                      size="sm"
                      variant="subtle"
                      display="flex"
                      alignItems="center"
                      gap="1"
                      title={entity.subtype ?? entity.type}
                    >
                      {entityTypeIcons[entity.type] || null}
                      {entity.name}
                    </Badge>
                  ))}
                </Flex>
              </Stack>
            )}

            {context.recent_messages && context.recent_messages.length > 0 && (
              <Stack gap="2">
                <SectionHeading icon={<LuMessageSquare size={14} />}>
                  Recent messages
                </SectionHeading>
                <Stack gap="1">
                  {context.recent_messages.slice(0, 5).map((msg) => (
                    <Box
                      key={msg.id}
                      p="2"
                      bg="bg.muted"
                      borderRadius="md"
                      fontSize="xs"
                    >
                      <Badge
                        size="sm"
                        mb="1"
                        colorPalette={msg.role === "user" ? "blue" : "green"}
                      >
                        {msg.role}
                      </Badge>
                      <Text>{msg.content}</Text>
                    </Box>
                  ))}
                </Stack>
              </Stack>
            )}

            {isEmpty && (
              <Text fontSize="sm" color="fg.muted" textAlign="center">
                No memories stored yet. Start chatting to build context!
              </Text>
            )}
          </>
        )}
      </Stack>
    </Box>
  );
}
